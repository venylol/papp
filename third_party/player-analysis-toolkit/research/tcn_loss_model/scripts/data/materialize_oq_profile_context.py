#!/usr/bin/env python3
"""Add the fixed 31-feature OQ Player context branch to a new model-ready NPZ."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.oq_player_profile import normalize_account
from src.data_contract import validate_model_ready_npz
from src.offbook import OFFBOOK_ARRAYS, validate_offbook_arrays
from src.oq_profile_features import (
    OQ_PROFILE_FEATURE_NAMES,
    OQ_PROFILE_SCHEMA,
    RETROSPECTIVE_TRUSTED_POLICY,
    STRICT_PRE_GAME_POLICY,
    build_profile_feature_vector,
    canonical_json_hash,
    fit_train_only_normalization,
    load_profile_snapshots,
    parse_utc,
    select_snapshot,
    sha256_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-npz", type=Path, required=True)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--snapshots-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-name", default="model_ready_10000_oq_profile_retrospective.npz")
    parser.add_argument(
        "--policy",
        choices=(STRICT_PRE_GAME_POLICY, RETROSPECTIVE_TRUSTED_POLICY),
        default=STRICT_PRE_GAME_POLICY,
    )
    parser.add_argument("--allow-temporal-leakage", action="store_true")
    parser.add_argument(
        "--normalization-reference-npz", type=Path,
        help="reuse the fixed 31-feature mean/std from an already validated Profile NPZ",
    )
    args = parser.parse_args()
    if args.policy == RETROSPECTIVE_TRUSTED_POLICY and not args.allow_temporal_leakage:
        parser.error("retrospective policy requires explicit --allow-temporal-leakage")
    if Path(args.output_name).name != args.output_name or not args.output_name.endswith(".npz"):
        parser.error("--output-name must be a plain .npz file name")
    return args


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot_set_hash(snapshot_files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(snapshot_files, key=lambda item: str(item).casefold()):
        digest.update(str(path.resolve()).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def align_game_metadata(games_by_id: pd.DataFrame, game_ids: np.ndarray) -> tuple[pd.DataFrame, list[str]]:
    """Only zero-move metadata may be absent from a sequence dataset."""
    model_ids = set(game_ids)
    metadata_ids = set(games_by_id.index)
    extra_ids = metadata_ids - model_ids
    zero_move_ids = set()
    if "length" in games_by_id.columns:
        lengths = pd.to_numeric(games_by_id["length"], errors="raise")
        zero_move_ids = set(games_by_id.index[lengths.eq(0)])
    if model_ids - metadata_ids or extra_ids - zero_move_ids:
        raise ValueError(
            "NPZ game membership differs from authoritative game metadata: "
            f"missing_metadata={sorted(model_ids - metadata_ids)} "
            f"missing_model_games={sorted(extra_ids - zero_move_ids)}"
        )
    return games_by_id.loc[list(game_ids)].copy(), sorted(extra_ids)


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    for path in (args.input_npz, args.games, args.snapshots_dir):
        if not path.exists():
            raise FileNotFoundError(path)
    output_dir.mkdir(parents=True)
    output_npz = output_dir / args.output_name
    manifest_path = output_dir / "oq_profile_materialization_manifest.json"
    coverage_path = output_dir / "oq_profile_coverage_report.json"

    normalized_snapshot_root = (
        args.snapshots_dir / "normalized"
        if (args.snapshots_dir / "normalized").is_dir()
        else args.snapshots_dir
    )
    snapshot_files = sorted(normalized_snapshot_root.rglob("*.json"))
    snapshots_by_account = load_profile_snapshots(snapshot_files)
    if not snapshots_by_account:
        raise ValueError("no normalized OQ Player profile snapshots found")
    games = pd.read_csv(args.games, encoding="utf-8", low_memory=False, dtype={"game_id": str})
    required = {"game_id", "created", "black_id", "white_id"}
    missing_columns = sorted(required - set(games.columns))
    if missing_columns:
        raise ValueError(f"game metadata missing columns: {missing_columns}")
    if games.duplicated("game_id").any():
        raise ValueError("game metadata contains duplicate game_id")
    games["game_id"] = games["game_id"].astype(str)
    games["black_id_normalized"] = games["black_id"].map(normalize_account)
    games["white_id_normalized"] = games["white_id"].map(normalize_account)
    if games[["black_id_normalized", "white_id_normalized"]].eq("").any().any():
        raise ValueError("game metadata contains blank player account")
    if games["black_id_normalized"].eq(games["white_id_normalized"]).any():
        raise ValueError("game metadata contains identical black and white account")
    games_by_id = games.set_index("game_id")

    with np.load(args.input_npz, allow_pickle=False) as source:
        original = {name: source[name].copy() for name in source.files}
    offbook_names = sorted(set(original) & set(OFFBOOK_ARRAYS))
    offbook_present = bool(offbook_names)
    if offbook_present and set(offbook_names) != set(OFFBOOK_ARRAYS):
        missing_offbook = sorted(set(OFFBOOK_ARRAYS) - set(offbook_names))
        raise ValueError(f"input NPZ contains an incomplete Level18 offbook contract: missing {missing_offbook}")
    input_offbook_validation = validate_offbook_arrays(original) if offbook_present else None
    game_ids = original["game_id"].astype(str)
    games_by_id, zero_move_game_ids = align_game_metadata(games_by_id, game_ids)
    shape = original["X"].shape[:2]
    node_valid = original["global_placement_ply"] > 0
    raw_features = np.zeros((*shape, 31), dtype=np.float32)
    missing = np.ones((*shape, 31), dtype=bool)
    player_snapshot_time = np.full(shape, "", dtype="U32")
    opponent_snapshot_time = np.full(shape, "", dtype="U32")
    player_snapshot_hash = np.full(shape, "", dtype="U64")
    opponent_snapshot_hash = np.full(shape, "", dtype="U64")
    temporal_violations = 0
    games_with_both_profiles = 0
    missing_accounts: set[str] = set()
    games_without_both_profiles = 0
    accounts_without_pre_game_snapshot: set[str] = set()
    games_without_both_pre_game_snapshots = 0

    for game_index, game_id in enumerate(game_ids):
        game = games_by_id.loc[game_id]
        created = parse_utc(game["created"])
        black_id = game["black_id_normalized"]
        white_id = game["white_id_normalized"]
        black_snapshot = select_snapshot(snapshots_by_account.get(black_id, []), created, args.policy)
        white_snapshot = select_snapshot(snapshots_by_account.get(white_id, []), created, args.policy)
        black_pre_game = select_snapshot(snapshots_by_account.get(black_id, []), created, STRICT_PRE_GAME_POLICY)
        white_pre_game = select_snapshot(snapshots_by_account.get(white_id, []), created, STRICT_PRE_GAME_POLICY)
        if black_pre_game is None:
            accounts_without_pre_game_snapshot.add(black_id)
        if white_pre_game is None:
            accounts_without_pre_game_snapshot.add(white_id)
        if black_pre_game is None or white_pre_game is None:
            games_without_both_pre_game_snapshots += 1
        if black_snapshot is None:
            missing_accounts.add(black_id)
        if white_snapshot is None:
            missing_accounts.add(white_id)
        if black_snapshot is not None and white_snapshot is not None:
            games_with_both_profiles += 1
        else:
            games_without_both_profiles += 1
        for snapshot in (black_snapshot, white_snapshot):
            if snapshot is not None and snapshot.fetched_at > created:
                temporal_violations += 1
        side_vectors = {}
        for side, player_id, opponent_id, player_snapshot, opponent_snapshot in (
            ("black", black_id, white_id, black_snapshot, white_snapshot),
            ("white", white_id, black_id, white_snapshot, black_snapshot),
        ):
            side_vectors[side] = build_profile_feature_vector(
                player_snapshot.document if player_snapshot else None,
                opponent_snapshot.document if opponent_snapshot else None,
                player_color=side,
                opponent_color="white" if side == "black" else "black",
            )
        for step in np.flatnonzero(node_valid[game_index]):
            side = str(original["side_to_move"][game_index, step]).casefold()
            if side not in {"black", "white"}:
                raise ValueError(f"invalid side_to_move for {game_id}/{step}: {side!r}")
            expected_player = black_id if side == "black" else white_id
            actual_player = normalize_account(original["player_id"][game_index, step])
            if actual_player != expected_player:
                raise ValueError(
                    f"node player/color mismatch for {game_id}/{step}: {actual_player!r} != {expected_player!r}"
                )
            values, mask = side_vectors[side]
            raw_features[game_index, step] = values
            missing[game_index, step] = mask
            player_snapshot = black_snapshot if side == "black" else white_snapshot
            opponent_snapshot = white_snapshot if side == "black" else black_snapshot
            if player_snapshot:
                player_snapshot_time[game_index, step] = player_snapshot.document["profile_fetched_at_utc"]
                player_snapshot_hash[game_index, step] = player_snapshot.document["raw_response_sha256"]
            if opponent_snapshot:
                opponent_snapshot_time[game_index, step] = opponent_snapshot.document["profile_fetched_at_utc"]
                opponent_snapshot_hash[game_index, step] = opponent_snapshot.document["raw_response_sha256"]

    normalization_source = "fit on this dataset train split"
    normalization_reference_npz = getattr(args, "normalization_reference_npz", None)
    if normalization_reference_npz:
        reference_validation = validate_model_ready_npz(normalization_reference_npz, require_oq_profile=True)
        with np.load(normalization_reference_npz, allow_pickle=False) as reference:
            means = reference["oq_profile_preprocessing_mean"].astype(np.float32)
            stds = reference["oq_profile_preprocessing_std"].astype(np.float32)
            preprocessing_hash = str(reference["oq_profile_preprocessing_sha256"].item())
        normalized = (raw_features - means.reshape(1, 1, -1)) / stds.reshape(1, 1, -1)
        normalized = np.where(missing | ~node_valid[..., None], 0.0, normalized).astype(np.float32)
        if not np.isfinite(normalized).all():
            raise ValueError("normalized OQ profile features contain NaN or Inf")
        normalization_source = str(normalization_reference_npz.resolve())
    else:
        reference_validation = None
        normalized, means, stds, preprocessing_hash = fit_train_only_normalization(
            raw_features, missing, node_valid, original["split"].astype(str),
        )
    if np.any(raw_features[~node_valid] != 0) or np.any(normalized[~node_valid] != 0):
        raise AssertionError("padded nodes carry nonzero OQ profile features")
    if not np.all(missing[~node_valid]):
        raise AssertionError("padded nodes must have all OQ profile fields missing")

    additions = {
        "oq_profile_raw_features": raw_features,
        "oq_profile_features": normalized,
        "oq_profile_missing": missing,
        "oq_profile_feature_names": np.asarray(OQ_PROFILE_FEATURE_NAMES, dtype="U64"),
        "oq_profile_node_valid": node_valid,
        "oq_profile_player_snapshot_time_utc": player_snapshot_time,
        "oq_profile_opponent_snapshot_time_utc": opponent_snapshot_time,
        "oq_profile_player_raw_response_sha256": player_snapshot_hash,
        "oq_profile_opponent_raw_response_sha256": opponent_snapshot_hash,
        "oq_profile_policy": np.asarray(args.policy),
        "oq_profile_temporal_leakage_authorized": np.asarray(
            args.policy == RETROSPECTIVE_TRUSTED_POLICY
        ),
        "oq_profile_schema": np.asarray(OQ_PROFILE_SCHEMA),
        "oq_profile_game_created_utc": np.asarray(
            [str(games_by_id.loc[game_id, "created"]) for game_id in game_ids], dtype="U32"
        ),
        "oq_profile_preprocessing_mean": means,
        "oq_profile_preprocessing_std": stds,
        "oq_profile_preprocessing_sha256": np.asarray(preprocessing_hash),
    }
    collision = sorted(set(original) & set(additions))
    if collision:
        raise ValueError(f"input NPZ already contains OQ profile arrays: {collision}")
    temporary = output_npz.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, **original, **additions)
    temporary.replace(output_npz)
    with np.load(output_npz, allow_pickle=False) as written:
        for name, value in original.items():
            same = (
                np.array_equal(written[name], value, equal_nan=True)
                if value.dtype.kind in {"f", "c"}
                else np.array_equal(written[name], value)
            )
            if not same:
                raise AssertionError(f"original NPZ array changed: {name}")
    validation = validate_model_ready_npz(
        output_npz,
        require_oq_profile=True,
        expected_oq_profile_feature_names=OQ_PROFILE_FEATURE_NAMES,
        expected_oq_profile_preprocessing_sha256=preprocessing_hash,
        expected_oq_profile_policy=args.policy,
        require_offbook=offbook_present,
        expected_offbook_source_data_sha256=(
            input_offbook_validation["sourceDataSha256"] if offbook_present else None
        ),
        expected_offbook_source_checkpoint_sha256=(
            input_offbook_validation["sourceCheckpointSha256"] if offbook_present else None
        ),
        expected_offbook_records_sha256=(input_offbook_validation["recordsSha256"] if offbook_present else None),
        expected_offbook_materialization_sha256=(
            input_offbook_validation["materializationSha256"] if offbook_present else None
        ),
    )

    valid_count = int(node_valid.sum())
    current_overall_complete = node_valid & ~missing[..., :5].any(axis=-1)
    opponent_overall_complete = node_valid & ~missing[..., 9:14].any(axis=-1)
    current_color_complete = node_valid & ~missing[..., 5:9].any(axis=-1)
    opponent_color_complete = node_valid & ~missing[..., 14:18].any(axis=-1)
    current_full9_complete = current_overall_complete & current_color_complete
    opponent_full9_complete = opponent_overall_complete & opponent_color_complete
    strong_complete = node_valid & ~missing[..., 23:27].any(axis=-1)
    weak_complete = node_valid & ~missing[..., 27:31].any(axis=-1)
    split_players = {}
    for split in ("train", "validation", "test"):
        ids = game_ids[original["split"].astype(str) == split]
        rows = games_by_id.loc[ids]
        split_players[split] = set(rows["black_id_normalized"]) | set(rows["white_id_normalized"])
    all_players = set(games["black_id_normalized"]) | set(games["white_id_normalized"])
    per_feature_missing = {
        name: float(missing[..., index][node_valid].mean())
        for index, name in enumerate(OQ_PROFILE_FEATURE_NAMES)
    }
    special_accounts = {}
    for account in ("hero9", "xiaojianbao"):
        rows = games[(games["black_id_normalized"] == account) | (games["white_id_normalized"] == account)]
        special_accounts[account] = {
            "dataset_games": int(len(rows)),
            "snapshot_count": len(snapshots_by_account.get(account, [])),
            "matched": bool(len(rows) and snapshots_by_account.get(account)),
        }
    coverage = {
        "schema": "oq-profile-coverage-report-v1",
        "policy": args.policy,
        "temporalLeakageAuthorized": args.policy == RETROSPECTIVE_TRUSTED_POLICY,
        "games": int(shape[0]),
        "nodes": valid_count,
        "profileAccounts": len(snapshots_by_account),
        "datasetAccounts": len(all_players),
        "gamesWithBothProfiles": games_with_both_profiles,
        "gameCoverageRate": games_with_both_profiles / shape[0],
        "gamesWithoutBothProfiles": games_without_both_profiles,
        "accountsWithoutUsableSnapshot": len(missing_accounts),
        "accountsWithoutUsableSnapshotSample": sorted(missing_accounts)[:100],
        "noUsablePreGameSnapshot": {
            "accounts": len(accounts_without_pre_game_snapshot),
            "gamesWithoutBothPreGameSnapshots": games_without_both_pre_game_snapshots,
        },
        "snapshotSelectionsAfterGameCreated": temporal_violations,
        "nodeCoverage": {
            "currentOverallCompleteRate": float(current_overall_complete.sum() / valid_count),
            "opponentOverallCompleteRate": float(opponent_overall_complete.sum() / valid_count),
            "bothOverallCompleteRate": float((current_overall_complete & opponent_overall_complete).sum() / valid_count),
            "currentColorCompleteRate": float(current_color_complete.sum() / valid_count),
            "opponentColorCompleteRate": float(opponent_color_complete.sum() / valid_count),
            "currentFullNineCompleteRate": float(current_full9_complete.sum() / valid_count),
            "opponentFullNineCompleteRate": float(opponent_full9_complete.sum() / valid_count),
            "bothPlayersFullNineCompleteRate": float((current_full9_complete & opponent_full9_complete).sum() / valid_count),
            "strongCompleteRate": float(strong_complete.sum() / valid_count),
            "weakCompleteRate": float(weak_complete.sum() / valid_count),
        },
        "perFeatureMissingRate": per_feature_missing,
        "splitGames": {str(key): int(value) for key, value in Counter(original["split"].astype(str)).items()},
        "playerOverlapAudit": {
            "trainPlayers": len(split_players["train"]),
            "validationPlayers": len(split_players["validation"]),
            "testPlayers": len(split_players["test"]),
            "trainValidationOverlap": len(split_players["train"] & split_players["validation"]),
            "trainTestOverlap": len(split_players["train"] & split_players["test"]),
            "validationTestOverlap": len(split_players["validation"] & split_players["test"]),
            "testPlayersAlsoInTrainRate": (
                len(split_players["train"] & split_players["test"]) / len(split_players["test"])
                if split_players["test"] else None
            ),
        },
        "specialAccountAudit": special_accounts,
    }
    write_json(coverage_path, coverage)
    manifest = {
        "schema": "tcn-oq-profile-materialization-v1",
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "oqProfileSchema": OQ_PROFILE_SCHEMA,
        "oqProfilePolicy": args.policy,
        "temporalLeakageAuthorized": args.policy == RETROSPECTIVE_TRUSTED_POLICY,
        "temporalWarning": (
            "Current cumulative Player-page statistics were queried after historical games and are trusted by explicit user authorization."
            if args.policy == RETROSPECTIVE_TRUSTED_POLICY else
            "Only the latest snapshot with profile_fetched_at_utc <= game.created is selected."
        ),
        "inputs": {
            "modelReadyNpz": {"path": str(args.input_npz.resolve()), "sha256": sha256_file(args.input_npz)},
            "gamesMetadata": {"path": str(args.games.resolve()), "sha256": sha256_file(args.games)},
            "snapshotDirectory": str(args.snapshots_dir.resolve()),
            "normalizedSnapshotFiles": len(snapshot_files),
            "snapshotSetSha256": snapshot_set_hash(snapshot_files),
        },
        "output": {"path": str(output_npz), "sha256": sha256_file(output_npz)},
        "originalArraysPreserved": True,
        "excludedZeroMoveGameIds": zero_move_game_ids,
        "offbookArraysPreserved": offbook_present,
        "offbookValidationBeforeProfileMaterialization": input_offbook_validation,
        "offbookValidationAfterProfileMaterialization": validation.get("offbook"),
        "originalNumericFeatureCount": int(original["X"].shape[-1]),
        "originalBoardChannelCount": int(len(original["board_cnn_channels"])),
        "featureNames": list(OQ_PROFILE_FEATURE_NAMES),
        "formulas": {
            "N": "win + loss + draw",
            "win_rate": "win / N",
            "draw_rate": "draw / N",
            "games_log": "ln(1 + N)",
            "rating_maturity_40": "min(N / 40, 1)",
            "differenceDirection": "side-to-move player minus opponent",
            "blackCategory": "category=teban,name=sente",
            "whiteCategory": "category=teban,name=gote",
            "strongCategory": "category=opp,name=strong for side-to-move player only",
            "weakCategory": "category=opp,name=weak for side-to-move player only",
        },
        "missingPolicy": "N=0, missing category, or missing profile => value 0 and corresponding missing mask true",
        "normalization": {
            "fitSplit": "train only",
            "source": normalization_source,
            "referenceValidation": reference_validation,
            "means": means.astype(float).tolist(),
            "stds": stds.astype(float).tolist(),
            "sha256": preprocessing_hash,
        },
        "coverageReport": str(coverage_path),
        "validation": validation,
        "coverage": coverage,
    }
    write_json(manifest_path, manifest)
    return manifest


def main() -> int:
    args = parse_args()
    manifest = materialize(args)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
