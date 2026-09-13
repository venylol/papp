#!/usr/bin/env python3
"""Create a new model-ready NPZ with an official-style grouped train/test split."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.model_selection import GroupShuffleSplit


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ids_hash(values: np.ndarray) -> str:
    body = "\n".join(sorted(values.astype(str).tolist())).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-name", default="model_ready_10000_split90_10.npz")
    parser.add_argument("--test-size", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    source_path = args.data.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    output_dir.mkdir(parents=True)
    output_path = output_dir / args.output_name

    with np.load(source_path, allow_pickle=False) as source:
        game_ids = source["game_id"].astype(str)
        if len(set(game_ids.tolist())) != len(game_ids):
            raise ValueError("source contains duplicate game_id values")
        splitter = GroupShuffleSplit(
            n_splits=1,
            test_size=args.test_size,
            random_state=args.seed,
        )
        train_indexes, test_indexes = next(
            splitter.split(np.zeros(len(game_ids), dtype=np.uint8), groups=game_ids)
        )
        split = np.full(len(game_ids), "train", dtype="U10")
        split[test_indexes] = "test"
        arrays = {name: source[name] for name in source.files}
        original_split = arrays["split"].astype(str).copy()
        arrays["split"] = split
        np.savez_compressed(output_path, **arrays)

    with np.load(source_path, allow_pickle=False) as source, np.load(
        output_path, allow_pickle=False
    ) as output:
        unchanged = all(
            name == "split" or np.array_equal(source[name], output[name])
            for name in source.files
        )
        if set(source.files) != set(output.files) or not unchanged:
            raise ValueError("an array other than split changed during resplitting")
        output_split = output["split"].astype(str)

    counts = {
        "train": int(np.sum(output_split == "train")),
        "test": int(np.sum(output_split == "test")),
    }
    expected_test = int(round(len(game_ids) * args.test_size))
    if counts["test"] != expected_test or counts["train"] + counts["test"] != len(game_ids):
        raise ValueError(f"unexpected split counts: {counts}")

    manifest = {
        "schema": "oq-model-ready-official-grouped-train-test-split-v1",
        "ok": True,
        "sourceData": str(source_path),
        "sourceDataSha256": sha256_file(source_path),
        "outputData": str(output_path),
        "outputDataSha256": sha256_file(output_path),
        "method": "sklearn.model_selection.GroupShuffleSplit grouped by unique game_id",
        "seed": args.seed,
        "testSize": args.test_size,
        "games": len(game_ids),
        "originalSplitCounts": {
            name: int(np.sum(original_split == name))
            for name in sorted(set(original_split.tolist()))
        },
        "splitCounts": counts,
        "trainGameIdsSha256": ids_hash(game_ids[train_indexes]),
        "testGameIdsSha256": ids_hash(game_ids[test_indexes]),
        "arraysUnchangedExceptSplit": unchanged,
    }
    manifest_path = output_dir / "split_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
