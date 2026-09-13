#!/usr/bin/env python3
"""Derive a personal model-ready NPZ with an explicit control/reported split."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reported-game", action="append", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite: {args.output}")
    reported = set(args.reported_game)
    if len(reported) != len(args.reported_game):
        raise ValueError("reported game IDs must be unique")
    with np.load(args.input, allow_pickle=False) as source:
        game_ids = source["game_id"].astype(str)
        missing = sorted(reported - set(game_ids))
        if missing:
            raise ValueError(f"reported games absent from input: {missing}")
        if len(reported) == len(game_ids):
            raise ValueError("at least one control game is required")
        arrays = {name: source[name] for name in source.files}
    arrays["split"] = np.asarray(
        ["test" if game_id in reported else "train" for game_id in game_ids],
        dtype=arrays["split"].dtype,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + f".{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    os.replace(temporary, args.output)
    report = {
        "schema": "personal-inference-split-derivation-v1",
        "status": "completed",
        "input": str(args.input.resolve()),
        "inputSha256": sha256_file(args.input),
        "output": str(args.output.resolve()),
        "outputSha256": sha256_file(args.output),
        "gameCount": int(len(game_ids)),
        "controlGameCount": int(len(game_ids) - len(reported)),
        "reportedGameCount": int(len(reported)),
        "reportedGameIds": sorted(reported),
        "changedField": "split",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
