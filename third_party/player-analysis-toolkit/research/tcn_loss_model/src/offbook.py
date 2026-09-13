"""Level18 TCN off-book feature contract and shared detection adapters."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np


TOOLKIT_ROOT = Path(__file__).resolve().parents[3]
TOOLKIT_SRC = TOOLKIT_ROOT / "src"
if str(TOOLKIT_SRC) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_SRC))

from player_analysis_toolkit.offbook_core import (  # noqa: E402
    ALGORITHM_LABEL,
    algorithm_contract,
    detect_target_offbook,
)


OFFBOOK_SCHEMA = "offbook-ply-level18-hint6-v1"
OFFBOOK_LABEL_SOURCE = OFFBOOK_SCHEMA
OFFBOOK_ENGINE_LEVEL = 18
OFFBOOK_ENGINE_CONTRACT = "egaroucid-level18-book-threads16-hash25-hint6-rank1-v1"
OFFBOOK_TIME_LIMIT_MS = 300000
OFFBOOK_MIN_PLY = 5
OFFBOOK_MAX_PLY = 60
OFFBOOK_NORMALIZATION = "offbook_ply / 60.0"
OFFBOOK_RETROSPECTIVE_DISCLOSURE = "retrospective-whole-game-anchor-repeated-on-earlier-nodes-v1"
OFFBOOK_MATERIALIZATION_VERSION = "level18-offbook-materialization-v1"

OFFBOOK_ARRAYS = frozenset({
    "offbook_ply", "offbook_present", "offbook_feature",
    "offbook_schema", "offbook_label_source", "offbook_algorithm_version",
    "offbook_source_engine_level", "offbook_engine_contract",
    "offbook_normalization", "offbook_time_limit_ms",
    "offbook_source_data_sha256", "offbook_source_checkpoint_sha256",
    "offbook_records_sha256", "offbook_materialization_sha256",
    "offbook_retrospective_disclosure",
})


def canonical_json_hash(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(repr(array.shape).encode("ascii"))
    digest.update(array.tobytes())
    return digest.hexdigest()


def recover_level18_hint6_scores(
    current_hint_values_rank1: np.ndarray,
    actual_node: np.ndarray,
    hint_value_scale: float,
    *,
    integer_tolerance: float = 1.0e-3,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Invert the checkpoint's rank-1 tanh transform without a scale constant."""

    values = np.asarray(current_hint_values_rank1)
    actual = np.asarray(actual_node).astype(bool)
    if values.shape != actual.shape:
        raise ValueError(f"rank-1 hint values and actual-node mask differ: {values.shape} != {actual.shape}")
    if not np.isfinite(hint_value_scale) or hint_value_scale <= 0:
        raise ValueError(f"hint_value_scale must be finite and positive, got {hint_value_scale!r}")
    if not np.isfinite(values[actual]).all():
        raise ValueError("actual nodes contain non-finite current_hint_values[..., 0]")
    if np.any(values[actual] <= -1.0) or np.any(values[actual] >= 1.0):
        raise ValueError("actual nodes contain a rank-1 tanh value outside the open (-1, 1) interval")
    recovered = np.zeros(values.shape, dtype=np.float64)
    recovered[actual] = np.arctanh(values[actual].astype(np.float64)) * float(hint_value_scale)
    rounded = np.rint(recovered)
    error = np.abs(recovered[actual] - rounded[actual])
    max_error = float(error.max()) if error.size else 0.0
    if max_error > integer_tolerance:
        raise ValueError(
            "recovered Level18 rank-1 scores are not close to integers: "
            f"max_error={max_error} tolerance={integer_tolerance}"
        )
    result = np.zeros(values.shape, dtype=np.int16)
    result[actual] = rounded[actual].astype(np.int16)
    return result, {
        "hintValueScale": float(hint_value_scale),
        "integerTolerance": float(integer_tolerance),
        "actualNodes": int(actual.sum()),
        "nonFiniteRecovered": int((~np.isfinite(recovered[actual])).sum()),
        "maxAbsoluteDistanceToNearestInteger": max_error,
        "recoveredMinimum": int(result[actual].min()) if actual.any() else None,
        "recoveredMaximum": int(result[actual].max()) if actual.any() else None,
    }


