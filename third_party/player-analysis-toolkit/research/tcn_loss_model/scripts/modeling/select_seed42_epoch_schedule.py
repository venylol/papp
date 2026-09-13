#!/usr/bin/env python3
"""Prepare seed 42's fixed-test split and freeze an epoch schedule from validation only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.checkpoint import sha256_file
from src.ensemble import materialize_split_view
from src.feature_policy import INPUT_POLICY
from src.progress import atomic_write_json
from src.training import TrainingConfig


def canonical_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)


def prepare_split(data_path: Path, output_data: Path, output_manifest: Path, seed: int) -> dict[str, Any]:
    data_path = data_path.resolve()
    source_hash = sha256_file(data_path)
    if output_data.exists() or output_manifest.exists():
        if not output_data.is_file() or not output_manifest.is_file():
            raise FileExistsError("seed split must be either absent or a complete data/manifest pair")
        manifest = json.loads(output_manifest.read_text(encoding="utf-8"))
        expected = {
            "schema": "tcn-loss-ensemble-split-v1",
            "seed": seed,
            "sourceDataSha256": source_hash,
            "sha256": sha256_file(output_data),
        }
        mismatches = [key for key, value in expected.items() if manifest.get(key) != value]
        if mismatches:
            raise ValueError(f"existing seed split manifest mismatch: {mismatches}")
        return manifest
    output_data.parent.mkdir(parents=True, exist_ok=True)
    manifest = materialize_split_view(data_path, output_data, seed)
    manifest.update({"schema": "tcn-loss-ensemble-split-v1", "sourceDataSha256": source_hash})
    atomic_write_json(output_manifest, manifest)
    return manifest


def read_history(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("seed 42 history is empty")
    result: list[dict[str, Any]] = []
    numeric = (
        "epoch", "train_total_loss", "validation_total_loss",
        "validation_thinking_time_loss", "validation_severity_classification_loss",
        "zero_log_loss", "ge4_log_loss", "ge10_log_loss", "learning_rate",
    )
    for source in rows:
        row: dict[str, Any] = dict(source)
        for name in numeric:
            row[name] = int(source[name]) if name == "epoch" else float(source[name])
        result.append(row)
    epochs = [row["epoch"] for row in result]
    if epochs != list(range(1, max(epochs) + 1)):
        raise ValueError("seed 42 history epochs are incomplete or out of order")
    return result


def choose_schedule(
    history: list[dict[str, Any]], probe_config: TrainingConfig, minimum_confirmation_epochs: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    head_epochs = int(probe_config.head_epochs)
    max_epoch = int(history[-1]["epoch"])
    if max_epoch != head_epochs + int(probe_config.fine_tune_epochs):
        raise ValueError("history length does not match the probe config")
    fine_rows = [row for row in history if row["epoch"] > head_epochs and row["stage"] == "fine-tuning"]
    if not fine_rows:
        raise ValueError("seed 42 probe contains no fine-tuning epochs")
    best = min(fine_rows, key=lambda row: (row["validation_total_loss"], row["epoch"]))
    best_epoch = int(best["epoch"])
    confirmation_epochs = max_epoch - best_epoch
    if confirmation_epochs < minimum_confirmation_epochs:
        raise ValueError(
            f"probe is too short: validation best epoch {best_epoch} has only "
            f"{confirmation_epochs} later observations; require {minimum_confirmation_epochs}"
        )
    head_best = min(history[:head_epochs], key=lambda row: (row["validation_total_loss"], row["epoch"]))
    if best["validation_total_loss"] >= head_best["validation_total_loss"]:
        raise ValueError("fine-tuning never improved on the best frozen-head validation loss")
    fine_tune_epochs = best_epoch - head_epochs
    if fine_tune_epochs <= 0:
        raise ValueError("selected schedule must include whole-model fine-tuning")
    config = {
        "input_policy": INPUT_POLICY,
        "model_variant": "oq-profile",
        "oq_profile_ablation": "full-31",
        "training": {
            "head_epochs": head_epochs,
            "fine_tune_epochs": fine_tune_epochs,
            "batch_size": probe_config.batch_size,
            "head_learning_rate": probe_config.head_learning_rate,
            "fine_tune_learning_rate": probe_config.fine_tune_learning_rate,
            "weight_decay": probe_config.weight_decay,
            "time_task_weight": probe_config.time_task_weight,
            "severity_classification_weight": probe_config.severity_classification_weight,
            "severity_class_weights": list(probe_config.severity_class_weights),
            "num_workers": probe_config.num_workers,
            "seed": 42,
        },
    }
    decision = {
        "schema": "tcn-loss-seed42-epoch-decision-v1",
        "ok": True,
        "selectionData": "validation-only",
        "testEvaluatedDuringProbe": False,
        "selectionRule": "minimum validation total loss among fine-tuning epochs; earliest epoch breaks exact ties",
        "headEpochDecision": {
            "epochs": head_epochs,
            "basis": "single predeclared two-stage probe boundary; no alternate-learning-rate or broad schedule search",
            "bestFrozenHeadEpoch": int(head_best["epoch"]),
            "bestFrozenHeadValidationTotalLoss": float(head_best["validation_total_loss"]),
        },
        "fineTuneEpochDecision": {
            "epochs": fine_tune_epochs,
            "selectedBestEpoch": best_epoch,
            "probeMaxEpoch": max_epoch,
            "laterConfirmationEpochs": confirmation_epochs,
            "minimumRequiredLaterConfirmationEpochs": minimum_confirmation_epochs,
        },
        "selectedValidationMetrics": {
            name: best[name] for name in (
                "validation_total_loss", "validation_thinking_time_loss",
                "validation_severity_classification_loss", "zero_log_loss",
                "ge4_log_loss", "ge10_log_loss",
            )
        },
        "formalConfig": config,
        "formalConfigCanonicalSha256": canonical_hash(config),
    }
    return config, decision


def select(args: argparse.Namespace) -> dict[str, Any]:
    completion = json.loads(args.validation_completion.read_text(encoding="utf-8"))
    if completion.get("schema") != "tcn-loss-validation-only-completion-v1" or not completion.get("ok"):
        raise ValueError("seed 42 probe lacks a passing validation-only completion gate")
    if completion.get("testEvaluated") is not False or args.test_metrics.exists():
        raise ValueError("fixed test was opened during the seed 42 epoch probe")
    probe = TrainingConfig.load(args.probe_config)
    if probe.seed != 42:
        raise ValueError("epoch probe must use seed 42")
    history = read_history(args.history)
    config, decision = choose_schedule(history, probe, args.minimum_confirmation_epochs)
    decision.update({
        "history": str(args.history.resolve()),
        "historySha256": sha256_file(args.history),
        "probeConfig": str(args.probe_config.resolve()),
        "probeConfigSha256": sha256_file(args.probe_config),
        "validationOnlyCompletion": str(args.validation_completion.resolve()),
        "validationOnlyCompletionSha256": sha256_file(args.validation_completion),
    })
    config_text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if args.output_config.exists() and args.output_config.read_text(encoding="utf-8") != config_text:
        raise FileExistsError("refusing to overwrite a different frozen formal config")
    atomic_write_text(args.output_config, config_text)
    decision["formalConfigPath"] = str(args.output_config.resolve())
    decision["formalConfigFileSha256"] = sha256_file(args.output_config)
    if args.output_decision.exists():
        existing = json.loads(args.output_decision.read_text(encoding="utf-8"))
        if existing != decision:
            raise FileExistsError("refusing to overwrite a different epoch decision")
    else:
        atomic_write_json(args.output_decision, decision)
    return decision


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-split")
    prepare.add_argument("--data", required=True, type=Path)
    prepare.add_argument("--output-data", required=True, type=Path)
    prepare.add_argument("--output-manifest", required=True, type=Path)
    prepare.add_argument("--seed", type=int, default=42)
    freeze = commands.add_parser("select")
    freeze.add_argument("--history", required=True, type=Path)
    freeze.add_argument("--probe-config", required=True, type=Path)
    freeze.add_argument("--validation-completion", required=True, type=Path)
    freeze.add_argument("--test-metrics", required=True, type=Path)
    freeze.add_argument("--output-config", required=True, type=Path)
    freeze.add_argument("--output-decision", required=True, type=Path)
    freeze.add_argument("--minimum-confirmation-epochs", type=int, default=12)
    return result


def main() -> int:
    args = parser().parse_args()
    report = (
        prepare_split(args.data, args.output_data, args.output_manifest, args.seed)
        if args.command == "prepare-split"
        else select(args)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
