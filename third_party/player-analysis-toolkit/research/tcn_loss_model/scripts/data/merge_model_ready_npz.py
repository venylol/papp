#!/usr/bin/env python3
"""Merge two disjoint, already validated model-ready NPZ cohorts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np


CONSTANT_ARRAYS = {"input_features", "board_cnn_channels", "preprocessing_sha256", "input_policy"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def context_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"context has no header: {path}")
        return list(reader.fieldnames), list(reader)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-data", required=True, type=Path)
    parser.add_argument("--extension-data", required=True, type=Path)
    parser.add_argument("--base-context", required=True, type=Path)
    parser.add_argument("--extension-context", required=True, type=Path)
    parser.add_argument("--base-games", required=True, type=Path)
    parser.add_argument("--extension-games", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-name", default="model_ready_11200.npz")
    parser.add_argument("--expected-base-games", required=True, type=int)
    parser.add_argument("--expected-extension-games", required=True, type=int)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    output.mkdir(parents=True)
    final_path = output / args.output_name
    temporary = output / f"{args.output_name}.partial"
    started = time.time()
    with np.load(args.base_data.resolve(), allow_pickle=False) as base, np.load(
        args.extension_data.resolve(), allow_pickle=False
    ) as extension:
        if set(base.files) != set(extension.files):
            raise ValueError("base and extension NPZ array sets differ")
        base_games = base["game_id"].astype(str)
        extension_games = extension["game_id"].astype(str)
        if len(base_games) != args.expected_base_games or len(extension_games) != args.expected_extension_games:
            raise ValueError("base or extension game count differs from contract")
        if len(set(base_games)) != len(base_games) or len(set(extension_games)) != len(extension_games):
            raise ValueError("a source NPZ contains duplicate game IDs")
        overlap = set(base_games) & set(extension_games)
        if overlap:
            raise ValueError(f"base/extension game overlap: {sorted(overlap)[:10]}")
        arrays: dict[str, np.ndarray] = {}
        for name in base.files:
            left, right = base[name], extension[name]
            if name in CONSTANT_ARRAYS:
                if not np.array_equal(left, right):
                    raise ValueError(f"constant model contract differs: {name}")
                arrays[name] = left.copy()
                continue
            if left.ndim == 0 or right.ndim == 0 or left.shape[0] != len(base_games) or right.shape[0] != len(extension_games):
                raise ValueError(f"unclassified non-game array: {name} {left.shape} {right.shape}")
            if left.shape[1:] != right.shape[1:]:
                raise ValueError(f"per-game array shape differs: {name} {left.shape} {right.shape}")
            arrays[name] = np.concatenate([left, right], axis=0)
        np.savez_compressed(temporary, **arrays)
    generated = temporary.with_suffix(temporary.suffix + ".npz") if not temporary.name.endswith(".npz") else temporary
    os.replace(generated, final_path)

    base_fields, base_rows = context_rows(args.base_context.resolve())
    extension_fields, extension_rows = context_rows(args.extension_context.resolve())
    if base_fields != extension_fields:
        raise ValueError("base and extension context schemas differ")
    keys = [(row["game_id"], int(row["move_index"])) for row in base_rows + extension_rows]
    if len(keys) != len(set(keys)):
        raise ValueError("combined context contains duplicate exact node keys")
    context_path = output / "position_context_metadata.csv"
    with context_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=base_fields)
        writer.writeheader()
        writer.writerows(base_rows)
        writer.writerows(extension_rows)

    base_game_fields, base_game_rows = context_rows(args.base_games.resolve())
    extension_game_fields, extension_game_rows = context_rows(args.extension_games.resolve())
    if base_game_fields != extension_game_fields:
        raise ValueError("base and extension game metadata schemas differ")
    base_game_ids = [row["game_id"] for row in base_game_rows]
    extension_game_ids = [row["game_id"] for row in extension_game_rows]
    if set(base_game_ids) != set(base_games) or set(extension_game_ids) != set(extension_games):
        raise ValueError("NPZ and authoritative game metadata memberships differ")
    if len(base_game_ids + extension_game_ids) != len(set(base_game_ids + extension_game_ids)):
        raise ValueError("combined game metadata contains duplicate game IDs")
    games_path = output / "games.csv"
    with games_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=base_game_fields)
        writer.writeheader()
        writer.writerows(base_game_rows)
        writer.writerows(extension_game_rows)

    manifest = {
        "schema": "oq-model-ready-disjoint-merge-v1", "ok": True,
        "validation": {"ok": True}, "trainingStarted": False, "cudaUsed": False,
        "baseGames": len(base_games), "extensionGames": len(extension_games),
        "combinedGames": len(base_games) + len(extension_games), "gameOverlap": 0,
        "baseDataSha256": sha256_file(args.base_data.resolve()),
        "extensionDataSha256": sha256_file(args.extension_data.resolve()),
        "outputData": str(final_path), "outputDataSha256": sha256_file(final_path),
        "contextRows": len(keys), "contextSha256": sha256_file(context_path),
        "gamesMetadata": str(games_path), "gamesMetadataSha256": sha256_file(games_path),
        "elapsedSeconds": time.time() - started,
    }
    (output / "materialization_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
