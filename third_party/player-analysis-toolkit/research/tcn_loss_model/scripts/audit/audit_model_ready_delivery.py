#!/usr/bin/env python3
"""Audit a frozen OQ model-ready delivery without changing its contents."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import torch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_contract import validate_model_ready_npz  # noqa: E402


HINT1_FIELDS = ("hint1_move", "hint1_score", "hint1_nodes", "hint1_depth", "hint1_is_book")
HINT6_SUFFIXES = ("move", "score", "nodes", "depth", "is_book")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_hash(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def audit_delivery_files(delivery: Path, errors: list[str]) -> dict[str, Any]:
    manifest_path = delivery / "DELIVERY_MANIFEST.json"
    manifest = read_json(manifest_path)
    checked = 0
    for item in manifest["files"]:
        relative = Path(item["path"])
        path = delivery / relative
        if not path.is_file():
            errors.append(f"delivery file missing: {relative.as_posix()}")
            continue
        checked += 1
        if path.stat().st_size != int(item["bytes"]):
            errors.append(f"delivery file size mismatch: {relative.as_posix()}")
        if sha256_file(path) != item["sha256"]:
            errors.append(f"delivery file sha256 mismatch: {relative.as_posix()}")
    return {"schema": manifest.get("schema"), "records": len(manifest["files"]), "checked": checked}


def load_games(
    path: Path,
    errors: list[str],
    excluded_players: set[str],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    games: dict[str, dict[str, str]] = {}
    excluded_game_ids: list[str] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            game_id = row["game_id"].strip()
            if not game_id:
                errors.append("blank game_id in games.csv")
                continue
            if game_id in games:
                errors.append(f"duplicate game_id in games.csv: {game_id}")
                continue
            games[game_id] = row
            identity = " ".join(
                row.get(field, "").strip().lower()
                for field in ("black_id", "black_name", "white_id", "white_name")
            )
            if excluded_players.intersection(identity):
                excluded_game_ids.append(game_id)
    return games, excluded_game_ids


def audit_raw_hints(
    path: Path,
    games: dict[str, dict[str, str]],
    expected_hint1_level: int,
    errors: list[str],
) -> dict[str, Any]:
    raw_games: set[str] = set()
    splits_by_game: dict[str, set[str]] = defaultdict(set)
    players_by_game: dict[str, set[str]] = defaultdict(set)
    rows_by_game: Counter[str] = Counter()
    placement_rows = pass_rows = 0
    missing_hint1_rows = missing_hint6_rows = 0
    illegal_hint1_rows = illegal_hint6_rows = 0
    illegal_hint6_candidates: Counter[int] = Counter()
    illegal_hint6_games: set[str] = set()
    illegal_hint6_rank1_games: set[str] = set()
    illegal_hint6_samples: list[dict[str, Any]] = []
    missing_hint1_games: set[str] = set()
    missing_hint6_games: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            game_id = row["game_id"].strip()
            raw_games.add(game_id)
            rows_by_game[game_id] += 1
            splits_by_game[game_id].add(row["split"].strip())
            actual_move = row["actual_move"].strip()
            if actual_move == "-":
                pass_rows += 1
                continue
            placement_rows += 1
            players_by_game[game_id].add(row["player_id"].strip().lower())
            legal_moves = set(row["legal_moves"].strip().lower().split())
            hint1_missing = row["hint1_level"].strip() != str(expected_hint1_level) or any(
                not row[field].strip() for field in HINT1_FIELDS
            )
            if hint1_missing:
                missing_hint1_rows += 1
                missing_hint1_games.add(game_id)
            if row["hint1_move"].strip().lower() not in legal_moves:
                illegal_hint1_rows += 1
            try:
                required_ranks = min(6, int(row["n_legal_moves"]))
            except ValueError:
                required_ranks = 6
                errors.append(f"invalid n_legal_moves: {(game_id, row['move_index'])}")
            hint6_missing = any(
                not row[f"hint6_{rank}_{suffix}"].strip()
                for rank in range(1, required_ranks + 1)
                for suffix in HINT6_SUFFIXES
            )
            if hint6_missing:
                missing_hint6_rows += 1
                missing_hint6_games.add(game_id)
            row_has_illegal_hint6 = False
            for rank in range(1, 7):
                hint_move = row[f"hint6_{rank}_move"].strip().lower()
                if hint_move and hint_move not in legal_moves:
                    illegal_hint6_candidates[rank] += 1
                    illegal_hint6_games.add(game_id)
                    if rank == 1:
                        illegal_hint6_rank1_games.add(game_id)
                    row_has_illegal_hint6 = True
            if row_has_illegal_hint6:
                illegal_hint6_rows += 1
                if len(illegal_hint6_samples) < 10:
                    illegal_hint6_samples.append({
                        "gameId": game_id,
                        "moveIndex": int(row["move_index"]),
                        "actualMove": actual_move,
                        "legalMoves": sorted(legal_moves),
                        "hint1Move": row["hint1_move"].strip().lower(),
                        "hint6Moves": [
                            row[f"hint6_{rank}_move"].strip().lower() for rank in range(1, 7)
                        ],
                    })

    if raw_games != set(games):
        errors.append("raw-node game IDs differ from games.csv")
    leaked = sorted(game_id for game_id, values in splits_by_game.items() if len(values) != 1)
    if leaked:
        errors.append(f"raw-node split leakage: {len(leaked)} games")
    side_mismatches: list[str] = []
    for game_id, game in games.items():
        expected_players = {game["black_id"].strip().lower(), game["white_id"].strip().lower()}
        if players_by_game[game_id] != expected_players:
            side_mismatches.append(game_id)
    if side_mismatches:
        errors.append(f"games without complete bilateral player coverage: {len(side_mismatches)}")
    if missing_hint1_rows:
        errors.append(f"placement rows missing complete hint1 level {expected_hint1_level}: {missing_hint1_rows}")
    if missing_hint6_rows:
        errors.append(f"placement rows missing required hint6 ranks: {missing_hint6_rows}")
    if illegal_hint1_rows:
        errors.append(f"placement rows with illegal hint1 move: {illegal_hint1_rows}")
    if illegal_hint6_rows:
        errors.append(f"placement rows with an illegal hint6 move: {illegal_hint6_rows}")
    return {
        "games": len(raw_games),
        "rows": sum(rows_by_game.values()),
        "placementRows": placement_rows,
        "passRows": pass_rows,
        "hint1Level": expected_hint1_level,
        "missingHint1Rows": missing_hint1_rows,
        "missingHint1Games": len(missing_hint1_games),
        "missingHint6Rows": missing_hint6_rows,
        "missingHint6Games": len(missing_hint6_games),
        "illegalHint1Rows": illegal_hint1_rows,
        "illegalHint6Rows": illegal_hint6_rows,
        "illegalHint6Games": len(illegal_hint6_games),
        "illegalHint6Rank1Games": len(illegal_hint6_rank1_games),
        "illegalHint6CandidatesByRank": {
            str(rank): illegal_hint6_candidates[rank] for rank in range(1, 7)
        },
        "illegalHint6Samples": illegal_hint6_samples,
        "bilateralPlayerCoverageErrors": len(side_mismatches),
        "splitLeakageGames": len(leaked),
    }


def audit_split_manifest(path: Path, expected_games: set[str], errors: list[str]) -> dict[str, Any]:
    ids: set[str] = set()
    counts: Counter[str] = Counter()
    duplicates = 0
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            game_id = row["game_id"].strip()
            if game_id in ids:
                duplicates += 1
            ids.add(game_id)
            counts[row["split"].strip()] += 1
    if duplicates:
        errors.append(f"duplicate split-manifest game IDs: {duplicates}")
    if ids != expected_games:
        errors.append("split-manifest game IDs differ from games.csv")
    return {"games": len(ids), "duplicates": duplicates, "counts": dict(counts)}


def audit_assets(
    delivery: Path,
    reference_manifest_path: Path,
    checkpoint_path: Path,
    preprocessing_path: Path,
    human_book_path: Path,
    errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    delivery_manifest = read_json(delivery / "model_ready" / "materialization_manifest.json")
    reference_manifest = read_json(reference_manifest_path)
    delivery_inputs = delivery_manifest["inputs"]
    reference_inputs = reference_manifest["inputs"]
    assets = {
        "baseCheckpoint": (checkpoint_path, "baseCheckpoint"),
        "preprocessing": (preprocessing_path, "preprocessing"),
        "humanOpeningBook": (human_book_path, "humanOpeningBook"),
    }
    result: dict[str, Any] = {}
    for label, (path, key) in assets.items():
        actual = sha256_file(path)
        delivered = delivery_inputs[key]["sha256"]
        reference = reference_inputs[key]["sha256"]
        matches = actual == delivered == reference
        result[label] = {"sha256": actual, "matchesDeliveryAndFormalReference": matches}
        if not matches:
            errors.append(f"{label} differs from delivery or formal 3,531-game reference")
    scripts_match = delivery_inputs["officialSourceScripts"] == reference_inputs["officialSourceScripts"]
    result["officialSourceScriptsMatch"] = scripts_match
    if not scripts_match:
        errors.append("official source-script hashes differ from formal 3,531-game reference")

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    preprocessing = read_json(preprocessing_path)
    if checkpoint["preprocessing"] != preprocessing:
        errors.append("external preprocessing.json differs from official checkpoint payload")
    checkpoint_preprocessing_hash = canonical_json_hash(checkpoint["preprocessing"])
    return result, {
        "checkpoint": checkpoint,
        "preprocessingHash": checkpoint_preprocessing_hash,
    }


def audit_npz(
    path: Path,
    expected_games: set[str],
    checkpoint_info: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    checkpoint = checkpoint_info["checkpoint"]
    report = validate_model_ready_npz(
        path,
        expected_input_features=checkpoint["input_features"],
        expected_board_channels=checkpoint["board_encoding"]["cnn_channels"],
        expected_preprocessing_sha256=checkpoint_info["preprocessingHash"],
    )
    with np.load(path, allow_pickle=False) as data:
        game_ids = set(data["game_id"].astype(str).tolist())
        mask = data["mask"].astype(bool)
        label_available = data["label_available"].astype(bool)
        current_hidden = bool(np.all(data["board_move_tokens"][:, :, 0] == 0))
        if game_ids != expected_games:
            errors.append("model-ready game IDs differ from games.csv")
        if not np.array_equal(mask, label_available):
            errors.append("model-ready mask differs from label_available")
        if not current_hidden:
            errors.append("current move is not fully hidden in board_move_tokens")
        report.update({
            "inputFeaturesExactCheckpointOrder": True,
            "boardChannelsExactCheckpointOrder": True,
            "gameIdsMatchGamesCsv": game_ids == expected_games,
            "labelMaskMatches": bool(np.array_equal(mask, label_available)),
            "currentMoveHidden": current_hidden,
            "boardChannels": int(len(data["board_cnn_channels"])),
        })
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery", required=True, type=Path)
    parser.add_argument("--reference-materialization-manifest", required=True, type=Path)
    parser.add_argument("--base-checkpoint", required=True, type=Path)
    parser.add_argument("--preprocessing", required=True, type=Path)
    parser.add_argument("--human-opening-book", required=True, type=Path)
    parser.add_argument("--expected-hint1-level", type=int, default=2)
    parser.add_argument(
        "--exclude-player",
        action="append",
        default=[],
        help="audit whole-game exclusion for this player ID; may be repeated",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    delivery = args.delivery.resolve()
    errors: list[str] = []
    delivery_files = audit_delivery_files(delivery, errors)
    excluded_players = {
        str(player).strip().lower() for player in args.exclude_player if str(player).strip()
    }
    games, excluded_game_ids = load_games(
        delivery / "handoff" / "games.csv", errors, excluded_players
    )
    raw_hints = audit_raw_hints(
        delivery / "handoff" / "raw_nodes_with_pass.csv",
        games,
        args.expected_hint1_level,
        errors,
    )
    split_manifest = audit_split_manifest(
        delivery / "handoff" / "split_manifest.csv", set(games), errors
    )
    assets, checkpoint_info = audit_assets(
        delivery,
        args.reference_materialization_manifest.resolve(),
        args.base_checkpoint.resolve(),
        args.preprocessing.resolve(),
        args.human_opening_book.resolve(),
        errors,
    )
    try:
        model_ready = audit_npz(
            delivery / "model_ready" / "model_ready_10000.npz",
            set(games),
            checkpoint_info,
            errors,
        )
    except Exception as exc:
        errors.append(f"model-ready validation failed: {exc}")
        model_ready = {"ok": False, "error": str(exc)}

    report = {
        "schema": "tcn-loss-model-ready-delivery-audit-v1",
        "ok": not errors,
        "delivery": str(delivery),
        "deliveryFiles": delivery_files,
        "games": len(games),
        "rawHints": raw_hints,
        "splitManifest": split_manifest,
        "formalAssetIdentity": assets,
        "modelReady": model_ready,
        "excludedPlayers": {
            "playerIds": sorted(excluded_players),
            "gamesFound": len(excluded_game_ids),
            "gameIds": excluded_game_ids,
            "wholeGameExclusionRequiredBeforeTraining": bool(excluded_game_ids),
        },
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
