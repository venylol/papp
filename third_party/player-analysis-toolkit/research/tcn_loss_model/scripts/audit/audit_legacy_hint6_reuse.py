#!/usr/bin/env python3
"""Build an exact-index, board-checked, legality-screened legacy hint6 seed."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.egaroucid_safe import legal_moves_from_setboard, normalize_setboard  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def legacy_arg(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected LABEL=CSV_PATH")
    label, path = value.split("=", 1)
    return label.strip(), Path(path)


def load_frozen(path: Path) -> tuple[dict[tuple[str, int], dict[str, Any]], set[str], dict[str, int]]:
    nodes: dict[tuple[str, int], dict[str, Any]] = {}
    games: set[str] = set()
    shape = Counter()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            shape["rows"] += 1
            game_id = row["game_id"]
            games.add(game_id)
            shape["maxSourcePlyIncludingPass"] = max(
                shape["maxSourcePlyIncludingPass"], int(row["source_ply_including_pass"])
            )
            shape["maxGlobalPlacementPly"] = max(
                shape["maxGlobalPlacementPly"], int(row["global_placement_ply"])
            )
            if row["actual_move"] == "-" or row.get("is_pass_record") == "1":
                shape["passes"] += 1
                continue
            shape["placements"] += 1
            key = (game_id, int(row["move_index"]))
            board = normalize_setboard(row["board_setboard"])
            legal = row["legal_moves"].lower().split()
            derived = legal_moves_from_setboard(board)
            if set(legal) != set(derived) or len(legal) != len(derived):
                raise ValueError(f"bad frozen legal moves: {key}")
            if key in nodes:
                raise ValueError(f"duplicate frozen key: {key}")
            nodes[key] = {
                "board_setboard": board,
                "side_to_move": row["side_to_move"].lower(),
                "actual_move": row["actual_move"].lower(),
                "legal_moves": legal,
                "source_ply_including_pass": int(row["source_ply_including_pass"]),
                "global_placement_ply": int(row["global_placement_ply"]),
            }
    shape["games"] = len(games)
    return nodes, games, dict(shape)


def parse_hints(row: dict[str, str]) -> list[dict[str, str]]:
    result = []
    for rank in range(1, 7):
        move = row.get(f"hint6_{rank}_move", "").strip().lower()
        if not move:
            continue
        result.append({
            "move": move,
            "score": row.get(f"hint6_{rank}_score", "").strip(),
            "nodes": row.get(f"hint6_{rank}_nodes", "").strip(),
            "depth": row.get(f"hint6_{rank}_depth", "").strip(),
            "is_book": row.get(f"hint6_{rank}_is_book", "").strip(),
        })
    return result


def hint_errors(hints: list[dict[str, str]], legal: list[str]) -> list[str]:
    errors = []
    moves = [item["move"] for item in hints]
    if len(hints) != min(6, len(legal)):
        errors.append("candidate-count")
    if len(moves) != len(set(moves)):
        errors.append("duplicate-candidate")
    if not set(moves).issubset(set(legal)):
        errors.append("illegal-candidate")
    if any(not item[name] for item in hints for name in ("score", "nodes", "depth", "is_book")):
        errors.append("incomplete-candidate-fields")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-source", required=True, type=Path)
    parser.add_argument("--legacy", required=True, action="append", type=legacy_arg)
    parser.add_argument("--output-audit", required=True, type=Path)
    parser.add_argument("--output-seed", type=Path)
    args = parser.parse_args()

    frozen_path = args.frozen_source.resolve()
    frozen, frozen_games, frozen_shape = load_frozen(frozen_path)
    selected: dict[tuple[str, int], dict[str, Any]] = {}
    all_legacy_games: set[str] = set()
    reports = []
    for priority, (label, supplied_path) in enumerate(args.legacy, start=1):
        path = supplied_path.resolve()
        file_hash = sha256_file(path)
        counts: Counter[str] = Counter()
        games: set[str] = set()
        examples = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for line_number, row in enumerate(csv.DictReader(handle), start=2):
                counts["rows"] += 1
                game_id = row["game_id"]
                games.add(game_id)
                all_legacy_games.add(game_id)
                actual_move = row.get("actual_move", "").strip().lower()
                if actual_move == "-" or row.get("is_pass_record", "") == "1":
                    counts["passRowsExcluded"] += 1
                    continue
                key = (game_id, int(row["move_index"]))
                target = frozen.get(key)
                if target is None:
                    counts["noExactFrozenKey"] += 1
                    continue
                counts["exactFrozenKey"] += 1
                board_value = row.get("board_setboard", "").strip() or row.get("board", "").strip()
                errors = []
                try:
                    board = normalize_setboard(board_value)
                except Exception:
                    board = ""
                    errors.append("invalid-board")
                if board and board != target["board_setboard"]:
                    errors.append("board-mismatch")
                side = row.get("side_to_move", "").strip().lower()
                if side and side != target["side_to_move"]:
                    errors.append("side-mismatch")
                if actual_move and actual_move != target["actual_move"]:
                    errors.append("actual-move-mismatch")
                hints = parse_hints(row)
                errors.extend(hint_errors(hints, target["legal_moves"]))
                if errors:
                    counts["rejectedAfterExactKey"] += 1
                    for error in sorted(set(errors)):
                        counts[f"rejected:{error}"] += 1
                    if len(examples) < 20:
                        examples.append({
                            "game_id": game_id,
                            "move_index": key[1],
                            "errors": sorted(set(errors)),
                            "legalMoves": target["legal_moves"],
                            "hintMoves": [item["move"] for item in hints],
                        })
                    continue
                counts["validAfterExactKey"] += 1
                if key in selected:
                    counts["validShadowedByHigherPriority"] += 1
                    continue
                selected[key] = {
                    "schema": "oq-legacy-hint6-exact-key-seed-v1",
                    "game_id": game_id,
                    "move_index": key[1],
                    **target,
                    "hints": hints,
                    "provenanceTier": "legacy-exact-key-board-and-legality-screened",
                    "legacySourceLabel": label,
                    "legacySourceSha256": file_hash,
                    "legacySourceLine": line_number,
                }
                counts["selectedByPriority"] += 1
        reports.append({
            "label": label,
            "priority": priority,
            "path": str(path),
            "sha256": file_hash,
            "games": len(games),
            "gamesInFrozen": len(games & frozen_games),
            "gamesOutsideFrozen": len(games - frozen_games),
            "counts": dict(sorted(counts.items())),
            "rejectedExamples": examples,
        })

    seed_info = None
    if args.output_seed:
        seed_path = args.output_seed.resolve()
        if seed_path.exists():
            raise FileExistsError(f"refusing to overwrite: {seed_path}")
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = seed_path.with_name(f"{seed_path.name}.tmp.{os.getpid()}.{time.time_ns()}")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            for key in sorted(selected):
                handle.write(json.dumps(selected[key], ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, seed_path)
        seed_info = {"path": str(seed_path), "rows": len(selected), "sha256": sha256_file(seed_path)}

    report = {
        "schema": "oq-legacy-hint6-exact-key-reuse-audit-v1",
        "ok": True,
        "matchingOrder": [
            "exact (game_id, move_index)",
            "board_setboard + side_to_move + actual_move identity",
            "complete unique legal hint6 candidates",
        ],
        "boardOnlyRemappingUsed": False,
        "frozenSource": str(frozen_path),
        "frozenSourceSha256": sha256_file(frozen_path),
        "frozenShape": frozen_shape,
        "legacyCoverage": {
            "allLegacyGames": len(all_legacy_games),
            "legacyGamesInFrozen": len(all_legacy_games & frozen_games),
            "frozenGamesAbsentFromAllLegacy": len(frozen_games - all_legacy_games),
            "legacyGamesOutsideFrozen": len(all_legacy_games - frozen_games),
        },
        "selectedUniqueRows": len(selected),
        "sources": reports,
        "seed": seed_info,
    }
    atomic_json(args.output_audit.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