def make_directed_record(
    game_id: str,
    color: str,
    player_id: str,
    target_nodes: list[dict[str, Any]],
    *,
    time_limit_ms: int = OFFBOOK_TIME_LIMIT_MS,
) -> dict[str, Any]:
    if color not in {"black", "white"}:
        raise ValueError(f"invalid directed offbook color: {color!r}")
    if not str(player_id).strip():
        raise ValueError(f"game {game_id!r} {color} directed record has a blank player")
    return detect_target_offbook(
        game_id,
        color,
        target_nodes,
        time_limit_ms,
        label_source=OFFBOOK_LABEL_SOURCE,
        record_schema=OFFBOOK_SCHEMA,
        extra_record_fields={
            "playerId": str(player_id),
            "sourceEngineLevel": OFFBOOK_ENGINE_LEVEL,
            "engineContract": OFFBOOK_ENGINE_CONTRACT,
            "normalization": OFFBOOK_NORMALIZATION,
            "retrospectiveDisclosure": OFFBOOK_RETROSPECTIVE_DISCLOSURE,
        },
    )


def _scalar(data: Mapping[str, Any], name: str) -> Any:
    value = np.asarray(data[name])
    if value.shape != ():
        raise ValueError(f"{name} must be a scalar NPZ value, got shape {value.shape}")
    return value.item()


def _validate_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValueError(f"{name} must be a 64-character SHA-256 hex digest")


