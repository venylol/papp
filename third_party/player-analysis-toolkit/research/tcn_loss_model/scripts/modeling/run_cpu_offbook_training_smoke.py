#!/usr/bin/env python3
"""Run a tiny CPU optimization smoke on a real Level18 model-ready subset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.checkpoint import load_transferred_offbook_profile_model, sha256_file  # noqa: E402
from src.data_contract import validate_model_ready_npz  # noqa: E402
from src.offbook import OFFBOOK_SCHEMA  # noqa: E402
from src.training import SequenceDataset, _move, _model_output  # noqa: E402
from src.model import multitask_loss  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-games", type=int, default=4)
    parser.add_argument("--validation-games", type=int, default=2)
    return parser.parse_args()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite smoke output directory: {args.output_dir}")
    if args.train_games <= 0 or args.validation_games <= 0:
        raise ValueError("smoke game counts must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with np.load(args.data, allow_pickle=False) as source:
        split = source["split"].astype(str)
        train_indexes = np.flatnonzero(split == "train")[:args.train_games]
        validation_indexes = np.flatnonzero(split == "validation")[:args.validation_games]
        selected = np.concatenate((train_indexes, validation_indexes))
        if len(train_indexes) != args.train_games or len(validation_indexes) != args.validation_games:
            raise ValueError("source data does not contain enough train/validation games for smoke")
        arrays = {name: source[name][selected].copy() if source[name].ndim >= 1 and source[name].shape[0] == len(split) else source[name].copy() for name in source.files}
        arrays["split"] = np.asarray(["train"] * len(train_indexes) + ["validation"] * len(validation_indexes), dtype="U10")
    smoke_data = args.output_dir / "smoke_model_ready.npz"
    np.savez_compressed(smoke_data, **arrays)
    validation = validate_model_ready_npz(
        smoke_data,
        require_oq_profile=True,
        require_offbook=True,
        expected_offbook_schema=OFFBOOK_SCHEMA,
        expected_offbook_source_checkpoint_sha256=sha256_file(args.base_checkpoint),
    )
    model, _ = load_transferred_offbook_profile_model(args.base_checkpoint, "full-31")
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1.0e-4)
    train_loader = DataLoader(
        SequenceDataset(smoke_data, "train", require_oq_profile=True, require_offbook=True),
        batch_size=max(1, min(2, args.train_games)), shuffle=False, num_workers=0,
    )
    validation_loader = DataLoader(
        SequenceDataset(smoke_data, "validation", require_oq_profile=True, require_offbook=True),
        batch_size=args.validation_games, shuffle=False, num_workers=0,
    )
    train_losses = []
    for batch in train_loader:
        batch = _move(batch, torch.device("cpu"))
        optimizer.zero_grad(set_to_none=True)
        output = _model_output(model, batch, use_oq_profile=True, use_offbook=True)
        losses = multitask_loss(
            output, batch["actual_thinking_time_ms"].float(), batch["severity_class"].float(), batch["mask"],
            wld_class=batch["wld_class"].float(), wld_label_available=batch["wld_label_available"],
            global_placement_ply=batch["global_placement_ply"],
        )
        if not bool(torch.isfinite(losses["total"])):
            raise ValueError("CPU smoke optimization produced a non-finite loss")
        losses["total"].backward()
        optimizer.step()
        train_losses.append(float(losses["total"].detach()))
    model.eval()
    with torch.no_grad():
        validation_batch = _move(next(iter(validation_loader)), torch.device("cpu"))
        validation_output = _model_output(model, validation_batch, use_oq_profile=True, use_offbook=True)
        validation_losses = multitask_loss(
            validation_output, validation_batch["actual_thinking_time_ms"].float(),
            validation_batch["severity_class"].float(), validation_batch["mask"],
            wld_class=validation_batch["wld_class"].float(),
            wld_label_available=validation_batch["wld_label_available"],
            global_placement_ply=validation_batch["global_placement_ply"],
        )
    report = {
        "schema": "tcn-loss-level18-offbook-cpu-training-smoke-v1",
        "ok": True,
        "device": "cpu",
        "sourceData": str(args.data.resolve()),
        "sourceDataSha256": sha256_file(args.data),
        "smokeData": str(smoke_data.resolve()),
        "smokeDataSha256": sha256_file(smoke_data),
        "validation": validation,
        "trainGames": len(train_indexes),
        "validationGames": len(validation_indexes),
        "optimizationSteps": len(train_losses),
        "trainTotalLosses": train_losses,
        "validationLosses": {name: float(value) for name, value in validation_losses.items()},
        "modelVariant": "oq-profile-offbook-level18",
        "offbookSchema": OFFBOOK_SCHEMA,
    }
    write_json(args.output_dir / "cpu_training_smoke_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
