"""Raw-node and model-ready-bundle validation without starting training."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .feature_policy import INPUT_POLICY
from .labels import assert_no_label_leakage, decision_nodes, generate_disc_loss_labels
from .oq_profile_features import (
    OQ_PROFILE_FEATURE_NAMES,
    OQ_PROFILE_SCHEMA,
    RETROSPECTIVE_TRUSTED_POLICY,
    STRICT_PRE_GAME_POLICY,
    canonical_json_hash,
    parse_utc,
)
from .offbook import OFFBOOK_SCHEMA, validate_offbook_arrays

RAW_REQUIRED = {
    "game_id", "move_index", "ply", "player_id", "side_to_move", "actual_move",
    "actual_thinking_time_ms", "board", "hint6_1_score",
}
MODEL_READY_ARRAYS = {
    "X", "board_tokens", "board_move_tokens", "current_hint_tokens", "current_hint_values",
    "prev_own_hint_values", "actual_thinking_time_ms", "disc_loss", "mask", "game_id",
    "player_id", "global_placement_ply", "side_to_move", "split",
    "input_features", "board_cnn_channels", "preprocessing_sha256",
    "input_policy",
    "raw_loss", "severity_class", "label_zero", "label_ge4", "label_ge10",
    "move_index", "source_ply_including_pass", "label_available",
    "has_consecutive_child", "child_continuity_ok", "same_side_after_move",
    "current_score", "actual_move_score", "wld_class", "wld_loss",
    "wld_label_available",
}
OQ_PROFILE_ARRAYS = {
    "oq_profile_raw_features", "oq_profile_features", "oq_profile_missing",
    "oq_profile_feature_names", "oq_profile_node_valid",
    "oq_profile_player_snapshot_time_utc", "oq_profile_opponent_snapshot_time_utc",
    "oq_profile_player_raw_response_sha256", "oq_profile_opponent_raw_response_sha256",
    "oq_profile_policy", "oq_profile_temporal_leakage_authorized", "oq_profile_schema",
    "oq_profile_game_created_utc", "oq_profile_preprocessing_mean",
    "oq_profile_preprocessing_std", "oq_profile_preprocessing_sha256",
}


def load_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, low_memory=False, encoding="utf-8")
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix in {".jsonl", ".ndjson"}:
        return pd.read_json(path, lines=True, encoding="utf-8")
    raise ValueError(f"unsupported table format: {path.suffix}; use CSV, Parquet, or JSONL")


def _validate_board(series: pd.Series) -> tuple[int, list[str]]:
    normalized = series.astype("string").str.upper().str.replace(".", "-", regex=False)
    valid_chars = normalized.str.fullmatch(r"[XO-]{64}", na=False)
    return int((~valid_chars).sum()), normalized.loc[~valid_chars].head(3).tolist()


def validate_raw_data(data_path: Path, context_metadata_path: Path | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = load_table(data_path)
    missing = sorted(RAW_REQUIRED - set(raw.columns))
    if missing:
        raise ValueError(f"raw data missing required columns: {missing}")
    time_column = "time_limit_ms" if "time_limit_ms" in raw.columns else "tcb" if "tcb" in raw.columns else None
    if time_column is None:
        raise ValueError("raw data must contain time_limit_ms or tcb to prove five-minute scope")
    if raw.empty:
        raise ValueError("raw data is empty")
    if raw["game_id"].isna().any() or raw["game_id"].astype("string").str.strip().eq("").any():
        raise ValueError("game_id contains missing/blank values")
    duplicate_nodes = int(raw.duplicated(["game_id", "move_index"]).sum())
    if duplicate_nodes:
        raise ValueError(f"duplicate game_id+move_index node keys: {duplicate_nodes}")
    if raw["player_id"].isna().any() or raw["player_id"].astype("string").str.strip().eq("").any():
        raise ValueError("player_id contains missing/blank values")
    time_limit = pd.to_numeric(raw[time_column], errors="coerce")
    non_five_minute = int(time_limit.ne(300000).sum())
    if non_five_minute:
        raise ValueError(f"non-five-minute rows ({time_column} != 300000): {non_five_minute}")
    invalid_boards, board_samples = _validate_board(raw["board"])
    if invalid_boards:
        raise ValueError(f"invalid 8x8 board encodings: {invalid_boards}, sample={board_samples}")
    actual_time = pd.to_numeric(raw["actual_thinking_time_ms"], errors="coerce")
    is_pass = raw["actual_move"].astype("string").str.strip().eq("-").fillna(False)
    bad_actual_time = int((~is_pass & (actual_time.isna() | actual_time.lt(0))).sum())
    if bad_actual_time:
        raise ValueError(f"actual placement rows with missing/negative thinking time: {bad_actual_time}")

    labelled = generate_disc_loss_labels(raw)
    nodes = decision_nodes(labelled)
    if nodes["disc_loss"].dropna().lt(0).any():
        raise ValueError("disc_loss must be nonnegative")
    split_leakage: dict[str, list[str]] = {}
    if "split" in nodes.columns:
        membership = nodes.groupby("game_id", observed=True)["split"].nunique()
        leaked = membership[membership > 1].index.astype(str).tolist()
        if leaked:
            split_leakage["gameIds"] = leaked[:20]
            raise ValueError(f"game leakage across train/validation/test splits: {len(leaked)} games")
        invalid_splits = sorted(set(nodes["split"].dropna().astype(str)) - {"train", "validation", "test"})
        if invalid_splits:
            raise ValueError(f"invalid split labels: {invalid_splits}")

    context_summary: dict[str, Any] = {"provided": False}
    if context_metadata_path:
        context = load_table(context_metadata_path)
        keys = ["game_id", "ply", "move_index"]
        missing_context = [key for key in keys if key not in context]
        if missing_context:
            raise ValueError(f"context metadata missing keys: {missing_context}")
        duplicates = int(context.duplicated(keys).sum())
        if duplicates:
            raise ValueError(f"context metadata duplicate keys: {duplicates}")
        raw_keys = labelled[["game_id", "source_ply_including_pass", "move_index"]].rename(columns={"source_ply_including_pass": "ply"})
        merged = raw_keys.merge(context[keys].drop_duplicates(), on=keys, how="left", indicator=True)
        unmatched = int(merged["_merge"].ne("both").sum())
        if unmatched:
            raise ValueError(f"raw nodes missing context metadata matches: {unmatched}")
        context_summary = {"provided": True, "path": str(context_metadata_path.resolve()), "rows": int(len(context)), "unmatched": 0}

    labels = nodes["disc_loss"].dropna()
    raw_loss = nodes.loc[labels.index, "raw_loss"] if len(labels) else pd.Series(dtype=float)
    report = {
        "schema": "tcn-loss-raw-validation-v1", "ok": True,
        "dataPath": str(data_path.resolve()), "contextMetadata": context_summary,
        "games": int(nodes["game_id"].nunique()), "players": int(nodes["player_id"].nunique()),
        "rawRows": int(len(raw)), "decisionNodes": int(len(nodes)), "passRows": int(labelled["is_pass_record"].sum()),
        "labelledNodes": int(len(labels)), "labelCoverage": float(len(labels) / len(nodes)) if len(nodes) else 0.0,
        "rawNegativeLossNodes": int((raw_loss < 0).sum()),
        "rawNegativeLossRate": float((raw_loss < 0).mean()) if len(raw_loss) else None,
        "zeroLossRate": float((labels == 0).mean()) if len(labels) else None,
        "maxSourcePlyIncludingPass": int(labelled["source_ply_including_pass"].max()),
        "maxGlobalPlacementPly": int(nodes["global_placement_ply"].max()),
        "placementPlySemantics": "explicit pass excluded; per-game actual placement index in 1..60",
        "inputPolicy": INPUT_POLICY,
        "lossHistoryFeatureConstruction": "omitted-not-masked",
        "missingRates": {
            column: float(nodes[column].isna().mean())
            for column in ("actual_thinking_time_ms", "board", "hint6_1_score", "disc_loss")
        },
        "splitLeakage": split_leakage,
    }
    return nodes, report


def validate_model_ready_npz(path: Path, expected_input_dim: int = 362,
                             expected_input_features: list[str] | None = None,
                             expected_board_channels: list[str] | None = None,
                             expected_preprocessing_sha256: str | None = None,
                             require_oq_profile: bool = False,
                             expected_oq_profile_feature_names: list[str] | tuple[str, ...] | None = None,
                             expected_oq_profile_preprocessing_sha256: str | None = None,
                             expected_oq_profile_policy: str | None = None,
                             require_offbook: bool = False,
                             expected_offbook_schema: str = OFFBOOK_SCHEMA,
                             expected_offbook_source_data_sha256: str | None = None,
                             expected_offbook_source_checkpoint_sha256: str | None = None,
                             expected_offbook_records_sha256: str | None = None,
                             expected_offbook_materialization_sha256: str | None = None) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as data:
        missing = sorted(MODEL_READY_ARRAYS - set(data.files))
        if missing:
            raise ValueError(f"model-ready NPZ missing arrays: {missing}")
        x = data["X"]
        if x.ndim != 3 or x.shape[-1] != expected_input_dim:
            raise ValueError(f"X must be games x time x {expected_input_dim}, got {x.shape}")
        shape = x.shape[:2]
        expected_shapes = {
            "board_tokens": (*shape, 3, 64), "board_move_tokens": (*shape, 3),
            "current_hint_tokens": (*shape, 6), "current_hint_values": (*shape, 4),
            "prev_own_hint_values": (*shape, 2), "actual_thinking_time_ms": shape,
            "disc_loss": shape, "mask": shape, "global_placement_ply": shape,
            "player_id": shape, "side_to_move": shape,
            "raw_loss": shape, "severity_class": shape, "label_zero": shape,
            "label_ge4": shape, "label_ge10": shape,
            "move_index": shape, "source_ply_including_pass": shape,
            "label_available": shape, "has_consecutive_child": shape,
            "child_continuity_ok": shape, "same_side_after_move": shape,
            "current_score": shape, "actual_move_score": shape,
            "wld_class": shape, "wld_loss": shape, "wld_label_available": shape,
        }
        for name, expected in expected_shapes.items():
            if data[name].shape != expected:
                raise ValueError(f"{name} expected shape {expected}, got {data[name].shape}")
        valid = data["mask"].astype(bool)
        ply = data["global_placement_ply"][valid]
        if np.any((ply < 1) | (ply > 60)):
            raise ValueError("model-ready bundle contains placement ply outside 1..60")
        disc_loss = data["disc_loss"][valid]
        severity_class = data["severity_class"][valid]
        label_zero = data["label_zero"][valid]
        label_ge4 = data["label_ge4"][valid]
        label_ge10 = data["label_ge10"][valid]
        if np.any(disc_loss < 0):
            raise ValueError("model-ready disc_loss must be nonnegative")
        expected_class = np.select(
            [disc_loss == 0, (disc_loss >= 1) & (disc_loss <= 3), (disc_loss >= 4) & (disc_loss <= 9), disc_loss >= 10],
            [0, 1, 2, 3], default=-1,
        ).astype(severity_class.dtype)
        if not np.array_equal(severity_class, expected_class):
            raise ValueError("severity_class does not match the four fixed disc-loss intervals")
        if not np.array_equal(label_zero, (disc_loss == 0).astype(label_zero.dtype)):
            raise ValueError("label_zero does not equal 1[disc_loss == 0]")
        if not np.array_equal(label_ge4, (disc_loss >= 4).astype(label_ge4.dtype)):
            raise ValueError("label_ge4 does not equal 1[disc_loss >= 4]")
        if not np.array_equal(label_ge10, (disc_loss >= 10).astype(label_ge10.dtype)):
            raise ValueError("label_ge10 does not equal 1[disc_loss >= 10]")
        if np.any(label_ge10 > label_ge4):
            raise ValueError("label_ge10 cannot exceed label_ge4")
        wld_available = data["wld_label_available"].astype(bool)
        expected_wld_available = (
            valid & data["label_available"].astype(bool)
            & data["child_continuity_ok"].astype(bool)
            & (data["global_placement_ply"] >= 39)
        )
        if not np.array_equal(wld_available, expected_wld_available):
            raise ValueError("wld_label_available violates the pass-safe ply >= 39 contract")
        wld_class = data["wld_class"][wld_available]
        wld_loss = data["wld_loss"][wld_available]
        if np.any((wld_class < 0) | (wld_class > 2)):
            raise ValueError("wld_class must be in 0..2 on available nodes")
        if not np.allclose(wld_loss, wld_class / 2.0, rtol=0, atol=1e-7):
            raise ValueError("wld_loss must equal wld_class / 2")
        before_rank = np.where(data["current_score"] > 0, 2, np.where(data["current_score"] < 0, 0, 1))
        after_rank = np.where(data["actual_move_score"] > 0, 2, np.where(data["actual_move_score"] < 0, 0, 1))
        expected_wld_class = np.maximum(0, before_rank - after_rank)
        if not np.array_equal(wld_class, expected_wld_class[wld_available].astype(wld_class.dtype)):
            raise ValueError("wld_class differs from the signed-score WLD rank drop")
        splits = data["split"].astype(str)
        game_ids = data["game_id"].astype(str)
        if splits.shape != (shape[0],) or game_ids.shape != (shape[0],):
            raise ValueError("split and game_id must contain one value per game sequence")
        if set(splits) - {"train", "validation", "test"}:
            raise ValueError("model-ready split must be train/validation/test")
        if len(set(game_ids)) != len(game_ids):
            raise ValueError("model-ready game_id values must be unique per sequence")
        input_features = data["input_features"].astype(str).tolist()
        board_channels = data["board_cnn_channels"].astype(str).tolist()
        preprocessing_sha256 = str(data["preprocessing_sha256"].item())
        input_policy = str(data["input_policy"].item())
        if len(input_features) != expected_input_dim or len(set(input_features)) != expected_input_dim:
            raise ValueError("model-ready input_features must be 362 unique ordered names")
        if len(board_channels) != 23 or len(set(board_channels)) != 23:
            raise ValueError("model-ready board_cnn_channels must be 23 unique ordered names")
        assert_no_label_leakage(input_features)
        if input_policy != INPUT_POLICY:
            raise ValueError(
                f"model-ready input_policy must be {INPUT_POLICY!r}, got {input_policy!r}"
            )
        if expected_input_features is not None and input_features != expected_input_features:
            raise ValueError("model-ready feature order differs from base checkpoint")
        if expected_board_channels is not None and board_channels != expected_board_channels:
            raise ValueError("model-ready board channel order differs from base checkpoint")
        if expected_preprocessing_sha256 is not None and preprocessing_sha256 != expected_preprocessing_sha256:
            raise ValueError("model-ready preprocessing hash differs from base checkpoint")
        oq_profile_report = None
        if require_oq_profile:
            missing_oq = sorted(OQ_PROFILE_ARRAYS - set(data.files))
            if missing_oq:
                raise ValueError(f"model-ready NPZ missing required OQ profile arrays: {missing_oq}")
            profile_shape = (*shape, 31)
            for name in ("oq_profile_raw_features", "oq_profile_features", "oq_profile_missing"):
                if data[name].shape != profile_shape:
                    raise ValueError(f"{name} expected shape {profile_shape}, got {data[name].shape}")
            for name in (
                "oq_profile_node_valid", "oq_profile_player_snapshot_time_utc",
                "oq_profile_opponent_snapshot_time_utc", "oq_profile_player_raw_response_sha256",
                "oq_profile_opponent_raw_response_sha256",
            ):
                if data[name].shape != shape:
                    raise ValueError(f"{name} expected shape {shape}, got {data[name].shape}")
            if data["oq_profile_game_created_utc"].shape != (shape[0],):
                raise ValueError("oq_profile_game_created_utc must contain one timestamp per game")
            feature_names = data["oq_profile_feature_names"].astype(str).tolist()
            if feature_names != list(OQ_PROFILE_FEATURE_NAMES):
                raise ValueError("OQ profile feature order differs from the fixed 31-feature contract")
            if expected_oq_profile_feature_names is not None and feature_names != list(expected_oq_profile_feature_names):
                raise ValueError("OQ profile feature order differs from checkpoint")
            assert_no_label_leakage(feature_names)
            profile_schema = str(data["oq_profile_schema"].item())
            if profile_schema != OQ_PROFILE_SCHEMA:
                raise ValueError(f"OQ profile schema mismatch: {profile_schema!r}")
            profile_policy = str(data["oq_profile_policy"].item())
            if profile_policy not in {STRICT_PRE_GAME_POLICY, RETROSPECTIVE_TRUSTED_POLICY}:
                raise ValueError(f"unsupported OQ profile policy: {profile_policy!r}")
            if expected_oq_profile_policy is not None and profile_policy != expected_oq_profile_policy:
                raise ValueError("OQ profile policy differs from checkpoint")
            leakage_authorized = bool(data["oq_profile_temporal_leakage_authorized"].item())
            if profile_policy == RETROSPECTIVE_TRUSTED_POLICY and not leakage_authorized:
                raise ValueError("retrospective OQ profile policy lacks temporal leakage authorization")
            if profile_policy == STRICT_PRE_GAME_POLICY and leakage_authorized:
                raise ValueError("strict pre-game OQ profile policy cannot authorize temporal leakage")
            node_valid = data["oq_profile_node_valid"].astype(bool)
            expected_node_valid = data["global_placement_ply"] > 0
            if not np.array_equal(node_valid, expected_node_valid):
                raise ValueError("OQ profile node-valid mask differs from non-padded placement nodes")
            profile_missing = data["oq_profile_missing"].astype(bool)
            raw_profile = data["oq_profile_raw_features"]
            normalized_profile = data["oq_profile_features"]
            if not np.isfinite(raw_profile).all() or not np.isfinite(normalized_profile).all():
                raise ValueError("OQ profile arrays contain NaN or Inf")
            if np.any(raw_profile[~node_valid] != 0) or np.any(normalized_profile[~node_valid] != 0):
                raise ValueError("padded nodes carry nonzero OQ profile features")
            if not np.all(profile_missing[~node_valid]):
                raise ValueError("padded nodes must mark all OQ profile features missing")
            if np.any(normalized_profile[profile_missing] != 0):
                raise ValueError("missing OQ profile features must be zero after normalization")
            means = data["oq_profile_preprocessing_mean"].astype(np.float32)
            stds = data["oq_profile_preprocessing_std"].astype(np.float32)
            if means.shape != (31,) or stds.shape != (31,) or not np.isfinite(means).all() or not np.isfinite(stds).all() or np.any(stds <= 0):
                raise ValueError("OQ profile preprocessing mean/std contract failure")
            preprocessing = {
                "schema": "oq-profile-train-only-standardization-v1",
                "feature_names": list(OQ_PROFILE_FEATURE_NAMES),
                "fit_split": "train",
                "missing_fill_after_standardization": 0.0,
                "std_ddof": 0,
                "means": means.astype(float).tolist(),
                "stds": stds.astype(float).tolist(),
            }
            profile_preprocessing_sha256 = str(data["oq_profile_preprocessing_sha256"].item())
            if profile_preprocessing_sha256 != canonical_json_hash(preprocessing):
                raise ValueError("OQ profile preprocessing hash does not match stored mean/std and feature order")
            if expected_oq_profile_preprocessing_sha256 is not None and profile_preprocessing_sha256 != expected_oq_profile_preprocessing_sha256:
                raise ValueError("OQ profile preprocessing hash differs from checkpoint")
            if profile_policy == STRICT_PRE_GAME_POLICY:
                created = data["oq_profile_game_created_utc"].astype(str)
                for name in ("oq_profile_player_snapshot_time_utc", "oq_profile_opponent_snapshot_time_utc"):
                    times = data[name].astype(str)
                    for game_index in range(shape[0]):
                        for value in np.unique(times[game_index][times[game_index] != ""]):
                            if parse_utc(value) > parse_utc(created[game_index]):
                                raise ValueError("strict OQ profile policy maps a future snapshot to an older game")
            oq_profile_report = {
                "schema": profile_schema,
                "policy": profile_policy,
                "temporalLeakageAuthorized": leakage_authorized,
                "features": 31,
                "preprocessingSha256": profile_preprocessing_sha256,
                "nodeCoverageRate": float((node_valid & ~profile_missing.any(axis=-1)).sum() / max(int(node_valid.sum()), 1)),
            }
        offbook_report = None
        if require_offbook:
            offbook_report = validate_offbook_arrays(
                data,
                expected_schema=expected_offbook_schema,
                expected_source_data_sha256=expected_offbook_source_data_sha256,
                expected_source_checkpoint_sha256=expected_offbook_source_checkpoint_sha256,
                expected_records_sha256=expected_offbook_records_sha256,
                expected_materialization_sha256=expected_offbook_materialization_sha256,
            )
        return {
            "schema": "tcn-loss-model-ready-validation-v1", "ok": True,
            "dataPath": str(path.resolve()), "games": int(shape[0]), "maxSequenceLength": int(shape[1]),
            "nodes": int(valid.sum()), "inputFeatures": int(x.shape[-1]),
            "preprocessingSha256": preprocessing_sha256, "inputPolicy": input_policy,
            "splits": {name: int(np.sum(splits == name)) for name in ("train", "validation", "test")},
            "wld": {
                "minimumGlobalPlacementPly": 39,
                "labelledNodes": int(wld_available.sum()),
                "classCounts": {
                    name: int(np.sum(data["wld_class"][wld_available] == index))
                    for index, name in enumerate((
                        "class_no_wld_loss", "class_half_wld_loss", "class_full_wld_loss"
                    ))
                },
                "splitCounts": {
                    name: int(wld_available[splits == name].sum())
                    for name in ("train", "validation", "test")
                },
            },
            "oqProfile": oq_profile_report,
            "offbook": offbook_report,
        }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
