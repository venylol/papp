"""Frozen-base 64-to-4 personal residual-logit adapters trained game-equally."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.nn import functional as F

from .checkpoint import (
    load_trained_state_with_offbook_migration,
    load_transferred_model, load_transferred_offbook_profile_model,
    load_transferred_profile_model, sha256_file,
)
from .oq_profile_features import OQ_PROFILE_FEATURE_NAMES, profile_ablation_hash
from .data_contract import validate_model_ready_npz
from .offbook import OFFBOOK_SCHEMA
from .ensemble import resolve_member_checkpoint
from .feature_policy import INPUT_POLICY
from .progress import atomic_write_json


@dataclass(frozen=True)
class AdapterConfig:
    optimizer: str
    max_iter: int
    line_search_fn: str
    tolerance_grad: float
    tolerance_change: float
    kl_weight: float
    delta_w_l2_weight: float
    delta_b_l2_weight: float

    @classmethod
    def load(cls, path: Path) -> "AdapterConfig":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "tcn-loss-personal-adapter-config-v1":
            raise ValueError("personal adapter config schema mismatch")
        if payload.get("input_policy") != INPUT_POLICY:
            raise ValueError("personal adapter input policy mismatch")
        values = {key: value for key, value in payload.items() if key not in {"schema", "input_policy"}}
        cfg = cls(**values)
        if cfg.optimizer != "LBFGS" or cfg.line_search_fn != "strong_wolfe":
            raise ValueError("the formal personal adapter supports only deterministic LBFGS strong_wolfe")
        if cfg.max_iter <= 0 or min(cfg.kl_weight, cfg.delta_w_l2_weight, cfg.delta_b_l2_weight) < 0:
            raise ValueError("invalid adapter optimization configuration")
        return cfg


def _state_sha256(model: torch.nn.Module) -> str:
    digest = hashlib.sha256()
    for name, value in model.state_dict().items():
        digest.update(name.encode("utf-8")); digest.update(b"\0")
        array = value.detach().cpu().contiguous().numpy()
        digest.update(str(array.dtype).encode("ascii")); digest.update(str(array.shape).encode("ascii"))
        digest.update(array.tobytes())
    return digest.hexdigest()


def _save(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    torch.save(payload, temporary)
    os.replace(temporary, path)


def _per_game_mean(values: torch.Tensor, game_index: torch.Tensor, game_count: int) -> torch.Tensor:
    sums = torch.zeros(game_count, dtype=values.dtype, device=values.device).scatter_add(0, game_index, values)
    counts = torch.zeros(game_count, dtype=values.dtype, device=values.device).scatter_add(
        0, game_index, torch.ones_like(values)
    )
    # A retained record in which the target never moved has no eligible term.
    # It contributes zero, while remaining in the fixed all-record denominator.
    return (sums / counts.clamp_min(1)).mean()


def validate_personal_game_sets(control_ids: list[str], reported_ids: list[str]) -> None:
    if not control_ids or not reported_ids or set(control_ids) & set(reported_ids):
        raise ValueError(
            "personal adaptation requires non-empty disjoint control and reported game sets; "
            f"found {len(control_ids)} controls and {len(reported_ids)} reported games"
        )


def adapter_objective(
    hidden: torch.Tensor, base_logits: torch.Tensor, targets: torch.Tensor,
    game_index: torch.Tensor, game_count: int, delta_w: torch.Tensor, delta_b: torch.Tensor,
    cfg: AdapterConfig,
) -> dict[str, torch.Tensor]:
    personal_logits = base_logits + hidden @ delta_w + delta_b
    log_personal = F.log_softmax(personal_logits, dim=-1)
    base_probability = F.softmax(base_logits, dim=-1)
    log_base = F.log_softmax(base_logits, dim=-1)
    cross_entropy_by_node = F.nll_loss(log_personal, targets, reduction="none")
    kl_by_node = (base_probability * (log_base - log_personal)).sum(dim=-1)
    cross_entropy = _per_game_mean(cross_entropy_by_node, game_index, game_count)
    kl = _per_game_mean(kl_by_node, game_index, game_count)
    delta_w_l2 = delta_w.square().sum()
    delta_b_l2 = delta_b.square().sum()
    total = cross_entropy + cfg.kl_weight * kl + cfg.delta_w_l2_weight * delta_w_l2 + cfg.delta_b_l2_weight * delta_b_l2
    return {"total": total, "gameEqualCrossEntropy": cross_entropy, "gameEqualKlBaseToPersonal": kl,
            "deltaWL2": delta_w_l2, "deltaBL2": delta_b_l2}


def wld_adapter_objective(
    hidden: torch.Tensor, base_logits: torch.Tensor, targets: torch.Tensor,
    game_index: torch.Tensor, game_count: int, delta_w: torch.Tensor, delta_b: torch.Tensor,
    cfg: AdapterConfig,
) -> dict[str, torch.Tensor]:
    """Game-equal three-class WLD residual-adapter objective."""
    return adapter_objective(
        hidden, base_logits, targets, game_index, game_count, delta_w, delta_b, cfg
    )


def _fit_residual_adapter(
    hidden: torch.Tensor, base_logits: torch.Tensor, targets: torch.Tensor,
    game_index: torch.Tensor, game_count: int, class_count: int, cfg: AdapterConfig,
) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor], dict[str, torch.Tensor], int, float]:
    delta_w = torch.nn.Parameter(torch.zeros((hidden.shape[1], class_count), dtype=torch.float64))
    delta_b = torch.nn.Parameter(torch.zeros(class_count, dtype=torch.float64))
    base_probability = F.softmax(base_logits, dim=-1)
    initial_probability = F.softmax(base_logits + hidden @ delta_w + delta_b, dim=-1)
    identity_max_difference = float((initial_probability - base_probability).abs().max().item())
    if identity_max_difference != 0.0:
        raise AssertionError("zero-initialized personal adapter is not exactly identity")
    initial = adapter_objective(
        hidden, base_logits, targets, game_index, game_count, delta_w, delta_b, cfg
    )
    optimizer = torch.optim.LBFGS(
        [delta_w, delta_b], max_iter=cfg.max_iter, line_search_fn=cfg.line_search_fn,
        tolerance_grad=cfg.tolerance_grad, tolerance_change=cfg.tolerance_change,
    )
    evaluations = 0

    def closure() -> torch.Tensor:
        nonlocal evaluations
        optimizer.zero_grad(set_to_none=True)
        losses = adapter_objective(
            hidden, base_logits, targets, game_index, game_count, delta_w, delta_b, cfg
        )
        losses["total"].backward()
        evaluations += 1
        return losses["total"]

    optimizer.step(closure)
    final = adapter_objective(
        hidden, base_logits, targets, game_index, game_count, delta_w, delta_b, cfg
    )
    final_probability = F.softmax(base_logits + hidden @ delta_w + delta_b, dim=-1)
    if not bool(torch.isfinite(final_probability).all()) or not bool(torch.allclose(
        final_probability.sum(-1), torch.ones(len(final_probability), dtype=torch.float64), atol=1e-10
    )):
        raise ValueError("personal adapter produced invalid class probabilities")
    return delta_w, delta_b, initial, final, evaluations, identity_max_difference


@torch.no_grad()
def _extract_control_representation(data_path: Path, base_checkpoint: Path, trained_checkpoint: Path):
    trained = torch.load(trained_checkpoint, map_location="cpu", weights_only=False)
    schema = trained.get("schema")
    use_offbook = schema == "tcn-loss-profile-offbook-level18-wld-checkpoint-v1"
    use_oq_profile = use_offbook or schema in {"tcn-loss-profile-checkpoint-v1", "tcn-loss-profile-wld-checkpoint-v2"}
    if schema not in {
        "tcn-loss-checkpoint-v1", "tcn-loss-wld-checkpoint-v2",
        "tcn-loss-profile-checkpoint-v1", "tcn-loss-profile-wld-checkpoint-v2",
        "tcn-loss-profile-offbook-level18-wld-checkpoint-v1",
    }:
        raise ValueError("ensemble member checkpoint schema mismatch")
    if trained["manifest"].get("baseCheckpointSha256") != sha256_file(base_checkpoint):
        raise ValueError("ensemble member official checkpoint mismatch")
    trained_manifest = trained["manifest"]
    profile_ablation = str(trained_manifest.get("oqProfileAblation") or "")
    if use_oq_profile:
        if trained_manifest.get("oqProfileAblationSha256") != profile_ablation_hash(profile_ablation):
            raise ValueError("ensemble member OQ profile ablation hash mismatch")
        validate_model_ready_npz(
            data_path, require_oq_profile=True,
            expected_oq_profile_feature_names=OQ_PROFILE_FEATURE_NAMES,
            expected_oq_profile_preprocessing_sha256=trained_manifest.get("oqProfilePreprocessingSha256"),
            expected_oq_profile_policy=trained_manifest.get("oqProfilePolicy"),
            require_offbook=use_offbook,
            expected_offbook_schema=OFFBOOK_SCHEMA,
            expected_offbook_source_data_sha256=(
                trained_manifest.get("offbookSourceDataSha256") if use_offbook else None
            ),
            expected_offbook_source_checkpoint_sha256=(sha256_file(base_checkpoint) if use_offbook else None),
            expected_offbook_records_sha256=(
                trained_manifest.get("offbookRecordsSha256") if use_offbook else None
            ),
            expected_offbook_materialization_sha256=(
                trained_manifest.get("offbookMaterializationSha256") if use_offbook else None
            ),
        )
        model, base_payload = (
            load_transferred_offbook_profile_model(base_checkpoint, profile_ablation)
            if use_offbook else load_transferred_profile_model(base_checkpoint, profile_ablation)
        )
    else:
        validate_model_ready_npz(data_path)
        model, base_payload = load_transferred_model(base_checkpoint)
    if use_offbook:
        load_trained_state_with_offbook_migration(model, trained["modelStateDict"])
    else:
        model.load_state_dict(trained["modelStateDict"], strict=True)
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    model.eval()
    before_hash = _state_sha256(model)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)
    with np.load(data_path, allow_pickle=False) as data:
        splits = data["split"].astype(str)
        selected_games = np.flatnonzero(splits == "train")
        game_ids = data["game_id"].astype(str)[selected_games].tolist()
        reported = data["game_id"].astype(str)[splits == "test"].tolist()
        hidden_parts, logits_parts, target_parts, game_parts, counts = [], [], [], [], []
        wld_hidden_parts, wld_logits_parts, wld_target_parts, wld_game_parts, wld_counts = [], [], [], [], []
        for local_game_index, source_game_index in enumerate(selected_games):
            sl = slice(source_game_index, source_game_index + 1)
            model_args = (
                torch.from_numpy(data["X"][sl]).float().to(device),
                torch.from_numpy(data["board_tokens"][sl]).to(device),
                torch.from_numpy(data["board_move_tokens"][sl]).to(device),
                torch.from_numpy(data["current_hint_tokens"][sl]).to(device),
                torch.from_numpy(data["current_hint_values"][sl]).float().to(device),
                torch.from_numpy(data["prev_own_hint_values"][sl]).float().to(device),
                torch.from_numpy(data["actual_thinking_time_ms"][sl]).float().to(device),
            )
            if use_oq_profile:
                profile_args = (
                    torch.from_numpy(data["oq_profile_features"][sl]).float().to(device),
                    torch.from_numpy(data["oq_profile_missing"][sl]).to(device),
                )
                output = (
                    model(
                        *model_args,
                        torch.from_numpy(data["offbook_feature"][sl]).float().to(device),
                        *profile_args,
                    )
                    if use_offbook else model(*model_args, *profile_args)
                )
            else:
                output = model(*model_args)
            mask = torch.from_numpy(data["mask"][sl]).bool().to(device)
            node_count = int(mask.sum().item())
            if node_count:
                hidden_parts.append(output.severity_hidden[mask].detach().cpu().double())
                logits_parts.append(output.severity_logits[mask].detach().cpu().double())
                target_parts.append(torch.from_numpy(data["severity_class"][sl])[mask.cpu()].long())
                game_parts.append(torch.full((node_count,), local_game_index, dtype=torch.long))
            counts.append(node_count)
            wld_mask = mask & torch.from_numpy(
                data["wld_label_available"][sl]
                & (data["global_placement_ply"][sl] >= 39)
            ).bool().to(device)
            wld_node_count = int(wld_mask.sum().item())
            if wld_node_count:
                wld_hidden_parts.append(output.severity_hidden[wld_mask].detach().cpu().double())
                wld_logits_parts.append(output.wld_logits[wld_mask].detach().cpu().double())
                wld_target_parts.append(torch.from_numpy(data["wld_class"][sl])[wld_mask.cpu()].long())
                wld_game_parts.append(torch.full((wld_node_count,), local_game_index, dtype=torch.long))
            wld_counts.append(wld_node_count)
    after_hash = _state_sha256(model)
    if before_hash != after_hash:
        raise AssertionError("frozen base model parameters changed during representation extraction")
    return (
        torch.cat(hidden_parts), torch.cat(logits_parts), torch.cat(target_parts), torch.cat(game_parts),
        torch.cat(wld_hidden_parts), torch.cat(wld_logits_parts), torch.cat(wld_target_parts),
        torch.cat(wld_game_parts), game_ids, reported, counts, wld_counts, before_hash,
        base_payload, use_oq_profile, use_offbook, profile_ablation,
    )


def train_adapter_member(data_path: Path, base_checkpoint: Path, trained_checkpoint: Path,
                         config_path: Path, output_dir: Path, member_index: int) -> dict[str, Any]:
    started = time.perf_counter()
    cfg = AdapterConfig.load(config_path)
    trained_manifest = torch.load(trained_checkpoint, map_location="cpu", weights_only=False)["manifest"]
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "personal_adapter.pt"
    manifest_path = output_dir / "personal_adapter_manifest.json"
    expected = {
        "member": member_index, "baseEnsembleCheckpointSha256": sha256_file(trained_checkpoint),
        "personalDataSha256": sha256_file(data_path), "configSha256": sha256_file(config_path),
        "adapterSchema": "personal-tcn-adapter-v2",
    }
    if checkpoint_path.is_file() and manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if all(existing.get(key) == value for key, value in expected.items()):
            return {**existing, "personalCheckpoint": str(checkpoint_path.resolve()),
                    "personalCheckpointSha256": sha256_file(checkpoint_path)}
        raise ValueError(f"personal adapter member {member_index} manifest mismatch")
    (hidden, base_logits, targets, game_index, wld_hidden, wld_base_logits, wld_targets,
     wld_game_index, control_ids, reported_ids, node_counts, wld_node_counts, frozen_hash,
     base_payload, use_oq_profile, use_offbook, profile_ablation) = _extract_control_representation(
        data_path, base_checkpoint, trained_checkpoint
    )
    validate_personal_game_sets(control_ids, reported_ids)
    if hidden.shape[1] != 64:
        raise ValueError(f"severity_hidden must have 64 features, got {hidden.shape}")
    if wld_hidden.shape[1] != 64 or not len(wld_hidden):
        raise ValueError(f"WLD adapter requires non-empty 64-feature hidden states, got {wld_hidden.shape}")
    delta_w, delta_b, initial, final, evaluations, identity_max_difference = _fit_residual_adapter(
        hidden, base_logits, targets, game_index, len(control_ids), 4, cfg
    )
    wld_delta_w, wld_delta_b, wld_initial, wld_final, wld_evaluations, wld_identity_max_difference = _fit_residual_adapter(
        wld_hidden, wld_base_logits, wld_targets, wld_game_index, len(control_ids), 3, cfg
    )
    time_policy = ""
    with np.load(data_path, allow_pickle=False) as data:
        if "time_control_policy" in data.files:
            time_policy = str(data["time_control_policy"].item())
    manifest = {
        "schema": "personal-tcn-adapter-manifest-v2", **expected,
        "baseEnsembleCheckpoint": str(trained_checkpoint.resolve()),
        "officialBaseCheckpoint": str(base_checkpoint.resolve()),
        "officialBaseCheckpointSha256": sha256_file(base_checkpoint),
        "controlGameIds": control_ids, "controlNodeCountsByGame": dict(zip(control_ids, node_counts, strict=True)),
        "wldControlNodeCountsByGame": dict(zip(control_ids, wld_node_counts, strict=True)),
        "wldApplicableNodeCount": int(sum(wld_node_counts)),
        "wldApplicabilityPolicy": "wld_label_available and global_placement_ply >= 39",
        "zeroTargetNodeControlGameIds": [game_id for game_id, count in zip(control_ids, node_counts, strict=True) if count == 0],
        "zeroTargetNodeConvention": "retained in the fixed control-game denominator with zero CE and KL contribution because the target player had no eligible node",
        "reportedExcludedGameIds": reported_ids, "reportedExclusionVerified": True,
        "gameEqualWeighting": True,
        "adapterShape": {
            "severity": {"deltaW": [64, 4], "deltaB": [4]},
            "wld": {"deltaW": [64, 3], "deltaB": [3]},
        },
        "trainableParameterCount": 455,
        "zeroInitializationIdentityMaxAbsDifference": identity_max_difference,
        "wldZeroInitializationIdentityMaxAbsDifference": wld_identity_max_difference,
        "allBaseParametersFrozen": True, "frozenBaseStateSha256BeforeAndAfter": frozen_hash,
        "modelVariant": "oq-profile-offbook-level18" if use_offbook else ("oq-profile" if use_oq_profile else "baseline"),
        "offbookSchema": trained_manifest.get("offbookSchema", "") if use_offbook else "",
        "offbookLabelSource": trained_manifest.get("offbookLabelSource", "") if use_offbook else "",
        "offbookAlgorithmVersion": trained_manifest.get("offbookAlgorithmVersion", "") if use_offbook else "",
        "offbookNormalization": trained_manifest.get("offbookNormalization", "") if use_offbook else "",
        "offbookSourceEngineLevel": trained_manifest.get("offbookSourceEngineLevel", 0) if use_offbook else 0,
        "offbookEngineContract": trained_manifest.get("offbookEngineContract", "") if use_offbook else "",
        "offbookSourceDataSha256": trained_manifest.get("offbookSourceDataSha256", "") if use_offbook else "",
        "offbookSourceCheckpointSha256": trained_manifest.get("offbookSourceCheckpointSha256", "") if use_offbook else "",
        "offbookRecordsSha256": trained_manifest.get("offbookRecordsSha256", "") if use_offbook else "",
        "offbookMaterializationSha256": trained_manifest.get("offbookMaterializationSha256", "") if use_offbook else "",
        "offbookRetrospectiveDisclosure": trained_manifest.get("offbookRetrospectiveDisclosure", "") if use_offbook else "",
        "oqProfileAblation": profile_ablation if use_oq_profile else "",
        "timeControlPolicy": time_policy, "optimizer": asdict(cfg), "optimizerEvaluations": evaluations,
        "initialControlLoss": {key: float(value.detach()) for key, value in initial.items()},
        "finalControlLoss": {key: float(value.detach()) for key, value in final.items()},
        "initialWldControlLoss": {key: float(value.detach()) for key, value in wld_initial.items()},
        "finalWldControlLoss": {key: float(value.detach()) for key, value in wld_final.items()},
        "averageKlDrift": float(final["gameEqualKlBaseToPersonal"].detach()),
        "averageWldKlDrift": float(wld_final["gameEqualKlBaseToPersonal"].detach()),
        "wldOptimizerEvaluations": wld_evaluations,
        "fixedProtocolNoPersonalValidation": True, "fixedProtocolNoReportedStopping": True,
        "elapsedSeconds": time.perf_counter() - started,
    }
    checkpoint = {
        "schema": "personal-tcn-adapter-v2",
        "deltaW": delta_w.detach().cpu(), "deltaB": delta_b.detach().cpu(),
        "wldDeltaW": wld_delta_w.detach().cpu(), "wldDeltaB": wld_delta_b.detach().cpu(),
        "manifest": manifest,
    }
    _save(checkpoint_path, checkpoint)
    atomic_write_json(manifest_path, manifest)
    return {**manifest, "personalCheckpoint": str(checkpoint_path.resolve()),
            "personalCheckpointSha256": sha256_file(checkpoint_path)}


def train_adapter_ensemble(data_path: Path, base_checkpoint: Path, ensemble_manifest_path: Path,
                           config_path: Path, output_dir: Path) -> dict[str, Any]:
    started_clock = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    ensemble = json.loads(ensemble_manifest_path.read_text(encoding="utf-8"))
    members = ensemble.get("members", [])
    if ensemble.get("status") != "completed" or len(members) not in {1, 12}:
        raise ValueError("base ensemble must be a completed one-member smoke or formal twelve-member ensemble")
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for source_member in members:
        index = int(source_member["member"])
        atomic_write_json(output_dir / "personal_ensemble_progress.json", {
            "schema": "personal-tcn-adapter-ensemble-progress-v2", "status": "training",
            "currentMember": index, "memberCount": len(members), "completedMembers": len(results),
        })
        results.append(train_adapter_member(
            data_path, base_checkpoint,
            resolve_member_checkpoint(ensemble_manifest_path, source_member["bestCheckpoint"]), config_path,
            output_dir / "members" / f"member_{index:02d}", index,
        ))
    manifest = {
        "schema": "personal-tcn-adapter-ensemble-manifest-v2", "status": "completed",
        "baseEnsembleManifest": str(ensemble_manifest_path.resolve()),
        "baseEnsembleManifestSha256": sha256_file(ensemble_manifest_path),
        "personalData": str(data_path.resolve()), "personalDataSha256": sha256_file(data_path),
        "memberCount": len(results), "members": results,
        "startedAtUtc": started_at, "completedAtUtc": datetime.now(timezone.utc).isoformat(),
        "elapsedSeconds": time.perf_counter() - started_clock,
    }
    atomic_write_json(output_dir / "personal_ensemble_manifest.json", manifest)
    atomic_write_json(output_dir / "personal_ensemble_progress.json", {
        "schema": "personal-tcn-adapter-ensemble-progress-v2", "status": "completed",
        "memberCount": len(results), "completedMembers": len(results),
    })
    return manifest
