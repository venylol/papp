#!/usr/bin/env python3
"""Materialize the official 362-feature/23-channel TCN bundle from an OQ handoff.

This script deliberately reuses the retained Egaroucid research feature engineering.
It never derives checkpoint features from their names and never recomputes engine
scores. Pass rows remain in the raw input for label generation, while model sequences
contain actual placements only.

Formal materialization is also the hard provenance gate: every placement must retain
the hint1/hint6 request boards and the native Console boards parsed from the two hint
responses, all matching its source board.  Engine intermediates remain outside this
output and must be retained under EGAROUCID_HINT_PROVENANCE_POLICY.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.checkpoint import load_checkpoint_payload, sha256_file
from src.data_contract import validate_model_ready_npz
from src.feature_policy import INPUT_POLICY
from src.labels import decision_nodes, generate_disc_loss_labels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-dir", type=Path, required=True)
    parser.add_argument("--raw-nodes", type=Path)
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--comparison-oracle", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--preprocessing", type=Path, required=True)
    parser.add_argument("--source-research", type=Path, required=True)
    parser.add_argument("--human-opening-book", type=Path, required=True)
    parser.add_argument("--output-name", default="model_ready_3531.npz")
    parser.add_argument(
        "--exclude-player",
        action="append",
        default=[],
        help="exclude every game containing this player ID; may be repeated",
    )
    parser.add_argument("--workers", type=int, default=min(os.cpu_count() or 1, 16))
    parser.add_argument(
        "--allow-screened-legacy-hint6",
        action="store_true",
        help="allow explicitly labelled exact-key legacy hint6 rows without native response-board evidence",
    )
    args = parser.parse_args()
    output_name = Path(args.output_name)
    if output_name.name != args.output_name or output_name.suffix.lower() != ".npz":
        parser.error("--output-name must be a .npz file name without directories")
    return args


def canonical_json_hash(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_new(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")


def validate_hint_board_provenance(
    raw: pd.DataFrame,
    *,
    allow_screened_legacy_hint6: bool = False,
) -> dict[str, Any]:
    required = {
        "game_id", "move_index", "actual_move", "board_setboard", "legal_moves",
        "n_legal_moves", "hint1_request_board_setboard", "hint1_board_setboard",
        "hint6_request_board_setboard", "hint6_board_setboard", "hint1_move",
    }
    for rank in range(1, 7):
        required.update(
            f"hint6_{rank}_{suffix}"
            for suffix in ("move", "score", "nodes", "depth", "is_book")
        )
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(
            "raw handoff lacks mandatory per-search board provenance/engine fields: "
            f"{missing}"
        )
    keys = ["game_id", "move_index"]
    if raw.duplicated(keys).any():
        examples = raw.loc[raw.duplicated(keys, keep=False), keys].head(10).to_dict("records")
        raise ValueError(f"raw handoff contains duplicate node keys: {examples}")

    placements = raw.loc[raw["actual_move"].astype(str) != "-"]
    board = placements["board_setboard"].astype(str)
    hint1_request_board = placements["hint1_request_board_setboard"].fillna("").astype(str)
    hint1_board = placements["hint1_board_setboard"].fillna("").astype(str)
    hint6_request_board = placements["hint6_request_board_setboard"].fillna("").astype(str)
    hint6_board = placements["hint6_board_setboard"].fillna("").astype(str)
    base_mismatch = (board != hint1_request_board) | (board != hint1_board)
    if allow_screened_legacy_hint6:
        if "hint6_provenance_tier" not in placements.columns:
            raise ValueError("--allow-screened-legacy-hint6 requires hint6_provenance_tier")
        tiers = placements["hint6_provenance_tier"].astype(str)
        legacy = tiers.eq("legacy-exact-key-board-and-legality-screened")
        native = tiers.eq("native-console-response")
        unknown = ~(legacy | native)
        mismatch = (
            base_mismatch
            | unknown
            | (native & ((board != hint6_request_board) | (board != hint6_board)))
            | (legacy & ((board != hint6_request_board) | hint6_board.ne("")))
        )
    else:
        legacy = pd.Series(False, index=placements.index)
        native = pd.Series(True, index=placements.index)
        mismatch = base_mismatch | (board != hint6_request_board) | (board != hint6_board)
    if mismatch.any():
        examples = placements.loc[
            mismatch,
            keys + [
                "board_setboard", "hint1_request_board_setboard", "hint1_board_setboard",
                "hint6_request_board_setboard", "hint6_board_setboard",
            ],
        ].head(10).to_dict("records")
        raise ValueError(
            f"per-search board provenance mismatch: rows={int(mismatch.sum())} examples={examples}"
        )

    errors: list[dict[str, Any]] = []
    checked_candidates = 0
    for row in placements.itertuples(index=False):
        legal_moves = str(row.legal_moves).split()
        legal = set(legal_moves)
        key = {"game_id": str(row.game_id), "move_index": int(row.move_index)}
        if int(row.n_legal_moves) != len(legal_moves):
            errors.append({**key, "error": "n_legal_moves differs from legal_moves"})
            continue
        hint1_move = str(row.hint1_move).lower()
        if hint1_move not in legal:
            errors.append({**key, "error": "missing-or-illegal-hint1", "move": hint1_move})
            continue
        expected = min(6, len(legal_moves))
        seen: set[str] = set()
        for rank in range(1, expected + 1):
            move = str(getattr(row, f"hint6_{rank}_move")).lower()
            fields = [
                getattr(row, f"hint6_{rank}_{suffix}")
                for suffix in ("move", "score", "nodes", "depth", "is_book")
            ]
            if any(pd.isna(value) or str(value).strip() == "" for value in fields):
                errors.append({**key, "error": "incomplete-hint6", "rank": rank})
                break
            if move not in legal:
                errors.append({**key, "error": "illegal-hint6", "rank": rank, "move": move})
                break
            if move in seen:
                errors.append({**key, "error": "duplicate-hint6", "rank": rank, "move": move})
                break
            seen.add(move)
            checked_candidates += 1
        if len(errors) >= 10:
            break
    if errors:
        raise ValueError(f"engine provenance validation failed; first errors={errors}")
    return {
        "ok": True,
        "rows": int(len(raw)),
        "placementRows": int(len(placements)),
        "passRows": int(len(raw) - len(placements)),
        "checkedHint6Candidates": checked_candidates,
        "hint6ProvenanceTiers": {
            "native-console-response": int(native.sum()),
            "legacy-exact-key-board-and-legality-screened": int(legacy.sum()),
        },
        "boardFields": [
            "board_setboard",
            "hint1_request_board_setboard", "hint1_board_setboard",
            "hint6_request_board_setboard", "hint6_board_setboard",
        ],
    }


OFFICIAL_RESEARCH_SCRIPT_NAMES = (
    "train_unified_model.py",
    "train_tcn_model.py",
    "train_tcn_v2_model.py",
    "train_causal_transformer_board_model.py",
    "train_tcn_board_cnn_model.py",
    "build_position_context_metadata.py",
    "add_hint6_dispersion_metadata.py",
    "add_compact_move_dispersion_metadata.py",
    "add_human_opening_frequency_metadata.py",
)


def load_official_modules(source_research: Path):
    source_research = source_research.resolve()
    required = list(OFFICIAL_RESEARCH_SCRIPT_NAMES)
    missing = [name for name in required if not (source_research / name).is_file()]
    if missing:
        raise FileNotFoundError(f"official source research is missing scripts: {missing}")
    sys.path.insert(0, str(source_research))
    import build_position_context_metadata as context_builder
    import train_causal_transformer_board_model as board_context
    import train_tcn_board_cnn_model as board_tcn
    import train_tcn_v2_model as v2
    return context_builder, board_context, board_tcn, v2, required


def validate_against_oracle(decisions: pd.DataFrame, oracle_path: Path) -> dict[str, Any]:
    oracle_columns = [
        "game_id", "move_index", "next_nonpass_move_index", "next_nonpass_source_ply",
        "child_pass_count", "has_consecutive_child", "child_continuity_ok",
        "same_side_after_move", "raw_loss", "disc_loss", "severity_class",
        "label_zero", "label_ge4", "label_ge10", "label_available",
    ]
    oracle = pd.read_csv(oracle_path, usecols=oracle_columns, low_memory=False, encoding="utf-8")
    generated = decisions.rename(columns={
        "next_move_index": "next_nonpass_move_index",
        "next_source_ply": "next_nonpass_source_ply",
    })
    keys = ["game_id", "move_index"]
    if generated.duplicated(keys).any() or oracle.duplicated(keys).any():
        raise ValueError("generated labels or oracle contains duplicate node keys")
    merged = generated[keys + oracle_columns[2:]].merge(
        oracle, on=keys, how="outer", validate="one_to_one", suffixes=("_generated", "_oracle"), indicator=True
    )
    if len(merged) != len(oracle) or not merged["_merge"].eq("both").all():
        raise ValueError("generated decision keys do not exactly match decision_labels.csv")
    compared = oracle_columns[2:]
    errors: dict[str, int] = {}
    for column in compared:
        left = merged[f"{column}_generated"]
        right = merged[f"{column}_oracle"]
        equal = np.isclose(
            pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce"),
            equal_nan=True,
        )
        count = int((~equal).sum())
        if count:
            errors[column] = count
    if errors:
        raise ValueError(f"generated labels differ from decision_labels.csv: {errors}")
    return {"rows": int(len(oracle)), "formulaErrors": 0, "columnsCompared": compared}


def build_context_metadata(
    decision_source: Path,
    context_path: Path,
    context_builder,
    workers: int,
) -> dict[str, Any]:
    columns = [
        "game_id", "ply", "move_index", "board", "side_to_move",
        "hint6_1_move", "hint6_2_move", "hint6_3_move",
    ]
    frame = pd.read_csv(decision_source, usecols=columns, low_memory=False, encoding="utf-8")
    frame["board"] = frame["board"].map(context_builder.normalize_board)
    frame["normalized_side_to_move"] = frame["side_to_move"].map(context_builder.normalize_side)
    unique_columns = [
        "board", "normalized_side_to_move", "ply",
        "hint6_1_move", "hint6_2_move", "hint6_3_move",
    ]
    unique = frame[unique_columns].drop_duplicates().reset_index(drop=True)
    items = list(unique.itertuples(index=False, name=None))
    started = time.time()
    rows: list[dict[str, Any]] = []
    workers = max(1, min(int(workers), len(items)))
    print(f"context: rows={len(frame)} unique={len(items)} workers={workers}", flush=True)
    if workers == 1:
        iterator = map(context_builder.compute_one, items)
        for index, row in enumerate(iterator, start=1):
            rows.append(row)
            if index % 5000 == 0:
                print(f"context: {index}/{len(items)}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            iterator = executor.map(context_builder.compute_one, items, chunksize=256)
            for index, row in enumerate(iterator, start=1):
                rows.append(row)
                if index % 5000 == 0:
                    print(f"context: {index}/{len(items)}", flush=True)
    metadata_unique = pd.DataFrame(rows)
    merged = frame.merge(
        metadata_unique,
        on=unique_columns,
        how="left",
        suffixes=("", "_meta"),
        validate="many_to_one",
    )
    merged = merged.drop(
        columns=[column for column in ("normalized_side_to_move_meta", "side_to_move_meta") if column in merged],
        errors="ignore",
    )
    merged.to_csv(context_path, index=False, encoding="utf-8")
    return {
        "rows": int(len(merged)), "uniquePositions": int(len(items)),
        "workers": workers, "elapsedSeconds": round(time.time() - started, 3),
    }


def run_metadata_augmenters(
    source_research: Path,
    decision_source: Path,
    context_path: Path,
    human_opening_book: Path,
    output_dir: Path,
) -> None:
    commands = [
        [sys.executable, str(source_research / "add_hint6_dispersion_metadata.py"),
         "--data", str(decision_source), "--metadata", str(context_path)],
        [sys.executable, str(source_research / "add_compact_move_dispersion_metadata.py"),
         "--metadata", str(context_path)],
        [sys.executable, str(source_research / "add_human_opening_frequency_metadata.py"),
         "--metadata", str(context_path), "--book", str(human_opening_book),
         "--frequency-lookup-out", str(output_dir / "human_opening_frequency_lookup.csv"),
         "--frequency-summary-out", str(output_dir / "human_opening_frequency_summary.json")],
    ]
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(source_research), existing_pythonpath) if value
    )
    for command in commands:
        print("running:", subprocess.list2cmdline(command), flush=True)
        subprocess.run(command, cwd=source_research, env=environment, check=True)


def official_feature_frame(v2, decision_source: Path, context_path: Path) -> pd.DataFrame:
    v2.CONTEXT_METADATA_PATH = context_path
    frame, _all, _categorical, _numeric, _metadata = v2.prepare_frame_v2_with_context_metadata(decision_source)
    return frame.sort_values(["game_id", "move_index"], kind="stable").reset_index(drop=True)


def apply_checkpoint_preprocessing(frame: pd.DataFrame, preprocessing: dict[str, Any]) -> np.ndarray:
    numeric_features = list(preprocessing["numeric_features"])
    missing_features = list(preprocessing["missing_indicator_features"])
    input_features = list(preprocessing["input_features"])
    missing_columns = [name for name in numeric_features if name not in frame]
    if missing_columns:
        raise ValueError(f"official feature frame is missing checkpoint features: {missing_columns}")
    raw_frame = frame[numeric_features].copy()
    for column in numeric_features:
        raw_frame[column] = pd.to_numeric(raw_frame[column], errors="coerce")
    raw = raw_frame.to_numpy(dtype=np.float32)
    missing = ~np.isfinite(raw)
    means = np.asarray(preprocessing["means"], dtype=np.float32)
    stds = np.asarray(preprocessing["stds"], dtype=np.float32)
    if means.shape != (len(numeric_features),) or stds.shape != means.shape:
        raise ValueError("checkpoint preprocessing mean/std shape mismatch")
    scaled = (raw - means) / stds
    scaled = np.where(np.isfinite(scaled), scaled, 0.0).astype(np.float32)
    missing_indexes = [numeric_features.index(name) for name in missing_features]
    if missing_indexes:
        scaled = np.concatenate([scaled, missing[:, missing_indexes].astype(np.float32)], axis=1)
    expected = numeric_features + [f"{name}__missing" for name in missing_features]
    if expected != input_features or scaled.shape[1] != len(input_features):
        raise ValueError("checkpoint preprocessing input feature construction mismatch")
    return scaled


def make_model_ready_arrays(
    frame: pd.DataFrame,
    features: np.ndarray,
    decisions: pd.DataFrame,
    split_manifest: Path,
    preprocessing: dict[str, Any],
    checkpoint: dict[str, Any],
    board_context,
    board_tcn,
    workers: int,
) -> dict[str, np.ndarray]:
    keys = ["game_id", "move_index"]
    frame_keys = pd.MultiIndex.from_frame(frame[keys])
    audit = decisions.set_index(keys).loc[frame_keys].reset_index()
    if not audit[keys].equals(frame[keys]):
        raise ValueError("official feature rows do not align with generated decision labels")
    games = np.asarray(pd.unique(frame["game_id"].astype(str)))
    max_len = int(frame.groupby("game_id", sort=False).size().max())
    if max_len > 60:
        raise ValueError(f"decision sequence exceeds 60 placements: {max_len}")
    n_games, n_features = len(games), features.shape[1]
    shape = (n_games, max_len)
    arrays: dict[str, np.ndarray] = {
        "X": np.zeros((*shape, n_features), dtype=np.float32),
        "actual_thinking_time_ms": np.zeros(shape, dtype=np.float32),
        "raw_loss": np.full(shape, np.nan, dtype=np.float32),
        "disc_loss": np.zeros(shape, dtype=np.float32),
        "severity_class": np.zeros(shape, dtype=np.int8),
        "label_zero": np.zeros(shape, dtype=np.int8),
        "label_ge4": np.zeros(shape, dtype=np.int8),
        "label_ge10": np.zeros(shape, dtype=np.int8),
        "mask": np.zeros(shape, dtype=bool),
        "player_id": np.full(shape, "", dtype="U64"),
        "side_to_move": np.full(shape, "", dtype="U8"),
        "move_index": np.full(shape, -1, dtype=np.int16),
        "source_ply_including_pass": np.zeros(shape, dtype=np.int16),
        "global_placement_ply": np.zeros(shape, dtype=np.int16),
        "label_available": np.zeros(shape, dtype=bool),
        "has_consecutive_child": np.zeros(shape, dtype=bool),
        "child_continuity_ok": np.zeros(shape, dtype=bool),
        "same_side_after_move": np.zeros(shape, dtype=bool),
        "current_score": np.zeros(shape, dtype=np.float32),
        "actual_move_score": np.zeros(shape, dtype=np.float32),
        "wld_class": np.zeros(shape, dtype=np.int8),
        "wld_loss": np.zeros(shape, dtype=np.float32),
        "wld_label_available": np.zeros(shape, dtype=bool),
    }
    row_positions = np.arange(len(frame), dtype=np.int64)
    rows_by_game = frame.groupby("game_id", sort=False).indices
    for game_index, game_id in enumerate(games):
        positions = row_positions[np.asarray(rows_by_game[game_id], dtype=np.int64)]
        length = len(positions)
        view = audit.iloc[positions]
        arrays["X"][game_index, :length] = features[positions]
        arrays["actual_thinking_time_ms"][game_index, :length] = pd.to_numeric(
            view["actual_thinking_time_ms"], errors="raise"
        ).to_numpy(np.float32)
        available = view["label_available"].astype(bool).to_numpy()
        arrays["mask"][game_index, :length] = available
        arrays["label_available"][game_index, :length] = available
        for name in ("raw_loss", "disc_loss"):
            values = pd.to_numeric(view[name], errors="coerce").to_numpy(np.float32)
            if name == "disc_loss":
                values = np.where(np.isfinite(values), values, 0).astype(np.float32)
            arrays[name][game_index, :length] = values
        for name in ("severity_class", "label_zero", "label_ge4", "label_ge10"):
            arrays[name][game_index, :length] = pd.to_numeric(view[name], errors="coerce").fillna(0).to_numpy(np.int8)
        arrays["player_id"][game_index, :length] = view["player_id"].astype(str).to_numpy()
        arrays["side_to_move"][game_index, :length] = view["side_to_move"].astype(str).str.lower().to_numpy()
        arrays["move_index"][game_index, :length] = pd.to_numeric(view["move_index"], errors="raise").to_numpy(np.int16)
        arrays["source_ply_including_pass"][game_index, :length] = pd.to_numeric(
            view["source_ply_including_pass"], errors="raise"
        ).to_numpy(np.int16)
        arrays["global_placement_ply"][game_index, :length] = pd.to_numeric(
            view["global_placement_ply"], errors="raise"
        ).to_numpy(np.int16)
        for name in ("has_consecutive_child", "child_continuity_ok", "same_side_after_move"):
            arrays[name][game_index, :length] = view[name].astype(bool).to_numpy()
        for name in ("current_score", "actual_move_score", "wld_loss"):
            arrays[name][game_index, :length] = (
                pd.to_numeric(view[name], errors="coerce").fillna(0).to_numpy(np.float32)
            )
        arrays["wld_class"][game_index, :length] = (
            pd.to_numeric(view["wld_class"], errors="coerce").fillna(0).to_numpy(np.int8)
        )
        arrays["wld_label_available"][game_index, :length] = (
            view["wld_label_available"].astype(bool).to_numpy()
        )

    board_tokens, board_move_tokens = board_context.make_board_context_sequences(frame, games, workers)
    current_hint_tokens = board_tcn.make_current_hint_move_sequences(frame, games, 6)
    current_hint_values = board_tcn.make_current_hint_value_sequences(frame, games, 6)
    prev_own_hint_values = board_tcn.make_prev_own_hint_value_sequences(frame, games)
    if not np.all(board_move_tokens[:, :, 0] == 0):
        raise ValueError("current actual move leaked into board_move_tokens current context")
    arrays.update({
        "board_tokens": board_tokens,
        "board_move_tokens": board_move_tokens,
        "current_hint_tokens": current_hint_tokens,
        "current_hint_values": current_hint_values,
        "prev_own_hint_values": prev_own_hint_values,
    })
    splits = pd.read_csv(split_manifest, encoding="utf-8")
    if splits.duplicated("game_id").any():
        raise ValueError("split_manifest.csv has duplicate game_id")
    split_by_game = splits.set_index("game_id")["split"]
    missing_splits = [game for game in games if game not in split_by_game.index]
    if missing_splits:
        raise ValueError(f"games missing split assignments: {missing_splits[:5]}")
    board_channels = checkpoint["board_encoding"]["cnn_channels"]
    official_channels = board_tcn.make_board_cnn_encoding(6)["cnn_channels"]
    if official_channels != board_channels:
        raise ValueError("official board feature code channel order differs from checkpoint")
    arrays.update({
        "game_id": games.astype("U64"),
        "split": split_by_game.loc[games].astype(str).to_numpy(dtype="U10"),
        "input_features": np.asarray(preprocessing["input_features"], dtype="U128"),
        "board_cnn_channels": np.asarray(board_channels, dtype="U96"),
        "preprocessing_sha256": np.asarray(canonical_json_hash(checkpoint["preprocessing"])),
        "input_policy": np.asarray(INPUT_POLICY),
    })
    return arrays


def main() -> int:
    args = parse_args()
    started = time.time()
    handoff = args.handoff_dir.resolve()
    output = args.output_dir.resolve()
    require_new(output)
    raw_path = args.raw_nodes.resolve() if args.raw_nodes else handoff / "raw_nodes_with_pass.csv"
    split_path = args.split_manifest.resolve() if args.split_manifest else handoff / "split_manifest.csv"
    oracle_path = args.comparison_oracle.resolve() if args.comparison_oracle else None
    required_paths = (raw_path, split_path, args.base_checkpoint, args.preprocessing, args.human_opening_book)
    for path in required_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    if oracle_path is not None and not oracle_path.is_file():
        raise FileNotFoundError(oracle_path)
    print("validating independent hint1/hint6 board provenance", flush=True)
    raw = pd.read_csv(raw_path, low_memory=False, encoding="utf-8")
    provenance_validation = validate_hint_board_provenance(
        raw,
        allow_screened_legacy_hint6=args.allow_screened_legacy_hint6,
    )
    shape = {
        "rows": int(len(raw)),
        "placements": int((raw["actual_move"].astype(str) != "-").sum()),
        "passes": int((raw["actual_move"].astype(str) == "-").sum()),
        "games": int(raw["game_id"].astype(str).nunique()),
    }
    expected_shape = {"rows": 609124, "placements": 599112, "passes": 10012, "games": 10000}
    if shape != expected_shape:
        raise ValueError(f"safe assembled raw shape differs from frozen contract: {shape} != {expected_shape}")
    excluded_players = {
        str(player).strip().lower() for player in args.exclude_player if str(player).strip()
    }
    normalized_players = raw["player_id"].astype(str).str.strip().str.lower()
    excluded_player_games = sorted(
        raw.loc[normalized_players.isin(excluded_players), "game_id"]
        .astype(str)
        .unique()
        .tolist()
    )
    if excluded_player_games:
        raw = raw.loc[~raw["game_id"].astype(str).isin(excluded_player_games)].copy()
    output.mkdir(parents=True)
    context_builder, board_context, board_tcn, v2, source_scripts = load_official_modules(args.source_research)
    checkpoint = load_checkpoint_payload(args.base_checkpoint)
    external_preprocessing = json.loads(args.preprocessing.read_text(encoding="utf-8"))
    if external_preprocessing != checkpoint["preprocessing"]:
        raise ValueError("external preprocessing.json differs from base checkpoint payload")

    print("loading raw nodes and generating pass-safe labels", flush=True)
    labelled = generate_disc_loss_labels(raw)
    decisions = decision_nodes(labelled).sort_values(["game_id", "move_index"], kind="stable").reset_index(drop=True)
    if int(decisions["label_available"].astype(bool).sum()) != len(decisions) - decisions["game_id"].nunique():
        raise ValueError("labelled node count is not exactly placements minus one terminal node per game")
    finite_raw = pd.to_numeric(decisions.loc[decisions["label_available"].astype(bool), "raw_loss"], errors="raise")
    if not np.allclose(finite_raw.to_numpy(), np.rint(finite_raw.to_numpy())):
        raise ValueError("new hint6 labels contain non-integer raw loss")
    oracle_validation = (
        validate_against_oracle(decisions, oracle_path)
        if oracle_path is not None
        else {
            "mode": "new-safe-hint6-labels-no-legacy-oracle",
            "legacyOracleUsed": False,
            "reason": "old decision_labels.csv derives from unsafe hint6 and is not a formal equality oracle",
        }
    )
    decision_source = output / "decision_feature_source.csv"
    generated_labels_path = output / "decision_labels_recomputed.csv"
    context_path = output / "position_context_metadata.csv"
    model_ready_path = output / args.output_name
    validation_path = output / "model_ready_validation.json"
    manifest_path = output / "materialization_manifest.json"
    raw_columns = list(raw.columns)
    feature_source = decisions[raw_columns].copy()
    feature_source["ply"] = decisions["global_placement_ply"].astype("int64")
    feature_source["source_ply_including_pass"] = decisions["source_ply_including_pass"].astype("int64")
    feature_source["global_placement_ply"] = decisions["global_placement_ply"].astype("int64")
    feature_source["is_pass_record"] = 0
    feature_source.to_csv(decision_source, index=False, encoding="utf-8")
    label_columns = [
        "game_id", "move_index", "source_ply_including_pass", "global_placement_ply",
        "next_move_index", "next_source_ply", "child_pass_count", "has_consecutive_child",
        "child_continuity_ok", "same_side_after_move", "raw_loss", "disc_loss",
        "severity_class", "label_zero", "label_ge4", "label_ge10", "label_available",
    ]
    decisions[label_columns].to_csv(generated_labels_path, index=False, encoding="utf-8")

    context_report = build_context_metadata(decision_source, context_path, context_builder, args.workers)
    run_metadata_augmenters(
        args.source_research.resolve(), decision_source, context_path,
        args.human_opening_book.resolve(), output,
    )
    print("deriving official numeric and board-CNN features", flush=True)
    frame = official_feature_frame(v2, decision_source, context_path)
    feature_matrix = apply_checkpoint_preprocessing(frame, external_preprocessing)
    arrays = make_model_ready_arrays(
        frame, feature_matrix, decisions, split_path, external_preprocessing,
        checkpoint, board_context, board_tcn, args.workers,
    )
    temporary_npz = model_ready_path.with_suffix(".tmp.npz")
    np.savez_compressed(temporary_npz, **arrays)
    temporary_npz.replace(model_ready_path)
    validation = validate_model_ready_npz(
        model_ready_path,
        expected_input_features=checkpoint["input_features"],
        expected_board_channels=checkpoint["board_encoding"]["cnn_channels"],
        expected_preprocessing_sha256=canonical_json_hash(checkpoint["preprocessing"]),
    )
    validation.update({
        "labelledNodes": int(arrays["mask"].sum()),
        "decisionNodes": int((arrays["global_placement_ply"] > 0).sum()),
        "classCounts": {
            str(index): int(np.sum(arrays["severity_class"][arrays["mask"]] == index))
            for index in range(4)
        },
        "currentMoveHidden": bool(np.all(arrays["board_move_tokens"][:, :, 0] == 0)),
    })
    write_json(validation_path, validation)
    manifest = {
        "schema": "tcn-loss-oq-materialization-v1",
        "ok": True,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsedSeconds": round(time.time() - started, 3),
        "inputPolicy": INPUT_POLICY,
        "inputs": {
            "rawNodes": {"path": str(raw_path), "sha256": sha256_file(raw_path)},
            "splitManifest": {"path": str(split_path), "sha256": sha256_file(split_path)},
            "baseCheckpoint": {"path": str(args.base_checkpoint.resolve()), "sha256": sha256_file(args.base_checkpoint)},
            "preprocessing": {"path": str(args.preprocessing.resolve()), "sha256": sha256_file(args.preprocessing)},
            "humanOpeningBook": {"path": str(args.human_opening_book.resolve()), "sha256": sha256_file(args.human_opening_book)},
            "officialSourceScripts": {
                name: sha256_file(args.source_research.resolve() / name) for name in source_scripts
            },
        },
        "frozenInputShape": shape,
        "excludedPlayers": sorted(excluded_players),
        "excludedPlayerGameIds": excluded_player_games,
        "generatedLabels": {
            "path": str(generated_labels_path),
            "sha256": sha256_file(generated_labels_path),
            "decisionNodes": int(len(decisions)),
            "labelledNodes": int(decisions["label_available"].astype(bool).sum()),
            "negativeRawLossNodes": int((finite_raw < 0).sum()),
            "sameSideAfterMoveNodes": int(decisions["same_side_after_move"].astype(bool).sum()),
        },
        "oracleValidation": oracle_validation,
        "engineBoardProvenanceValidation": provenance_validation,
        "context": context_report,
        "output": {"path": str(model_ready_path), "sha256": sha256_file(model_ready_path)},
        "validation": validation,
    }
    write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
