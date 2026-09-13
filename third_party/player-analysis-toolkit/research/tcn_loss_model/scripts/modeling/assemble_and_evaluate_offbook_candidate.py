#!/usr/bin/env python3
"""Assemble all twelve Level18 members and evaluate old/new ensembles on test."""

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
from src.ensemble import resolve_member_checkpoint  # noqa: E402
from src.model import WLD_CLASS_NAMES, SEVERITY_CLASS_NAMES  # noqa: E402
from src.offbook import OFFBOOK_SCHEMA  # noqa: E402
from src.oq_profile_features import OQ_PROFILE_FEATURE_NAMES, profile_ablation_hash  # noqa: E402
from src.training import (  # noqa: E402
    TrainingConfig,
    SequenceDataset,
    _binary_metrics,
    _model_output,
    _move,
    evaluate,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--official-ensemble-manifest", type=Path, required=True)
    parser.add_argument("--pilot-checkpoint", type=Path, required=True)
    parser.add_argument("--remaining-ensemble-manifest", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-size", type=int)
    return parser.parse_args()


def json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def split_membership(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as data:
        game_ids = data["game_id"].astype(str)
        split = data["split"].astype(str)
    return {
        name: {
            "games": int((split == name).sum()),
            "gameIdsSha256": hashlib.sha256(
                "\n".join(sorted(game_ids[split == name].tolist())).encode("utf-8")
            ).hexdigest(),
        }
        for name in ("train", "validation", "test")
    }


def safe_log(values: np.ndarray) -> np.ndarray:
    return np.log(np.clip(values.astype(np.float64), 1.0e-12, 1.0))


def anchor_group(value: int, present: bool) -> str:
    if not present:
        return "no_offbook"
    if value <= 14:
        return "5-14"
    if value <= 24:
        return "15-24"
    if value <= 34:
        return "25-34"
    if value <= 44:
        return "35-44"
    if value <= 54:
        return "45-54"
    if value <= 60:
        return "55-60"
    raise ValueError(f"invalid anchor ply {value}")


def collect_predictions(
    model: torch.nn.Module,
    data_path: Path,
    device: torch.device,
    batch_size: int,
    use_offbook: bool,
) -> dict[str, np.ndarray]:
    with np.load(data_path, allow_pickle=False) as data:
        test_games = np.flatnonzero(data["split"].astype(str) == "test")
        test_game_ids = data["game_id"].astype(str)[test_games]
        steps = data["X"].shape[1]
        test_side = data["side_to_move"][test_games].astype(str)
        test_offbook_present = data["offbook_present"][test_games].astype(bool)
        test_offbook_ply = data["offbook_ply"][test_games].astype(int)
        dataset = SequenceDataset(data_path, "test", require_oq_profile=True, require_offbook=use_offbook)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0, pin_memory=True)
        parts: dict[str, list[np.ndarray]] = {
            "severity_probabilities": [], "wld_probabilities": [], "pred_time_log": [],
            "severity_targets": [], "wld_targets": [], "wld_available": [],
            "time_targets": [], "game_id": [], "side_to_move": [],
            "offbook_present": [], "offbook_ply": [],
        }
        cursor = 0
        model.eval()
        for batch in loader:
            batch_size_here = int(batch["X"].shape[0])
            batch = _move(batch, device)
            output = _model_output(model, batch, use_oq_profile=True, use_offbook=use_offbook)
            valid = batch["mask"].bool()
            valid_cpu = valid.detach().cpu().numpy()
            selected_ids = np.repeat(test_game_ids[cursor:cursor + batch_size_here, None], steps, axis=1)
            side = test_side[cursor:cursor + batch_size_here]
            offbook_present = test_offbook_present[cursor:cursor + batch_size_here]
            offbook_ply = test_offbook_ply[cursor:cursor + batch_size_here]
            parts["severity_probabilities"].append(output.severity_class_probabilities.detach().cpu().numpy()[valid_cpu])
            parts["wld_probabilities"].append(output.wld_probabilities.detach().cpu().numpy()[valid_cpu])
            parts["pred_time_log"].append(output.pred_time_log_seconds.detach().cpu().numpy()[valid_cpu])
            parts["severity_targets"].append(batch["severity_class"].detach().cpu().numpy()[valid_cpu].astype(int))
            parts["wld_targets"].append(batch["wld_class"].detach().cpu().numpy()[valid_cpu].astype(int))
            parts["wld_available"].append((
                batch["wld_label_available"].bool()
                & (batch["global_placement_ply"] >= 39)
            ).detach().cpu().numpy()[valid_cpu])
            actual_time = batch["actual_thinking_time_ms"].detach().cpu().numpy()[valid_cpu].astype(float)
            parts["time_targets"].append(np.log1p(np.clip(actual_time, 0, None) / 1000.0))
            parts["game_id"].append(selected_ids[valid_cpu])
            parts["side_to_move"].append(side[valid_cpu])
            parts["offbook_present"].append(offbook_present[valid_cpu])
            parts["offbook_ply"].append(offbook_ply[valid_cpu])
            cursor += batch_size_here
    return {name: np.concatenate(values) for name, values in parts.items()}


def metrics_from_predictions(predictions: dict[str, np.ndarray]) -> dict[str, Any]:
    severity_probabilities = predictions["severity_probabilities"].astype(np.float64)
    severity_targets = predictions["severity_targets"]
    time_error = predictions["pred_time_log"].astype(np.float64) - predictions["time_targets"]
    severity_ce = -safe_log(severity_probabilities[np.arange(len(severity_targets)), severity_targets])
    zero = severity_targets == 0
    ge4 = severity_targets >= 2
    ge10 = severity_targets == 3
    p_zero = severity_probabilities[:, 0]
    p_ge4 = severity_probabilities[:, 2] + severity_probabilities[:, 3]
    p_ge10 = severity_probabilities[:, 3]
    wld_mask = predictions["wld_available"].astype(bool)
    wld_probabilities = predictions["wld_probabilities"].astype(np.float64)[wld_mask]
    wld_targets = predictions["wld_targets"][wld_mask]
    expected_wld = 0.5 * wld_probabilities[:, 1] + wld_probabilities[:, 2]
    actual_wld = wld_targets / 2.0
    wld = {
        "nodes": int(len(wld_targets)),
        "cross_entropy": float(-np.mean(safe_log(wld_probabilities[np.arange(len(wld_targets)), wld_targets]))),
        "accuracy": float((wld_probabilities.argmax(axis=1) == wld_targets).mean()),
        "expected_wld_loss_mae": float(np.abs(expected_wld - actual_wld).mean()),
        "expected_wld_loss_mean": float(expected_wld.mean()),
        "actual_wld_loss_mean": float(actual_wld.mean()),
        "class_actual_rates": {name: float((wld_targets == index).mean()) for index, name in enumerate(WLD_CLASS_NAMES)},
        "class_mean_probabilities": {name: float(wld_probabilities[:, index].mean()) for index, name in enumerate(WLD_CLASS_NAMES)},
    }
    return {
        "nodes": int(len(severity_targets)),
        "thinking_time_mse": float(np.mean(time_error ** 2)),
        "severity_classification_loss": float(np.mean(severity_ce)),
        "wld_classification_loss": wld["cross_entropy"],
        "zero": _binary_metrics(zero, p_zero),
        "ge4": _binary_metrics(ge4, p_ge4),
        "ge10": _binary_metrics(ge10, p_ge10),
        "class_actual_rates": {
            name: float((severity_targets == index).mean()) for index, name in enumerate(SEVERITY_CLASS_NAMES)
        },
        "class_mean_probabilities": {
            name: float(severity_probabilities[:, index].mean()) for index, name in enumerate(SEVERITY_CLASS_NAMES)
        },
        "wld": wld,
    }


def group_metrics(predictions: dict[str, np.ndarray]) -> dict[str, Any]:
    present = predictions["offbook_present"].astype(bool)
    ply = predictions["offbook_ply"].astype(int)
    groups: dict[str, np.ndarray] = {
        "offbook": present,
        "no_offbook": ~present,
        "black": predictions["side_to_move"] == "black",
        "white": predictions["side_to_move"] == "white",
    }
    anchor_labels = np.asarray([anchor_group(int(value), bool(flag)) for value, flag in zip(ply, present, strict=True)])
    for label in ("5-14", "15-24", "25-34", "35-44", "45-54", "55-60"):
        groups[f"anchor_{label}"] = anchor_labels == label
    result = {}
    for name, selected in groups.items():
        if not bool(selected.any()):
            result[name] = {"nodes": 0, "status": "empty"}
            continue
        subset = {key: value[selected] for key, value in predictions.items()}
        result[name] = metrics_from_predictions(subset)
    return result


def load_member_model(
    checkpoint_path: Path,
    base_checkpoint: Path,
    device: torch.device,
    use_offbook: bool,
    profile_ablation: str,
) -> tuple[torch.nn.Module, dict[str, Any]]:
    payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    expected_schema = (
        "tcn-loss-profile-offbook-level18-wld-checkpoint-v1"
        if use_offbook else "tcn-loss-profile-wld-checkpoint-v2"
    )
    if payload.get("schema") != expected_schema:
        raise ValueError(f"checkpoint {checkpoint_path} schema mismatch: {payload.get('schema')!r}")
    model, _ = (
        load_transferred_offbook_profile_model(base_checkpoint, profile_ablation)
        if use_offbook else load_transferred_profile_model(base_checkpoint, profile_ablation)
    )
    migration = (
        load_trained_state_with_offbook_migration(model, payload["modelStateDict"])
        if use_offbook else model.load_state_dict(payload["modelStateDict"], strict=True)
    )
    model.to(device).eval()
    return model, {"payload": payload, "stateMigration": migration}


def evaluate_one_member(
    checkpoint_path: Path,
    data_path: Path,
    base_checkpoint: Path,
    config: TrainingConfig,
    device: torch.device,
    batch_size: int,
    use_offbook: bool,
    profile_ablation: str,
) -> tuple[dict[str, Any], dict[str, np.ndarray], dict[str, Any]]:
    model, loaded = load_member_model(checkpoint_path, base_checkpoint, device, use_offbook, profile_ablation)
    loader = DataLoader(
        SequenceDataset(data_path, "test", require_oq_profile=True, require_offbook=use_offbook),
        batch_size=batch_size, num_workers=0, pin_memory=True,
    )
    batch_metrics = evaluate(model, loader, device, config, use_oq_profile=True, use_offbook=use_offbook)
    predictions = collect_predictions(model, data_path, device, batch_size, use_offbook)
    node_metrics = metrics_from_predictions(predictions)
    node_metrics["groups"] = group_metrics(predictions)
    del model
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return {"batchWeightedEvaluation": batch_metrics, "nodeWeightedEvaluation": node_metrics}, predictions, loaded


def manifest_member_record(
    seed: int, checkpoint_path: Path, source_member: dict[str, Any], evaluation: dict[str, Any]
) -> dict[str, Any]:
    payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    run_manifest = payload.get("manifest", {})
    progress_path = checkpoint_path.parent / "progress.json"
    progress = load_json(progress_path) if progress_path.is_file() else {}
    return {
        "member": int(seed) - 41,
        "seed": int(seed),
        "bestCheckpoint": str(checkpoint_path.resolve()),
        "bestCheckpointSha256": sha256_file(checkpoint_path),
        "bestEpoch": progress.get("best_epoch", payload.get("bestEpoch")),
        "bestSemanticEpoch": int(run_manifest.get("warmStartEpoch", 0)) + int(progress.get("best_epoch", payload.get("bestEpoch", 0))),
        "sourceMember": source_member,
        "runManifest": run_manifest,
        "validationMetrics": source_member.get("validationMetrics"),
        "testMetrics": evaluation,
    }


def main() -> int:
    args = parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("final candidate test evaluation requires CUDA")
    if not args.device.startswith("cuda"):
        raise ValueError("final candidate evaluation must run on CUDA")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty candidate output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device(args.device)
    official_manifest = load_json(args.official_ensemble_manifest)
    remaining_manifest = load_json(args.remaining_ensemble_manifest)
    pilot_payload = torch.load(args.pilot_checkpoint, map_location="cpu", weights_only=False)
    if pilot_payload.get("schema") != "tcn-loss-profile-offbook-level18-wld-checkpoint-v1":
        raise ValueError("pilot checkpoint schema mismatch")
    if remaining_manifest.get("testEvaluationPerformed") is not False:
        raise ValueError("remaining ensemble must be validation-only before this final test stage")
    remaining_by_seed = {int(item["seed"]): item for item in remaining_manifest.get("members", [])}
    if set(remaining_by_seed) != set(range(43, 54)):
        raise ValueError(f"remaining validation-only ensemble must contain seeds 43..53, found {sorted(remaining_by_seed)}")
    official_by_seed = {int(item["seed"]): item for item in official_manifest.get("members", [])}
    if set(official_by_seed) != set(range(42, 54)):
        raise ValueError(f"official ensemble must contain seeds 42..53, found {sorted(official_by_seed)}")
    source_hash = sha256_file(args.data)
    base_hash = sha256_file(args.base_checkpoint)
    pilot_manifest = pilot_payload["manifest"]
    if pilot_manifest.get("dataSha256") != source_hash or pilot_manifest.get("baseCheckpointSha256") != base_hash:
        raise ValueError("pilot checkpoint source identity mismatch")
    if pilot_manifest.get("offbookSchema") != OFFBOOK_SCHEMA:
        raise ValueError("pilot offbook schema mismatch")
    profile_ablation = str(pilot_manifest.get("oqProfileAblation") or "full-31")
    if pilot_manifest.get("oqProfileAblationSha256") != profile_ablation_hash(profile_ablation):
        raise ValueError("pilot profile ablation hash mismatch")
    base_payload = torch.load(args.base_checkpoint, map_location="cpu", weights_only=False)
    validation = validate_model_ready_npz(
        args.data,
        expected_input_features=base_payload["input_features"],
        expected_board_channels=base_payload["board_encoding"]["cnn_channels"],
        expected_preprocessing_sha256=json_hash(base_payload["preprocessing"]),
        require_oq_profile=True,
        expected_oq_profile_feature_names=OQ_PROFILE_FEATURE_NAMES,
        expected_oq_profile_preprocessing_sha256=pilot_manifest.get("oqProfilePreprocessingSha256"),
        expected_oq_profile_policy=pilot_manifest.get("oqProfilePolicy"),
        require_offbook=True,
        expected_offbook_schema=OFFBOOK_SCHEMA,
        expected_offbook_source_checkpoint_sha256=base_hash,
        expected_offbook_source_data_sha256=pilot_manifest.get("offbookSourceDataSha256"),
        expected_offbook_records_sha256=pilot_manifest.get("offbookRecordsSha256"),
        expected_offbook_materialization_sha256=pilot_manifest.get("offbookMaterializationSha256"),
    )
    fixed_split_membership = split_membership(args.data)
    config = TrainingConfig.load(args.config)
    batch_size = args.batch_size or config.batch_size
    old_evaluations: dict[int, dict[str, Any]] = {}
    new_evaluations: dict[int, dict[str, Any]] = {}
    old_predictions: dict[int, dict[str, np.ndarray]] = {}
    new_predictions: dict[int, dict[str, np.ndarray]] = {}
    new_member_records = []
    old_member_records = []
    for seed in range(42, 54):
        old_path = resolve_member_checkpoint(
            args.official_ensemble_manifest, official_by_seed[seed]["bestCheckpoint"]
        )
        old_evaluation, old_prediction, _ = evaluate_one_member(
            old_path, args.data, args.base_checkpoint, config, device, batch_size, False, profile_ablation
        )
        old_evaluations[seed] = old_evaluation
        old_predictions[seed] = old_prediction
        old_member_records.append({
            "member": seed - 41, "seed": seed, "checkpoint": str(old_path.resolve()),
            "checkpointSha256": sha256_file(old_path), "evaluation": old_evaluation,
        })
        if seed == 42:
            new_path = args.pilot_checkpoint
            source_member = {"source": "pilot_seed42", "seed": seed}
        else:
            source_member = remaining_by_seed[seed]
            new_path = Path(source_member["bestCheckpoint"])
        new_evaluation, new_prediction, _ = evaluate_one_member(
            new_path, args.data, args.base_checkpoint, config, device, batch_size, True, profile_ablation
        )
        new_evaluations[seed] = new_evaluation
        new_predictions[seed] = new_prediction
        new_member_records.append(manifest_member_record(seed, new_path, source_member, new_evaluation))

    def ensemble_prediction(predictions: dict[int, dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
        seeds = sorted(predictions)
        result = {}
        for name in ("severity_probabilities", "wld_probabilities", "pred_time_log"):
            result[name] = np.mean(np.stack([predictions[seed][name] for seed in seeds]), axis=0)
        for name in ("severity_targets", "wld_targets", "wld_available", "time_targets", "game_id", "side_to_move", "offbook_present", "offbook_ply"):
            first = predictions[seeds[0]][name]
            for seed in seeds[1:]:
                if not np.array_equal(first, predictions[seed][name]):
                    raise ValueError(f"member prediction alignment differs for {name} at seed {seed}")
            result[name] = first
        return result

    old_ensemble_predictions = ensemble_prediction(old_predictions)
    new_ensemble_predictions = ensemble_prediction(new_predictions)
    old_ensemble_metrics = metrics_from_predictions(old_ensemble_predictions)
    old_ensemble_metrics["groups"] = group_metrics(old_ensemble_predictions)
    new_ensemble_metrics = metrics_from_predictions(new_ensemble_predictions)
    new_ensemble_metrics["groups"] = group_metrics(new_ensemble_predictions)
    candidate_manifest = {
        "schema": "tcn-loss-level18-offbook-candidate-ensemble-manifest-v1",
        "status": "completed",
        "modelVariant": "oq-profile-offbook-level18",
        "sourceData": str(args.data.resolve()), "sourceDataSha256": source_hash,
        "baseCheckpoint": str(args.base_checkpoint.resolve()), "baseCheckpointSha256": base_hash,
        "config": str(args.config.resolve()), "configSha256": sha256_file(args.config),
        "officialPrimaryManifest": str(args.official_ensemble_manifest.resolve()),
        "officialPrimaryManifestSha256": sha256_file(args.official_ensemble_manifest),
        "pilotCheckpoint": str(args.pilot_checkpoint.resolve()),
        "pilotCheckpointSha256": sha256_file(args.pilot_checkpoint),
        "remainingValidationOnlyManifest": str(args.remaining_ensemble_manifest.resolve()),
        "remainingValidationOnlyManifestSha256": sha256_file(args.remaining_ensemble_manifest),
        "fixedSplit": True,
        "splitMembership": fixed_split_membership,
        "testEvaluationPerformed": True,
        "offbookSchema": OFFBOOK_SCHEMA,
        "offbookLabelSource": pilot_manifest.get("offbookLabelSource"),
        "offbookAlgorithmVersion": pilot_manifest.get("offbookAlgorithmVersion"),
        "offbookNormalization": pilot_manifest.get("offbookNormalization"),
        "offbookSourceEngineLevel": pilot_manifest.get("offbookSourceEngineLevel"),
        "offbookEngineContract": pilot_manifest.get("offbookEngineContract"),
        "offbookSourceDataSha256": pilot_manifest.get("offbookSourceDataSha256"),
        "offbookSourceCheckpointSha256": pilot_manifest.get("offbookSourceCheckpointSha256"),
        "offbookRecordsSha256": pilot_manifest.get("offbookRecordsSha256"),
        "offbookMaterializationSha256": pilot_manifest.get("offbookMaterializationSha256"),
        "offbookRetrospectiveDisclosure": pilot_manifest.get("offbookRetrospectiveDisclosure"),
        "validation": validation,
        "members": new_member_records,
    }
    candidate_manifest_path = args.output_dir / "candidate_ensemble_manifest.json"
    candidate_manifest_path.write_text(json.dumps(candidate_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema": "tcn-loss-level18-offbook-candidate-test-report-v1",
        "status": "completed",
        "testEvaluated": True,
        "evaluationDevice": str(device),
        "data": {"path": str(args.data.resolve()), "sha256": source_hash, "validation": validation},
        "fixedSplitMembership": fixed_split_membership,
        "baseCheckpointSha256": base_hash,
        "offbookContract": {
            "schema": OFFBOOK_SCHEMA,
            "algorithmVersion": pilot_manifest.get("offbookAlgorithmVersion"),
            "normalization": pilot_manifest.get("offbookNormalization"),
            "materializationSha256": pilot_manifest.get("offbookMaterializationSha256"),
            "retrospectiveDisclosure": pilot_manifest.get("offbookRetrospectiveDisclosure"),
        },
        "oldOfficialEnsemble": {
            "manifest": str(args.official_ensemble_manifest.resolve()),
            "manifestSha256": sha256_file(args.official_ensemble_manifest),
            "members": old_member_records,
            "ensembleLevel": old_ensemble_metrics,
        },
        "newCandidateEnsemble": {
            "manifest": str(candidate_manifest_path.resolve()),
            "manifestSha256": sha256_file(candidate_manifest_path),
            "members": new_member_records,
            "ensembleLevel": new_ensemble_metrics,
        },
        "comparison": {
            "ensembleLevelNodeWeighted": {
                "severityClassificationLossCandidateMinusOld": new_ensemble_metrics["severity_classification_loss"] - old_ensemble_metrics["severity_classification_loss"],
                "wldClassificationLossCandidateMinusOld": new_ensemble_metrics["wld_classification_loss"] - old_ensemble_metrics["wld_classification_loss"],
                "thinkingTimeMseCandidateMinusOld": new_ensemble_metrics["thinking_time_mse"] - old_ensemble_metrics["thinking_time_mse"],
            },
            "groupKeys": ["offbook", "no_offbook", "black", "white", "anchor_5-14", "anchor_15-24", "anchor_25-34", "anchor_35-44", "anchor_45-54", "anchor_55-60"],
        },
        "noPrimaryPromotion": True,
        "primaryModelPointerChanged": False,
        "formalArtifactsPreserved": True,
    }
    report_path = args.output_dir / "candidate_test_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidateManifest": str(candidate_manifest_path.resolve()),
        "testReport": str(report_path.resolve()),
        "testEvaluated": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
