"""Fixed-protocol personal fine-tuning of every member in a trained ensemble."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader

from .checkpoint import (
    load_trained_state_with_offbook_migration,
    load_transferred_model,
    load_transferred_offbook_profile_model,
    load_transferred_profile_model,
    sha256_file,
)
from .data_contract import validate_model_ready_npz
from .ensemble import resolve_member_checkpoint
from .feature_policy import INPUT_POLICY
from .progress import atomic_write_json
from .training import SequenceDataset, _model_output, _move
from .model import multitask_loss
from .offbook import OFFBOOK_SCHEMA
from .oq_profile_features import OQ_PROFILE_FEATURE_NAMES, profile_ablation_hash


@dataclass(frozen=True)
class PersonalConfig:
    epochs: int
    batch_size: int
    learning_rate: float
    weight_decay: float
    l2_sp_weight: float
    time_task_weight: float
    severity_classification_weight: float
    severity_class_weights: tuple[float, float, float, float]
    seed: int

    @classmethod
    def load(cls, path: Path) -> "PersonalConfig":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "tcn-loss-personal-finetune-config-v1":
            raise ValueError("personal config schema mismatch")
        if payload.get("input_policy") != INPUT_POLICY:
            raise ValueError("personal config input policy mismatch")
        values = {key: value for key, value in payload.items() if key not in {"schema", "input_policy"}}
        values["severity_class_weights"] = tuple(values["severity_class_weights"])
        cfg = cls(**values)
        if cfg.epochs <= 0 or cfg.learning_rate <= 0 or cfg.l2_sp_weight < 0:
            raise ValueError("invalid personal fine-tuning schedule")
        return cfg


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    torch.save(payload, temporary)
    os.replace(temporary, path)


def _control_and_reported_ids(data_path: Path) -> tuple[list[str], list[str], int]:
    with np.load(data_path, allow_pickle=False) as data:
        game_ids = data["game_id"].astype(str)
        splits = data["split"].astype(str)
        control = sorted(game_ids[splits == "train"].tolist())
        reported = sorted(game_ids[splits == "test"].tolist())
        mask = data["mask"].astype(bool)
        control_nodes = int(mask[splits == "train"].sum())
    if not control or not reported or set(control) & set(reported):
        raise ValueError("personal data must have disjoint non-empty train controls and test reported games")
    return control, reported, control_nodes


def fine_tune_member(
    data_path: Path, base_checkpoint: Path, trained_member_checkpoint: Path,
    config_path: Path, output_dir: Path, member_index: int,
) -> dict[str, Any]:
    if not torch.cuda.is_available():
        raise RuntimeError("personal fine-tuning requires CUDA")
    cfg = PersonalConfig.load(config_path)
    seed = cfg.seed + member_index - 1
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
    control_ids, reported_ids, control_nodes = _control_and_reported_ids(data_path)
    trained = torch.load(trained_member_checkpoint, map_location="cpu", weights_only=False)
    checkpoint_schema = trained.get("schema")
    use_offbook = checkpoint_schema == "tcn-loss-profile-offbook-level18-wld-checkpoint-v1"
    use_oq_profile = use_offbook or checkpoint_schema in {
        "tcn-loss-profile-checkpoint-v1", "tcn-loss-profile-wld-checkpoint-v2",
    }
    if checkpoint_schema not in {
        "tcn-loss-checkpoint-v1", "tcn-loss-wld-checkpoint-v2",
        "tcn-loss-profile-checkpoint-v1", "tcn-loss-profile-wld-checkpoint-v2",
        "tcn-loss-profile-offbook-level18-wld-checkpoint-v1",
    }:
        raise ValueError("base ensemble member checkpoint schema mismatch")
    if trained["manifest"].get("baseCheckpointSha256") != sha256_file(base_checkpoint):
        raise ValueError("base ensemble member was initialized from another official checkpoint")
    manifest = {
        "schema": "personal-tcn-member-manifest-v1", "member": member_index,
        "baseEnsembleCheckpoint": str(trained_member_checkpoint.resolve()),
        "baseEnsembleCheckpointSha256": sha256_file(trained_member_checkpoint),
        "officialBaseCheckpoint": str(base_checkpoint.resolve()),
        "officialBaseCheckpointSha256": sha256_file(base_checkpoint),
        "personalData": str(data_path.resolve()), "personalDataSha256": sha256_file(data_path),
        "config": str(config_path.resolve()), "configSha256": sha256_file(config_path),
        "controlGameIds": control_ids, "controlGameIdsSha256": _json_hash(control_ids),
        "reportedExcludedGameIds": reported_ids, "reportedExclusionVerified": True,
        "personalLossTargetNodes": control_nodes, "inputPolicy": INPUT_POLICY,
        "fixedProtocolNoReportedModelSelection": True, "seed": seed,
        "modelVariant": "oq-profile-offbook-level18" if use_offbook else ("oq-profile" if use_oq_profile else "baseline"),
        "offbookSchema": trained["manifest"].get("offbookSchema", "") if use_offbook else "",
        "offbookLabelSource": trained["manifest"].get("offbookLabelSource", "") if use_offbook else "",
        "offbookAlgorithmVersion": trained["manifest"].get("offbookAlgorithmVersion", "") if use_offbook else "",
        "offbookNormalization": trained["manifest"].get("offbookNormalization", "") if use_offbook else "",
        "offbookSourceEngineLevel": trained["manifest"].get("offbookSourceEngineLevel", 0) if use_offbook else 0,
        "offbookEngineContract": trained["manifest"].get("offbookEngineContract", "") if use_offbook else "",
        "offbookSourceDataSha256": trained["manifest"].get("offbookSourceDataSha256", "") if use_offbook else "",
        "offbookSourceCheckpointSha256": trained["manifest"].get("offbookSourceCheckpointSha256", "") if use_offbook else "",
        "offbookRecordsSha256": trained["manifest"].get("offbookRecordsSha256", "") if use_offbook else "",
        "offbookMaterializationSha256": trained["manifest"].get("offbookMaterializationSha256", "") if use_offbook else "",
        "offbookRetrospectiveDisclosure": trained["manifest"].get("offbookRetrospectiveDisclosure", "") if use_offbook else "",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / "personal_final.pt"
    manifest_path = output_dir / "personal_manifest.json"
    if final_path.is_file() and manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if all(existing.get(key) == value for key, value in manifest.items()):
            return {**existing, "personalCheckpoint": str(final_path.resolve()), "personalCheckpointSha256": sha256_file(final_path)}
        raise ValueError(f"personal member {member_index} completed manifest mismatch")
    trained_manifest = trained["manifest"]
    profile_ablation = str(trained_manifest.get("oqProfileAblation") or "")
    if use_oq_profile:
        if trained_manifest.get("oqProfileAblationSha256") != profile_ablation_hash(profile_ablation):
            raise ValueError("base ensemble member OQ profile ablation hash mismatch")
        validation = validate_model_ready_npz(
            data_path,
            require_oq_profile=True,
            expected_oq_profile_feature_names=OQ_PROFILE_FEATURE_NAMES,
            expected_oq_profile_preprocessing_sha256=trained_manifest.get("oqProfilePreprocessingSha256"),
            expected_oq_profile_policy=trained_manifest.get("oqProfilePolicy"),
            require_offbook=use_offbook,
            expected_offbook_schema=OFFBOOK_SCHEMA,
            expected_offbook_source_data_sha256=(
                trained_manifest.get("offbookSourceDataSha256") if use_offbook else None
            ),
            expected_offbook_source_checkpoint_sha256=sha256_file(base_checkpoint) if use_offbook else None,
            expected_offbook_records_sha256=(
                trained_manifest.get("offbookRecordsSha256") if use_offbook else None
            ),
            expected_offbook_materialization_sha256=trained_manifest.get("offbookMaterializationSha256") if use_offbook else None,
        )
        model, base_payload = (
            load_transferred_offbook_profile_model(base_checkpoint, profile_ablation)
            if use_offbook else load_transferred_profile_model(base_checkpoint, profile_ablation)
        )
    else:
        validation = validate_model_ready_npz(data_path)
        model, base_payload = load_transferred_model(base_checkpoint)
    migration = (
        load_trained_state_with_offbook_migration(model, trained["modelStateDict"])
        if use_offbook else model.load_state_dict(trained["modelStateDict"], strict=True)
    )
    device = torch.device("cuda:0")
    model.to(device)
    reference = {name: parameter.detach().clone() for name, parameter in model.named_parameters()}
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.learning_rate, weight_decay=cfg.weight_decay)
    generator = torch.Generator().manual_seed(seed)
    loader = DataLoader(SequenceDataset(data_path, "train", use_oq_profile, use_offbook), batch_size=cfg.batch_size, shuffle=True,
                        num_workers=0, pin_memory=True, generator=generator)
    start_epoch = 1
    latest = output_dir / "personal_latest.pt"
    if latest.is_file():
        saved = torch.load(latest, map_location="cpu", weights_only=False)
        if saved.get("manifest") != manifest:
            raise ValueError(f"personal member {member_index} resume manifest mismatch")
        model.load_state_dict(saved["modelStateDict"], strict=True)
        optimizer.load_state_dict(saved["optimizerStateDict"])
        random.setstate(saved["pythonRandomState"]); np.random.set_state(saved["numpyRandomState"])
        torch.set_rng_state(saved["torchRandomState"]); torch.cuda.set_rng_state_all(saved["cudaRandomStates"])
        generator.set_state(saved["loaderGeneratorState"])
        start_epoch = int(saved["epoch"]) + 1
    history_path = output_dir / "personal_history.csv"
    for epoch in range(start_epoch, cfg.epochs + 1):
        model.train()
        totals = {"total": 0.0, "thinking_time": 0.0, "severity_classification": 0.0, "l2_sp": 0.0}
        for batch in loader:
            batch = _move(batch, device)
            optimizer.zero_grad(set_to_none=True)
            output = _model_output(model, batch, use_oq_profile, use_offbook)
            losses = multitask_loss(
                output, batch["actual_thinking_time_ms"].float(), batch["severity_class"].float(), batch["mask"],
                cfg.time_task_weight, cfg.severity_classification_weight, cfg.severity_class_weights,
            )
            l2_sp = sum((parameter - reference[name]).square().mean() for name, parameter in model.named_parameters())
            total = losses["total"] + cfg.l2_sp_weight * l2_sp
            total.backward(); optimizer.step()
            totals["total"] += float(total.item())
            totals["thinking_time"] += float(losses["thinking_time"].item())
            totals["severity_classification"] += float(losses["severity_classification"].item())
            totals["l2_sp"] += float(l2_sp.item())
        row = {"epoch": epoch, **{key: value / len(loader) for key, value in totals.items()}}
        with history_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            if history_path.stat().st_size == 0:
                writer.writeheader()
            writer.writerow(row)
        checkpoint = {
            "schema": "personal-tcn-offbook-checkpoint-v1" if use_offbook else "personal-tcn-checkpoint-v1", "epoch": epoch,
            "modelStateDict": model.state_dict(), "optimizerStateDict": optimizer.state_dict(),
            "manifest": manifest, "config": cfg.__dict__,
            "pythonRandomState": random.getstate(), "numpyRandomState": np.random.get_state(),
            "torchRandomState": torch.get_rng_state(), "cudaRandomStates": torch.cuda.get_rng_state_all(),
            "loaderGeneratorState": generator.get_state(),
            "stateMigration": migration,
        }
        _save_checkpoint(latest, checkpoint)
        atomic_write_json(output_dir / "personal_progress.json", {
            "schema": "personal-tcn-member-progress-v1", "status": "fine-tuning",
            "member": member_index, "epoch": epoch, "epochs": cfg.epochs, **row,
        })
    final = torch.load(latest, map_location="cpu", weights_only=False)
    _save_checkpoint(final_path, final)
    completed = {**manifest, "epochs": cfg.epochs, "learningRate": cfg.learning_rate,
                 "l2SpWeight": cfg.l2_sp_weight, "finalTrainingMetrics": row}
    atomic_write_json(manifest_path, completed)
    atomic_write_json(output_dir / "personal_progress.json", {
        "schema": "personal-tcn-member-progress-v1", "status": "completed",
        "member": member_index, "epoch": cfg.epochs, "epochs": cfg.epochs,
    })
    return {**completed, "personalCheckpoint": str(final_path.resolve()), "personalCheckpointSha256": sha256_file(final_path)}


def fine_tune_ensemble(data_path: Path, base_checkpoint: Path, ensemble_manifest_path: Path,
                       config_path: Path, output_dir: Path) -> dict[str, Any]:
    ensemble = json.loads(ensemble_manifest_path.read_text(encoding="utf-8"))
    if ensemble.get("status") != "completed" or len(ensemble.get("members", [])) == 0:
        raise ValueError("base ensemble manifest is not completed")
    output_dir.mkdir(parents=True, exist_ok=True)
    members = []
    for member in ensemble["members"]:
        index = int(member["member"])
        atomic_write_json(output_dir / "personal_ensemble_progress.json", {
            "schema": "personal-tcn-ensemble-progress-v1", "status": "fine-tuning",
            "currentMember": index, "memberCount": len(ensemble["members"]), "completedMembers": len(members),
        })
        members.append(fine_tune_member(
            data_path, base_checkpoint,
            resolve_member_checkpoint(ensemble_manifest_path, member["bestCheckpoint"]), config_path,
            output_dir / "members" / f"member_{index:02d}", index,
        ))
    manifest = {
        "schema": "personal-tcn-ensemble-manifest-v1", "status": "completed",
        "baseEnsembleManifest": str(ensemble_manifest_path.resolve()),
        "baseEnsembleManifestSha256": sha256_file(ensemble_manifest_path),
        "personalData": str(data_path.resolve()), "personalDataSha256": sha256_file(data_path),
        "members": members,
    }
    atomic_write_json(output_dir / "personal_ensemble_manifest.json", manifest)
    atomic_write_json(output_dir / "personal_ensemble_progress.json", {
        "schema": "personal-tcn-ensemble-progress-v1", "status": "completed",
        "memberCount": len(members), "completedMembers": len(members),
    })
    return manifest
