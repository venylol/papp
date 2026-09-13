#!/usr/bin/env python3
"""Materialize the independent Level18 directed off-book TCN feature.

The source NPZ already contains the checkpoint's transformed rank-1 hint6
score.  This command inverts that transform using the scale stored in the base
checkpoint, applies the shared off-book algorithm to black and white
separately, and writes a new model-ready NPZ plus complete directed and node
mapping audits.  It never modifies the source NPZ.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np


MODEL_ROOT = Path(__file__).resolve().parents[2]
TOOLKIT_ROOT = MODEL_ROOT.parents[1]
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))
if str(TOOLKIT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT / "src"))

from src.checkpoint import load_checkpoint_payload, sha256_file  # noqa: E402
from src.data_contract import validate_model_ready_npz  # noqa: E402
from src.offbook import (  # noqa: E402
    ALGORITHM_LABEL,
    OFFBOOK_ENGINE_CONTRACT,
    OFFBOOK_ENGINE_LEVEL,
    OFFBOOK_LABEL_SOURCE,
    OFFBOOK_MAX_PLY,
    OFFBOOK_MATERIALIZATION_VERSION,
    OFFBOOK_MIN_PLY,
    OFFBOOK_NORMALIZATION,
    OFFBOOK_RETROSPECTIVE_DISCLOSURE,
    OFFBOOK_SCHEMA,
    OFFBOOK_TIME_LIMIT_MS,
    array_sha256,
    canonical_json_hash,
    make_directed_record,
    offbook_contract_manifest,
    recover_level18_hint6_scores,
    validate_offbook_arrays,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-npz", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--expected-audit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-name", default="model_ready_11200_oq_profile_wld_ply39_offbook_level18.npz")
    args = parser.parse_args()
    output_name = Path(args.output_name)
    if output_name.name != args.output_name or output_name.suffix.lower() != ".npz":
        parser.error("--output-name must be a plain .npz file name")
    return args


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]] | None = None):
    handle = path.open("w", encoding="utf-8", newline="\n")
    if rows is not None:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return handle


def ids_hash(values: np.ndarray) -> str:
    body = "\n".join(sorted(str(value) for value in values)).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def load_expected_audit(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "offbook-ply-level18-hint6-expected-audit-v1":
        raise ValueError("expected-audit schema mismatch")
    required = {
        "games", "directedSideRecords", "offbook", "noOffbook",
        "bothOffbookGames", "oneOffbookGames", "noneOffbookGames",
        "anchorMinimum", "anchorMaximum", "anchorMedian",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"expected-audit missing fields: {missing}")
    return payload


def compare_expected_audit(audit: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "games", "directedSideRecords", "offbook", "noOffbook",
        "bothOffbookGames", "oneOffbookGames", "noneOffbookGames",
        "anchorMinimum", "anchorMaximum", "anchorMedian",
    )
    mismatches = {
        name: {"actual": audit.get(name), "expected": expected.get(name)}
        for name in fields if audit.get(name) != expected.get(name)
    }
    if mismatches:
        raise ValueError(f"Level18 offbook audit contract mismatch: {json.dumps(mismatches, ensure_ascii=False)}")
    return {name: audit[name] for name in fields}


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    source_path = args.input_npz.resolve()
    checkpoint_path = args.base_checkpoint.resolve()
    expected_path = args.expected_audit.resolve()
    output_dir = args.output_dir.resolve()
    output_name = args.output_name
    output_npz = output_dir / output_name
    for path in (source_path, checkpoint_path, expected_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite versioned output directory: {output_dir}")
    output_dir.mkdir(parents=True)

    source_sha = sha256_file(source_path)
    checkpoint_sha = sha256_file(checkpoint_path)
    expected = load_expected_audit(expected_path)
    checkpoint = load_checkpoint_payload(checkpoint_path)
    base_validation = validate_model_ready_npz(
        source_path,
        expected_input_features=checkpoint["input_features"],
        expected_board_channels=checkpoint["board_encoding"]["cnn_channels"],
        expected_preprocessing_sha256=canonical_json_hash(checkpoint["preprocessing"]),
        require_oq_profile=True,
    )
    board_encoding = checkpoint.get("board_encoding") or {}
    if "hint_value_scale" not in board_encoding:
        raise ValueError("base checkpoint board_encoding lacks hint_value_scale")
    hint_value_scale = float(board_encoding["hint_value_scale"])

    with np.load(source_path, allow_pickle=False) as source:
        original = {name: source[name].copy() for name in source.files}
    required = {
        "current_hint_values", "actual_thinking_time_ms", "global_placement_ply",
        "side_to_move", "player_id", "game_id", "move_index", "split", "X",
    }
    missing = sorted(required - set(original))
    if missing:
        raise ValueError(f"source NPZ missing required Level18 recovery arrays: {missing}")
    collision = sorted(set(original) & {
        "offbook_ply", "offbook_present", "offbook_feature", "offbook_schema",
    })
    if collision:
        raise ValueError(f"source NPZ already contains Level18 offbook arrays: {collision}")

    shape = original["X"].shape[:2]
    actual_node = original["global_placement_ply"] > 0
    if actual_node.shape != shape:
        raise ValueError("global_placement_ply shape differs from X")
    if np.any(actual_node & ((original["global_placement_ply"] < 1) | (original["global_placement_ply"] > OFFBOOK_MAX_PLY))):
        raise ValueError("actual nodes contain global placement ply outside 1..60")
    if np.any(~actual_node & (original["global_placement_ply"] != 0)):
        raise ValueError("non-actual padding node has nonzero global placement ply")

    recovered_scores, recovery_audit = recover_level18_hint6_scores(
        original["current_hint_values"][..., 0], actual_node, hint_value_scale,
    )
    side = original["side_to_move"].astype(str)
    players = original["player_id"].astype(str)
    game_ids = original["game_id"].astype(str)
    splits = original["split"].astype(str)
    if len(game_ids) != len(set(game_ids)):
        raise ValueError("source game_id values are not unique")
    if set(splits) - {"train", "validation", "test"}:
        raise ValueError("source split contains labels outside train/validation/test")

    offbook_ply = np.zeros(shape, dtype=np.int16)
    offbook_present = np.zeros(shape, dtype=bool)
    offbook_feature = np.zeros(shape, dtype=np.float32)
    directed_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    records_path = output_dir / "directed_offbook_records.jsonl"
    mapping_path = output_dir / "node_to_directed_offbook.jsonl"
    records_handle = write_jsonl(records_path)
    mapping_handle = write_jsonl(mapping_path)
    try:
        for game_index, game_id in enumerate(game_ids):
            game_actual = actual_node[game_index]
            if not game_actual.any():
                raise ValueError(f"game {game_id!r} has no actual nodes")
            actual_plies = original["global_placement_ply"][game_index, game_actual]
            if np.any(np.diff(actual_plies) <= 0):
                raise ValueError(f"game {game_id!r} global placement ply is not strictly increasing")
            for color in ("black", "white"):
                selected = game_actual & (side[game_index] == color)
                if not selected.any():
                    raise ValueError(f"game {game_id!r} lacks an actual {color} side record")
                player_values = set(players[game_index, selected])
                if len(player_values) != 1 or "" in player_values:
                    raise ValueError(f"game {game_id!r} {color} side does not map to one player")
                player_id = next(iter(player_values))
                indexes = np.flatnonzero(selected)
                target_nodes = [
                    {
                        "ply": int(original["global_placement_ply"][game_index, index]),
                        "move": None,
                        "playerColor": color,
                        "thinkingTimeMs": float(original["actual_thinking_time_ms"][game_index, index]),
                        "bestEval": int(recovered_scores[game_index, index]),
                    }
                    for index in indexes
                ]
                record = make_directed_record(game_id, color, player_id, target_nodes)
                record["sourceDataSha256"] = source_sha
                record["sourceCheckpointSha256"] = checkpoint_sha
                record["sourceSplit"] = str(splits[game_index])
                record["sourceGameIndex"] = int(game_index)
                key = (str(game_id), color)
                if key in directed_by_key:
                    raise ValueError(f"duplicate directed record key: {key}")
                directed_by_key[key] = record
                records_handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                anchor = record["offBookPly"]
                if anchor is not None:
                    offbook_ply[game_index, selected] = int(anchor)
                    offbook_present[game_index, selected] = True
                    offbook_feature[game_index, selected] = np.float32(int(anchor) / 60.0)
                for index in indexes:
                    mapping_handle.write(json.dumps({
                        "gameId": str(game_id),
                        "gameIndex": int(game_index),
                        "timeIndex": int(index),
                        "moveIndex": int(original["move_index"][game_index, index]),
                        "globalPlacementPly": int(original["global_placement_ply"][game_index, index]),
                        "color": color,
                        "playerId": player_id,
                        "directedRecordKey": f"{game_id}:{color}",
                        "offbookPresent": bool(offbook_present[game_index, index]),
                        "offbookPly": int(offbook_ply[game_index, index]),
                        "offbookFeature": float(offbook_feature[game_index, index]),
                    }, ensure_ascii=False, separators=(",", ":")) + "\n")
    finally:
        records_handle.close()
        mapping_handle.close()

    records_sha = sha256_file(records_path)
    mapping_sha = sha256_file(mapping_path)
    game_judgments: dict[str, list[str]] = defaultdict(list)
    anchor_values: list[int] = []
    split_stats: dict[str, dict[str, int]] = {
        name: {"games": 0, "directedSideRecords": 0, "offbook": 0, "noOffbook": 0}
        for name in ("train", "validation", "test")
    }
    color_stats: dict[str, dict[str, int]] = {
        color: {"records": 0, "offbook": 0, "noOffbook": 0}
        for color in ("black", "white")
    }
    for game_index, game_id in enumerate(game_ids):
        split = str(splits[game_index])
        split_stats[split]["games"] += 1
        for color in ("black", "white"):
            record = directed_by_key[(str(game_id), color)]
            game_judgments[str(game_id)].append(record["judgment"])
            split_stats[split]["directedSideRecords"] += 1
            color_stats[color]["records"] += 1
            if record["judgment"] == "offbook":
                split_stats[split]["offbook"] += 1
                color_stats[color]["offbook"] += 1
                anchor_values.append(int(record["offBookPly"]))
            else:
                split_stats[split]["noOffbook"] += 1
                color_stats[color]["noOffbook"] += 1

    if len(directed_by_key) != len(game_ids) * 2:
        raise ValueError("directed record count is not exactly two per game")
    audit = {
        "schema": OFFBOOK_SCHEMA,
        "games": int(len(game_ids)),
        "directedSideRecords": int(len(directed_by_key)),
        "offbook": int(len(anchor_values)),
        "noOffbook": int(len(directed_by_key) - len(anchor_values)),
        "bothOffbookGames": int(sum(all(value == "offbook" for value in values) for values in game_judgments.values())),
        "oneOffbookGames": int(sum(sum(value == "offbook" for value in values) == 1 for values in game_judgments.values())),
        "noneOffbookGames": int(sum(all(value == "no_offbook" for value in values) for values in game_judgments.values())),
        "anchorMinimum": int(min(anchor_values)) if anchor_values else None,
        "anchorMaximum": int(max(anchor_values)) if anchor_values else None,
        "anchorMedian": float(np.median(anchor_values)) if anchor_values else None,
        "anchorPlyDistribution": {
            str(ply): int(count) for ply, count in sorted(Counter(anchor_values).items())
        },
        "splitStats": split_stats,
        "colorStats": color_stats,
        "actualNodes": int(actual_node.sum()),
        "paddingNodes": int((~actual_node).sum()),
        "mappedActualNodes": int(actual_node.sum()),
        "nodeMapping": {
            "path": str(mapping_path.resolve()),
            "sha256": mapping_sha,
            "expectedRows": int(actual_node.sum()),
            "mappedRows": int(actual_node.sum()),
            "oneToOne": True,
            "paddingRowsMapped": 0,
        },
        "recovery": recovery_audit,
    }
    write_json(output_dir / "offbook_audit.json", audit)
    compare_expected_audit(audit, expected)

    source_split_hashes = {
        split: ids_hash(game_ids[splits == split])
        for split in ("train", "validation", "test")
    }
    array_hashes = {
        name: array_sha256(value)
        for name, value in {
            "offbook_ply": offbook_ply,
            "offbook_present": offbook_present,
            "offbook_feature": offbook_feature,
        }.items()
    }
    materialization_payload = {
        "version": OFFBOOK_MATERIALIZATION_VERSION,
        "contract": offbook_contract_manifest(),
        "sourceDataSha256": source_sha,
        "sourceCheckpointSha256": checkpoint_sha,
        "sourceSplitHashes": source_split_hashes,
        "recordsSha256": records_sha,
        "nodeMappingSha256": mapping_sha,
        "arrayHashes": array_hashes,
        "recovery": recovery_audit,
        "audit": audit,
    }
    materialization_sha = canonical_json_hash(materialization_payload)
    additions = {
        "offbook_ply": offbook_ply,
        "offbook_present": offbook_present,
        "offbook_feature": offbook_feature,
        "offbook_schema": np.asarray(OFFBOOK_SCHEMA),
        "offbook_label_source": np.asarray(OFFBOOK_LABEL_SOURCE),
        "offbook_algorithm_version": np.asarray(ALGORITHM_LABEL),
        "offbook_source_engine_level": np.asarray(OFFBOOK_ENGINE_LEVEL, dtype=np.int16),
        "offbook_engine_contract": np.asarray(OFFBOOK_ENGINE_CONTRACT),
        "offbook_normalization": np.asarray(OFFBOOK_NORMALIZATION),
        "offbook_time_limit_ms": np.asarray(OFFBOOK_TIME_LIMIT_MS, dtype=np.int32),
        "offbook_source_data_sha256": np.asarray(source_sha),
        "offbook_source_checkpoint_sha256": np.asarray(checkpoint_sha),
        "offbook_records_sha256": np.asarray(records_sha),
        "offbook_materialization_sha256": np.asarray(materialization_sha),
        "offbook_retrospective_disclosure": np.asarray(OFFBOOK_RETROSPECTIVE_DISCLOSURE),
    }
    arrays = {**original, **additions}
    temporary_npz = output_npz.with_suffix(".tmp.npz")
    np.savez_compressed(temporary_npz, **arrays)
    temporary_npz.replace(output_npz)
    with np.load(output_npz, allow_pickle=False) as written:
        for name, value in original.items():
            same = (
                np.array_equal(written[name], value, equal_nan=True)
                if value.dtype.kind in {"f", "c"}
                else np.array_equal(written[name], value)
            )
            if not same:
                raise AssertionError(f"source array changed during Level18 materialization: {name}")
        contract_report = validate_offbook_arrays(
            written,
            expected_source_data_sha256=source_sha,
            expected_source_checkpoint_sha256=checkpoint_sha,
            expected_records_sha256=records_sha,
            expected_materialization_sha256=materialization_sha,
        )
    validation = validate_model_ready_npz(
        output_npz,
        expected_input_features=checkpoint["input_features"],
        expected_board_channels=checkpoint["board_encoding"]["cnn_channels"],
        expected_preprocessing_sha256=canonical_json_hash(checkpoint["preprocessing"]),
        require_oq_profile=True,
        require_offbook=True,
        expected_offbook_source_data_sha256=source_sha,
        expected_offbook_source_checkpoint_sha256=checkpoint_sha,
        expected_offbook_records_sha256=records_sha,
        expected_offbook_materialization_sha256=materialization_sha,
    )
    write_json(output_dir / "model_ready_validation.json", validation)
    manifest = {
        "schema": "tcn-loss-level18-offbook-materialization-manifest-v1",
        "status": "completed",
        "createdAtUtc": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "contract": offbook_contract_manifest(),
        "inputs": {
            "sourceModelReadyNpz": {"path": str(source_path), "sha256": source_sha},
            "baseCheckpoint": {"path": str(checkpoint_path), "sha256": checkpoint_sha},
            "expectedAudit": {"path": str(expected_path), "sha256": sha256_file(expected_path)},
        },
        "sourceValidation": base_validation,
        "sourceSplitHashes": source_split_hashes,
        "hintValueScale": hint_value_scale,
        "recovery": recovery_audit,
        "audit": audit,
        "artifacts": {
            "directedRecords": {"path": str(records_path.resolve()), "sha256": records_sha},
            "nodeMapping": {"path": str(mapping_path.resolve()), "sha256": mapping_sha},
            "audit": {"path": str((output_dir / "offbook_audit.json").resolve()), "sha256": sha256_file(output_dir / "offbook_audit.json")},
            "modelReadyValidation": {"path": str((output_dir / "model_ready_validation.json").resolve()), "sha256": sha256_file(output_dir / "model_ready_validation.json")},
            "modelReadyNpz": {"path": str(output_npz.resolve()), "sha256": sha256_file(output_npz)},
        },
        "arrayHashes": array_hashes,
        "materializationPayload": materialization_payload,
        "materializationSha256": materialization_sha,
        "validation": validation,
        "retrospectiveDisclosure": OFFBOOK_RETROSPECTIVE_DISCLOSURE,
        "elapsedSeconds": round(time.time() - started, 3),
    }
    write_json(output_dir / "materialization_manifest.json", manifest)
    return manifest


def main() -> int:
    args = parse_args()
    manifest = materialize(args)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
