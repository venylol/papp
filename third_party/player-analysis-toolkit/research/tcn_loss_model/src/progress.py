"""UTF-8 atomic progress and run-manifest helpers."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROGRESS_SCHEMA = "tcn-loss-training-progress-v1"


def initial_progress() -> dict[str, Any]:
    return {
        "schema": PROGRESS_SCHEMA, "status": "waiting-for-data", "stage": "setup",
        "model_name": "board-cnn-causal-tcn-time-conditioned-severe-loss", "run_id": "",
        "data_manifest": None, "base_checkpoint": "", "device": "", "gpu_name": "",
        "cuda_version": "", "torch_version": "", "gpu_count": 0,
        "gpu_total_memory_bytes": None, "epoch": 0, "max_epochs": 0, "batch": 0,
        "total_batches": 0, "train_total_loss": None, "validation_total_loss": None,
        "thinking_time_loss": None, "severity_classification_loss": None,
        "wld_classification_loss": None, "wld_validation_metrics": None,
        "zero_loss_log_loss": None, "ge4_log_loss": None, "ge10_log_loss": None,
        "zero_loss_brier": None, "ge4_brier": None, "ge10_brier": None,
        "severity_class_actual_rates": None, "severity_class_mean_probabilities": None,
        "zero_validation_metrics": None, "ge4_validation_metrics": None,
        "ge10_validation_metrics": None,
        "best_metric": None, "best_epoch": None, "learning_rate": None,
        "elapsed_seconds": 0, "eta_seconds": None, "updated_at": "", "error": None,
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    final_payload = dict(payload)
    final_payload["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(final_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for attempt in range(20):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    except Exception:
        if temp.exists():
            temp.unlink()
        raise


def write_progress(output_dir: Path, **updates: Any) -> dict[str, Any]:
    path = output_dir / "progress.json"
    payload = initial_progress()
    if path.exists():
        payload.update(json.loads(path.read_text(encoding="utf-8")))
    payload.update(updates)
    atomic_write_json(path, payload)
    return payload


def read_progress(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "progress.json"
    if not path.is_file():
        raise FileNotFoundError(f"progress file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != PROGRESS_SCHEMA:
        raise ValueError(f"unsupported progress schema: {payload.get('schema')}")
    return payload
