#!/usr/bin/env python3
"""Add audited pass-safe WLD labels to an existing model-ready NPZ."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.checkpoint import sha256_file
from src.data_contract import validate_model_ready_npz


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-name", default="model_ready_11200_oq_profile_wld_ply39.npz")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing non-empty output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = torch.load(args.base_checkpoint, map_location="cpu", weights_only=False)
    board_encoding = checkpoint.get("board_encoding", {})
    scale = board_encoding.get("hint_value_scale")
    transform = board_encoding.get("hint_value_transform")
    if not isinstance(scale, (int, float)) or not np.isfinite(scale) or scale <= 0:
        raise ValueError("base checkpoint lacks a finite positive hint_value_scale")
    if not isinstance(transform, str) or "tanh(value / scale)" not in transform:
        raise ValueError("base checkpoint hint value transform is not the required tanh encoding")

    with np.load(args.input, allow_pickle=False) as source:
        arrays = {name: source[name] for name in source.files}
    shape = arrays["mask"].shape
    node_valid = arrays["global_placement_ply"] > 0
    rank1_token_valid = arrays["current_hint_tokens"][..., 0] > 0
    missing_rank1 = node_valid & ~rank1_token_valid
    if np.any(missing_rank1):
        raise ValueError(f"valid nodes without rank-1 hint score token: {int(missing_rank1.sum())}")
    encoded = arrays["current_hint_values"][..., 0].astype(np.float64)
    if np.any(np.abs(encoded[node_valid]) >= 1):
        raise ValueError("rank-1 hint encoding cannot be inverted because abs(value) >= 1")
    recovered = np.arctanh(encoded) * float(scale)
    rounded = np.rint(recovered)
    errors = np.abs(recovered - rounded)
    max_error = float(errors[node_valid].max(initial=0.0))
    tolerance = 1e-4
    if max_error > tolerance:
        raise ValueError(f"hint score integer recovery unstable: max error {max_error} > {tolerance}")

    current_score = np.zeros(shape, dtype=np.float32)
    current_score[node_valid] = rounded[node_valid].astype(np.float32)
    actual_move_score = np.zeros(shape, dtype=np.float32)
    has_next = np.zeros(shape, dtype=bool)
    has_next[:, :-1] = node_valid[:, :-1] & node_valid[:, 1:]
    next_score = np.zeros(shape, dtype=np.float32)
    next_score[:, :-1] = current_score[:, 1:]
    same_side = arrays["same_side_after_move"].astype(bool)
    actual_move_score[has_next] = np.where(
        same_side[has_next], next_score[has_next], -next_score[has_next]
    )
    before_rank = np.where(current_score > 0, 2, np.where(current_score < 0, 0, 1))
    after_rank = np.where(actual_move_score > 0, 2, np.where(actual_move_score < 0, 0, 1))
    wld_class = np.maximum(0, before_rank - after_rank).astype(np.int8)
    wld_available = (
        arrays["mask"].astype(bool)
        & arrays["label_available"].astype(bool)
        & arrays["child_continuity_ok"].astype(bool)
        & has_next
        & (arrays["global_placement_ply"] >= 39)
    )
    wld_class[~wld_available] = 0
    wld_loss = (wld_class.astype(np.float32) / 2.0)
    wld_loss[~wld_available] = 0
    arrays.update({
        "current_score": current_score,
        "actual_move_score": actual_move_score,
        "wld_class": wld_class,
        "wld_loss": wld_loss,
        "wld_label_available": wld_available,
    })
    output = args.output_dir / args.output_name
    temporary = output.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, **arrays)
    temporary.replace(output)
    board_perspective = (
        str(arrays["board_perspective"].item())
        if "board_perspective" in arrays else None
    )
    validation = validate_model_ready_npz(
        output,
        require_oq_profile="oq_profile_features" in arrays,
        expected_board_perspective=board_perspective,
    )

    splits = arrays["split"].astype(str)
    class_names = ("class_no_wld_loss", "class_half_wld_loss", "class_full_wld_loss")
    split_distribution = {}
    for split in ("train", "validation", "test"):
        selected = wld_available[splits == split]
        values = wld_class[splits == split][selected]
        split_distribution[split] = {
            "nodes": int(selected.sum()),
            "classCounts": {name: int(np.sum(values == i)) for i, name in enumerate(class_names)},
        }
    manifest = {
        "schema": "tcn-wld-model-ready-materialization-v1",
        "status": "complete",
        "boardPerspective": board_perspective,
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceData": str(args.input.resolve()),
        "sourceDataSha256": sha256_file(args.input),
        "outputData": str(output.resolve()),
        "outputDataSha256": sha256_file(output),
        "scoreRecovery": {
            "source": "current_hint_values[...,0]",
            "checkpoint": str(args.base_checkpoint.resolve()),
            "checkpointSha256": sha256_file(args.base_checkpoint),
            "scaleMetadataPath": "board_encoding.hint_value_scale",
            "scale": float(scale),
            "transform": transform,
            "validNodesChecked": int(node_valid.sum()),
            "integerRecoveryTolerance": tolerance,
            "maximumAbsoluteIntegerRecoveryError": max_error,
            "allValidNodesRecoveredToIntegers": True,
        },
        "contract": {
            "minimumGlobalPlacementPlyInclusive": 39,
            "passExcludedPly": True,
            "normalTurnActualMoveScore": "-next_best_score",
            "sameSideAfterPassActualMoveScore": "next_best_score",
            "classes": list(class_names),
        },
        "wldClassCounts": {
            name: int(np.sum(wld_class[wld_available] == i)) for i, name in enumerate(class_names)
        },
        "splitDistribution": split_distribution,
        "validation": validation,
    }
    manifest_path = args.output_dir / "wld_materialization_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
