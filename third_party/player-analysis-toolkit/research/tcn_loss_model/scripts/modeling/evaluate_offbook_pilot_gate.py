#!/usr/bin/env python3
"""Validation-only comparison for the Level18 offbook seed-42 pilot gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.checkpoint import (  # noqa: E402
    load_trained_state_with_offbook_migration,
    load_transferred_offbook_profile_model,
    load_transferred_profile_model,
    sha256_file,
)
from src.data_contract import validate_model_ready_npz  # noqa: E402
from src.ensemble import resolve_warm_start_member  # noqa: E402
from src.model import ProfileConditionedLossModel  # noqa: E402
from src.offbook import OFFBOOK_SCHEMA  # noqa: E402
from src.oq_profile_features import OQ_PROFILE_FEATURE_NAMES, profile_ablation_hash  # noqa: E402
from src.training import TrainingConfig, SequenceDataset, _device_info, evaluate  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--official-ensemble-manifest", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--candidate-checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-size", type=int)
    return parser.parse_args()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _ids_hash(values: np.ndarray) -> str:
    return hashlib.sha256("\n".join(sorted(values.astype(str).tolist())).encode("utf-8")).hexdigest()


def split_membership(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as data:
        game_ids = data["game_id"].astype(str)
        split = data["split"].astype(str)
    return {
        name: {
            "games": int((split == name).sum()),
            "gameIdsSha256": _ids_hash(game_ids[split == name]),
        }
        for name in ("train", "validation", "test")
    }


def compare_metric(name: str, baseline: float, candidate: float) -> dict[str, Any]:
    absolute = candidate - baseline
    relative = absolute / baseline if baseline != 0 else None
    if name == "validation_severity_classification_loss":
        passed = candidate < baseline
        rule = "candidate < baseline"
    elif name == "validation_wld_classification_loss":
        passed = candidate <= baseline
        rule = "candidate <= baseline"
    elif name == "validation_thinking_time_mse":
        passed = candidate <= baseline * 1.01
        rule = "candidate <= baseline * 1.01"
    else:
        raise AssertionError(name)
    return {
        "baseline": float(baseline),
        "candidate": float(candidate),
        "absoluteDifferenceCandidateMinusBaseline": float(absolute),
        "relativeDifferenceCandidateMinusBaseline": float(relative) if relative is not None else None,
        "threshold": rule,
        "passed": bool(passed),
    }


def main() -> int:
    args = parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("pilot gate requires CUDA; torch.cuda.is_available() is false")
    if not args.device.startswith("cuda"):
        raise ValueError("pilot gate must run on CUDA")
    device = torch.device(args.device)
    if args.batch_size is not None and args.batch_size <= 0:
        raise ValueError("batch size must be positive")
    official_checkpoint, official_manifest_path, official_member = resolve_warm_start_member(
        args.official_ensemble_manifest, args.seed, "best.pt"
    )
    official_payload = torch.load(official_checkpoint, map_location="cpu", weights_only=False)
    candidate_payload = torch.load(args.candidate_checkpoint, map_location="cpu", weights_only=False)
    if official_payload.get("schema") not in {"tcn-loss-profile-checkpoint-v1", "tcn-loss-profile-wld-checkpoint-v2"}:
        raise ValueError("official seed-42 checkpoint is not the expected profile checkpoint")
    if candidate_payload.get("schema") != "tcn-loss-profile-offbook-level18-wld-checkpoint-v1":
        raise ValueError("candidate checkpoint is not the Level18 offbook checkpoint schema")
    source_hash = sha256_file(args.data)
    old_manifest = official_payload["manifest"]
    candidate_manifest = candidate_payload["manifest"]
    if candidate_manifest.get("dataSha256") != source_hash:
        raise ValueError("candidate seed-42 checkpoint data hash differs from the pilot data source")
    if old_manifest.get("dataSha256") != candidate_manifest.get("offbookSourceDataSha256"):
        raise ValueError(
            "official seed-42 raw data hash differs from the Level18 materialization source data hash"
        )
    base_hash = sha256_file(args.base_checkpoint)
    if old_manifest.get("baseCheckpointSha256") != base_hash or candidate_manifest.get("baseCheckpointSha256") != base_hash:
        raise ValueError("pilot checkpoint base hash mismatch")
    if candidate_manifest.get("offbookSchema") != OFFBOOK_SCHEMA:
        raise ValueError("candidate offbook schema manifest mismatch")
    if candidate_manifest.get("testEvaluationPlanned") is not False:
        raise ValueError("pilot candidate was not trained with test evaluation disabled")

    base_payload = torch.load(args.base_checkpoint, map_location="cpu", weights_only=False)
    preprocessing_hash = _json_hash(base_payload["preprocessing"])
    profile_ablation = str(old_manifest.get("oqProfileAblation") or "full-31")
    if old_manifest.get("oqProfileAblationSha256") != profile_ablation_hash(profile_ablation):
        raise ValueError("official profile ablation hash mismatch")
    if candidate_manifest.get("oqProfileAblation") != profile_ablation:
        raise ValueError("candidate profile ablation differs from official seed-42")
    validation = validate_model_ready_npz(
        args.data,
        expected_input_features=base_payload["input_features"],
        expected_board_channels=base_payload["board_encoding"]["cnn_channels"],
        expected_preprocessing_sha256=preprocessing_hash,
        require_oq_profile=True,
        expected_oq_profile_feature_names=OQ_PROFILE_FEATURE_NAMES,
        expected_oq_profile_preprocessing_sha256=old_manifest.get("oqProfilePreprocessingSha256"),
        expected_oq_profile_policy=old_manifest.get("oqProfilePolicy"),
        require_offbook=True,
        expected_offbook_schema=OFFBOOK_SCHEMA,
        expected_offbook_source_checkpoint_sha256=base_hash,
        expected_offbook_source_data_sha256=candidate_manifest.get("offbookSourceDataSha256"),
        expected_offbook_records_sha256=candidate_manifest.get("offbookRecordsSha256"),
        expected_offbook_materialization_sha256=candidate_manifest.get("offbookMaterializationSha256"),
    )
    split_report = split_membership(args.data)
    if old_manifest.get("dataset", {}).get("splits") != validation["splits"]:
        raise ValueError("pilot validation split counts differ from the official seed-42 checkpoint")
    if candidate_manifest.get("dataset", {}).get("splits") != validation["splits"]:
        raise ValueError("candidate validation split counts differ from the source")

    cfg = TrainingConfig.load(args.config)
    batch_size = args.batch_size or cfg.batch_size
    loader = DataLoader(
        SequenceDataset(args.data, "validation", require_oq_profile=True, require_offbook=True),
        batch_size=batch_size, num_workers=0, pin_memory=True,
    )
    baseline_model, _ = load_transferred_profile_model(args.base_checkpoint, profile_ablation)
    baseline_model.load_state_dict(official_payload["modelStateDict"], strict=True)
    candidate_model, _ = load_transferred_offbook_profile_model(args.base_checkpoint, profile_ablation)
    migration = load_trained_state_with_offbook_migration(candidate_model, candidate_payload["modelStateDict"])
    baseline_model.to(device).eval()
    candidate_model.to(device).eval()
    baseline_metrics = evaluate(baseline_model, loader, device, cfg, use_oq_profile=True, use_offbook=False)
    candidate_metrics = evaluate(candidate_model, loader, device, cfg, use_oq_profile=True, use_offbook=True)
    metrics = {
        "validation_severity_classification_loss": compare_metric(
            "validation_severity_classification_loss",
            baseline_metrics["severity_classification"], candidate_metrics["severity_classification"],
        ),
        "validation_wld_classification_loss": compare_metric(
            "validation_wld_classification_loss",
            baseline_metrics["wld_classification"], candidate_metrics["wld_classification"],
        ),
        "validation_thinking_time_mse": compare_metric(
            "validation_thinking_time_mse",
            baseline_metrics["thinking_time"], candidate_metrics["thinking_time"],
        ),
    }
    passed = all(item["passed"] for item in metrics.values())
    report = {
        "schema": "tcn-loss-level18-offbook-pilot-gate-v1",
        "pilotGateStatus": "passed" if passed else "failed",
        "pilot_gate_status": "passed" if passed else "failed",
        "seed": int(args.seed),
        "device": str(device),
        "gpu": _device_info(),
        "data": {"path": str(args.data.resolve()), "sha256": source_hash, "validation": validation},
        "rawSourceDataSha256": old_manifest.get("dataSha256"),
        "fixedSplitMembership": split_report,
        "officialSeed42": {
            "ensembleManifest": str(official_manifest_path.resolve()),
            "manifestMember": official_member,
            "checkpoint": str(official_checkpoint.resolve()),
            "checkpointSha256": sha256_file(official_checkpoint),
            "validationMetrics": baseline_metrics,
            "testSplitReadForEvaluation": False,
        },
        "candidateSeed42": {
            "checkpoint": str(args.candidate_checkpoint.resolve()),
            "checkpointSha256": sha256_file(args.candidate_checkpoint),
            "stateMigration": migration,
            "validationMetrics": candidate_metrics,
            "testSplitReadForEvaluation": False,
        },
        "metrics": metrics,
        "thresholds": {
            "severityClassificationLoss": "strictly lower",
            "wldClassificationLoss": "lower or equal",
            "thinkingTimeMse": "no more than 1.01 times baseline",
        },
        "testEvaluated": False,
        "testSplitAccessedForEvaluation": False,
        "continuationAuthorizedByThisReport": bool(passed),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