def validate_offbook_arrays(
    data: Mapping[str, Any],
    *,
    expected_schema: str = OFFBOOK_SCHEMA,
    expected_source_data_sha256: str | None = None,
    expected_source_checkpoint_sha256: str | None = None,
    expected_records_sha256: str | None = None,
    expected_materialization_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate the independent arrays and their game/color/player mapping."""

    missing = sorted(OFFBOOK_ARRAYS - set(data))
    if missing:
        raise ValueError(f"model-ready NPZ missing required Level18 offbook arrays: {missing}")
    if "X" not in data or "global_placement_ply" not in data:
        raise ValueError("Level18 offbook validation requires X and global_placement_ply")
    shape = np.asarray(data["X"]).shape[:2]
    expected_shapes = {
        "offbook_ply": shape, "offbook_present": shape, "offbook_feature": shape,
        "player_id": shape, "side_to_move": shape, "global_placement_ply": shape,
    }
    for name, expected in expected_shapes.items():
        if np.asarray(data[name]).shape != expected:
            raise ValueError(f"{name} expected shape {expected}, got {np.asarray(data[name]).shape}")
    if np.asarray(data["offbook_ply"]).dtype.kind not in "iu":
        raise ValueError("offbook_ply must be an integer audit array")
    if np.asarray(data["offbook_present"]).dtype.kind != "b":
        raise ValueError("offbook_present must be a bool audit array")
    if np.asarray(data["offbook_feature"]).dtype.kind not in "fc":
        raise ValueError("offbook_feature must be a floating-point model input array")
    ply = np.asarray(data["offbook_ply"])
    present = np.asarray(data["offbook_present"]).astype(bool)
    feature = np.asarray(data["offbook_feature"], dtype=np.float32)
    actual = np.asarray(data["global_placement_ply"]) > 0
    padding = ~actual
    if not np.isfinite(feature).all():
        raise ValueError("offbook_feature contains NaN or Inf")
    if np.any(ply[padding] != 0) or np.any(present[padding]) or np.any(feature[padding] != 0):
        raise ValueError("padding nodes carry nonzero or present Level18 offbook values")
    if np.any(~present & (ply != 0)):
        raise ValueError("offbook_present=false but offbook_ply is nonzero")
    if np.any(present & ((ply < OFFBOOK_MIN_PLY) | (ply > OFFBOOK_MAX_PLY))):
        raise ValueError("offbook_present=true but offbook_ply is outside 5..60")
    if not np.allclose(feature, ply.astype(np.float32) / 60.0, rtol=0, atol=1.0e-7):
        raise ValueError("offbook_feature is not exactly the offbook_ply / 60.0 normalization")
    if np.any(~present & (feature != 0)):
        raise ValueError("offbook_present=false but offbook_feature is nonzero")

    side = np.asarray(data["side_to_move"]).astype(str)
    players = np.asarray(data["player_id"]).astype(str)
    if np.any(actual & ~np.isin(side, ("black", "white"))):
        raise ValueError("actual nodes contain invalid side_to_move values")
    game_ids = np.asarray(data["game_id"]).astype(str)
    if game_ids.shape != (shape[0],) or len(set(game_ids)) != len(game_ids):
        raise ValueError("offbook validation requires unique one-per-sequence game_id values")
    mapping_rows = 0
    for game_index, game_id in enumerate(game_ids):
        for color in ("black", "white"):
            selected = actual[game_index] & (side[game_index] == color)
            if not selected.any():
                raise ValueError(f"game {game_id!r} has no actual {color} side nodes")
            player_values = set(players[game_index, selected])
            if len(player_values) != 1 or "" in player_values:
                raise ValueError(f"game {game_id!r} {color} nodes do not map to one nonblank player")
            side_ply = set(ply[game_index, selected].tolist())
            side_present = set(present[game_index, selected].tolist())
            if len(side_ply) != 1 or len(side_present) != 1:
                raise ValueError(f"game {game_id!r} {color} nodes do not repeat one directed anchor")
            mapping_rows += 1
    scalar_expectations = {
        "offbook_schema": expected_schema,
        "offbook_label_source": OFFBOOK_LABEL_SOURCE,
        "offbook_algorithm_version": ALGORITHM_LABEL,
        "offbook_source_engine_level": OFFBOOK_ENGINE_LEVEL,
        "offbook_engine_contract": OFFBOOK_ENGINE_CONTRACT,
        "offbook_normalization": OFFBOOK_NORMALIZATION,
        "offbook_time_limit_ms": OFFBOOK_TIME_LIMIT_MS,
        "offbook_retrospective_disclosure": OFFBOOK_RETROSPECTIVE_DISCLOSURE,
    }
    for name, expected in scalar_expectations.items():
        actual_value = _scalar(data, name)
        if actual_value != expected:
            raise ValueError(f"{name} contract mismatch: expected {expected!r}, got {actual_value!r}")
    hash_expectations = {
        "offbook_source_data_sha256": expected_source_data_sha256,
        "offbook_source_checkpoint_sha256": expected_source_checkpoint_sha256,
        "offbook_records_sha256": expected_records_sha256,
        "offbook_materialization_sha256": expected_materialization_sha256,
    }
    for name, expected in hash_expectations.items():
        actual_value = str(_scalar(data, name))
        _validate_sha256(actual_value, name)
        if expected is not None and actual_value != expected:
            raise ValueError(f"{name} contract mismatch: expected {expected!r}, got {actual_value!r}")
    return {
        "schema": expected_schema,
        "games": int(shape[0]),
        "actualNodes": int(actual.sum()),
        "paddingNodes": int(padding.sum()),
        "directedSideRecords": mapping_rows,
        "offbookNodes": int(present[actual].sum()),
        "noOffbookNodes": int((actual & ~present).sum()),
        "offbookPlyMinimum": int(ply[present].min()) if present.any() else None,
        "offbookPlyMaximum": int(ply[present].max()) if present.any() else None,
        "sourceDataSha256": str(_scalar(data, "offbook_source_data_sha256")),
        "sourceCheckpointSha256": str(_scalar(data, "offbook_source_checkpoint_sha256")),
        "recordsSha256": str(_scalar(data, "offbook_records_sha256")),
        "materializationSha256": str(_scalar(data, "offbook_materialization_sha256")),
    }


def offbook_contract_manifest() -> dict[str, Any]:
    return {
        "schema": OFFBOOK_SCHEMA,
        "labelSource": OFFBOOK_LABEL_SOURCE,
        "algorithm": algorithm_contract(),
        "sourceEngineLevel": OFFBOOK_ENGINE_LEVEL,
        "engineContract": OFFBOOK_ENGINE_CONTRACT,
        "normalization": OFFBOOK_NORMALIZATION,
        "minimumAnchorPly": OFFBOOK_MIN_PLY,
        "maximumAnchorPly": OFFBOOK_MAX_PLY,
        "retrospectiveDisclosure": OFFBOOK_RETROSPECTIVE_DISCLOSURE,
        "materializationVersion": OFFBOOK_MATERIALIZATION_VERSION,
    }
