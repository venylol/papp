#!/usr/bin/env python3
"""Final CPU-only validation for a server-prepared TCN data bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.checkpoint import load_checkpoint_payload  # noqa: E402
from src.data_contract import validate_model_ready_npz  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--preprocessing", required=True, type=Path)
    parser.add_argument("--materialization-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    checkpoint = load_checkpoint_payload(args.checkpoint.resolve())
    preprocessing_hash = sha256_file(args.preprocessing.resolve())
    report = validate_model_ready_npz(
        args.data.resolve(),
        expected_input_dim=int(checkpoint["input_dim"]),
        expected_input_features=list(checkpoint["input_features"]),
        expected_board_channels=list(checkpoint["board_cnn_channels"]),
        expected_preprocessing_sha256=preprocessing_hash,
    )
    materialization = json.loads(args.materialization_manifest.read_text(encoding="utf-8"))
    if not materialization.get("ok") or not materialization.get("validation", {}).get("ok"):
        raise ValueError("materialization manifest is not passing")
    with np.load(args.data, allow_pickle=False) as data:
        feature_count = len(data["input_features"])
        board_channel_count = len(data["board_cnn_channels"])
    if feature_count != 362 or board_channel_count != 23:
        raise ValueError(f"TCN shape contract failed: features={feature_count}, boardChannels={board_channel_count}")
    if not args.context.is_file() or args.context.stat().st_size == 0:
        raise ValueError("position context metadata is missing or empty")
    final = {
        "schema": "oq-tcn-server-data-ready-v1",
        "ok": True,
        "trainingStarted": False,
        "cudaRequired": False,
        "dataPath": str(args.data.resolve()),
        "dataSha256": sha256_file(args.data.resolve()),
        "contextPath": str(args.context.resolve()),
        "contextSha256": sha256_file(args.context.resolve()),
        "checkpointSha256": sha256_file(args.checkpoint.resolve()),
        "preprocessingSha256": preprocessing_hash,
        "inputFeatures": feature_count,
        "boardChannels": board_channel_count,
        "validation": report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
