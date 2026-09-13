#!/usr/bin/env python3
"""Create a new deterministic whole-game model-ready NPZ subset for smoke tests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--games-per-split", type=int, default=12)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    with np.load(args.input, allow_pickle=False) as source:
        game_count = len(source["game_id"])
        split = source["split"].astype(str)
        selected = np.concatenate([
            np.flatnonzero(split == name)[:args.games_per_split]
            for name in ("train", "validation", "test")
        ])
        if len(selected) != args.games_per_split * 3:
            raise ValueError("input does not contain enough games in every split")
        arrays = {
            name: (source[name][selected].copy() if source[name].ndim > 0 and source[name].shape[0] == game_count else source[name].copy())
            for name in source.files
        }
    output = args.output_dir / "model_ready_smoke.npz"
    np.savez_compressed(output, **arrays)
    manifest = {
        "schema": "model-ready-whole-game-smoke-subset-v1",
        "input": str(args.input.resolve()),
        "inputSha256": sha256_file(args.input),
        "output": str(output.resolve()),
        "outputSha256": sha256_file(output),
        "gamesPerSplit": args.games_per_split,
        "selectedGameIds": arrays["game_id"].astype(str).tolist(),
        "splits": {name: int(np.sum(arrays["split"].astype(str) == name)) for name in ("train", "validation", "test")},
    }
    (args.output_dir / "subset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
