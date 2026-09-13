"""Deterministic, resumable sequential training for a fixed-test TCN ensemble."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .checkpoint import sha256_file
from .progress import atomic_write_json
from .training import TrainingConfig, train_cuda


def resolve_member_checkpoint(manifest_path: Path, checkpoint: str | Path) -> Path:
    """Resolve a member checkpoint, including portable manifest-relative paths."""
    path = Path(checkpoint)
    return path if path.is_absolute() else manifest_path.resolve().parent / path


def _ids_hash(game_ids: Iterable[str]) -> str:
    body = "\n".join(sorted(map(str, game_ids))).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def deterministic_fixed_test_split(
    game_ids: np.ndarray, original_splits: np.ndarray, seed: int, validation_fraction: float = 0.10
) -> tuple[np.ndarray, dict[str, Any]]:
    game_ids = game_ids.astype(str)
    original_splits = original_splits.astype(str)
    test = original_splits == "test"
    pool_indexes = np.flatnonzero(~test)
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(pool_indexes)
    validation_count = int(round(len(pool_indexes) * validation_fraction))
    validation_count = max(1, min(validation_count, len(pool_indexes) - 1))
    validation_indexes = shuffled[:validation_count]
    train_indexes = shuffled[validation_count:]
    result = np.full(len(game_ids), "test", dtype="U10")
    result[train_indexes] = "train"
    result[validation_indexes] = "validation"
    if not np.array_equal(result == "test", test):
        raise AssertionError("fixed test membership changed")
    if len(set(game_ids)) != len(game_ids):
        raise ValueError("game_id must be unique before ensemble resplitting")
    summary = {
        "seed": int(seed),
        "trainGames": int((result == "train").sum()),
        "validationGames": int((result == "validation").sum()),
        "testGames": int((result == "test").sum()),
        "trainGameIdsSha256": _ids_hash(game_ids[result == "train"]),
        "validationGameIdsSha256": _ids_hash(game_ids[result == "validation"]),
        "testGameIdsSha256": _ids_hash(game_ids[result == "test"]),
    }
    return result, summary


def materialize_split_view(source_path: Path, output_path: Path, seed: int) -> dict[str, Any]:
    with np.load(source_path, allow_pickle=False) as source:
        split, summary = deterministic_fixed_test_split(source["game_id"], source["split"], seed)
        arrays = {name: source[name] for name in source.files}
        arrays["split"] = split
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, **arrays)
    temporary.replace(output_path)
    return {**summary, "path": str(output_path.resolve()), "sha256": sha256_file(output_path)}


def _write_member_config(base_config: Path, output_path: Path, seed: int) -> None:
    payload = json.loads(base_config.read_text(encoding="utf-8"))
    payload["training"]["seed"] = int(seed)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _member_complete(
    member_dir: Path,
    expected: dict[str, Any],
    required_max_epochs: int | None = None,
    evaluate_test: bool = True,
) -> bool:
    paths = [member_dir / "progress.json", member_dir / "run_manifest.json", member_dir / "best.pt"]
    if evaluate_test:
        paths.append(member_dir / "test_metrics.json")
    else:
        paths.append(member_dir / "validation_only_completion.json")
    if not all(path.is_file() for path in paths):
        return False
    progress = json.loads(paths[0].read_text(encoding="utf-8"))
    manifest = json.loads(paths[1].read_text(encoding="utf-8"))
    expected_status = "completed" if evaluate_test else "completed-validation-only"
    return (
        progress.get("status") == expected_status
        and (required_max_epochs is None or int(progress.get("max_epochs", 0)) >= required_max_epochs)
        and all(manifest.get(key) == value for key, value in expected.items())
    )


def _warm_start_manifest_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.is_file():
        return resolved
    candidate = resolved / "ensemble_manifest.json"
    if not candidate.is_file():
        raise FileNotFoundError(f"warm-start ensemble manifest is missing: {candidate}")
    return candidate


def resolve_warm_start_member(
    warm_start_ensemble: Path, seed: int, checkpoint_name: str = "best.pt"
) -> tuple[Path, Path, dict[str, Any]]:
    """Resolve an official member strictly by seed from its ensemble manifest."""

    manifest_path = _warm_start_manifest_path(warm_start_ensemble)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    members = [item for item in manifest.get("members", []) if int(item.get("seed", -1)) == int(seed)]
    if len(members) != 1:
        raise ValueError(f"warm-start ensemble must contain exactly one member for seed {seed}, found {len(members)}")
    member = members[0]
    key = "bestCheckpoint" if checkpoint_name == "best.pt" else "latestCheckpoint"
    checkpoint_value = member.get(key)
    if not checkpoint_value:
        raise FileNotFoundError(f"warm-start manifest has no {key} for seed {seed}")
    checkpoint_path = resolve_member_checkpoint(manifest_path, checkpoint_value)
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"warm-start checkpoint for seed {seed} is missing: {checkpoint_path}")
    return checkpoint_path, manifest_path, member


def train_ensemble(
    data_path: Path,
    base_checkpoint: Path,
    config_path: Path,
    output_dir: Path,
    seeds: list[int],
    context_metadata: Path | None = None,
    use_oq_profile: bool = False,
    use_offbook: bool = False,
    extend_completed: bool = False,
    fixed_split: bool = False,
    warm_start_ensemble: Path | None = None,
    warm_start_checkpoint_name: str = "best.pt",
    evaluate_test: bool = True,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "ensemble_progress.json"
    members: list[dict[str, Any]] = []
    source_hash = sha256_file(data_path)
    base_hash = sha256_file(base_checkpoint)
    target_max_epochs = TrainingConfig.load(config_path).head_epochs + TrainingConfig.load(config_path).fine_tune_epochs
    if use_offbook and not use_oq_profile:
        raise ValueError("Level18 offbook ensemble requires OQ profile training")
    if warm_start_ensemble is not None and not use_oq_profile:
        raise ValueError("warm-start ensemble requires profile training")
    if warm_start_checkpoint_name not in {"best.pt", "latest.pt"}:
        raise ValueError("warm_start_checkpoint_name must be 'best.pt' or 'latest.pt'")
    for index, seed in enumerate(seeds, start=1):
        member_dir = output_dir / "members" / f"member_{index:02d}_seed_{seed}"
        member_dir.mkdir(parents=True, exist_ok=True)
        split_path = data_path if fixed_split else output_dir / "splits" / f"seed_{seed}.npz"
        split_manifest_path = output_dir / "splits" / f"seed_{seed}.json"
        if fixed_split:
            split_manifest = {
                "schema": "tcn-loss-ensemble-fixed-split-v1", "seed": seed,
                "sourceDataSha256": source_hash, "sha256": source_hash,
                "path": str(data_path.resolve()),
            }
        elif split_path.is_file() and split_manifest_path.is_file():
            split_manifest = json.loads(split_manifest_path.read_text(encoding="utf-8"))
            if split_manifest.get("seed") != seed or split_manifest.get("sourceDataSha256") != source_hash or split_manifest.get("sha256") != sha256_file(split_path):
                raise ValueError(f"member {index} split manifest mismatch")
        else:
            if split_path.exists() or split_manifest_path.exists():
                raise FileExistsError(f"member {index} has an incomplete split artifact pair")
            split_manifest = materialize_split_view(data_path, split_path, seed)
            split_manifest.update({"schema": "tcn-loss-ensemble-split-v1", "sourceDataSha256": source_hash})
            atomic_write_json(split_manifest_path, split_manifest)
        member_config = output_dir / "configs" / (
            f"seed_{seed}_epochs_{target_max_epochs}.json" if extend_completed else f"seed_{seed}.json"
        )
        member_config.parent.mkdir(parents=True, exist_ok=True)
        if not member_config.exists():
            _write_member_config(config_path, member_config, seed)
        cfg = TrainingConfig.load(member_config)
        expected = {
            "dataSha256": split_manifest["sha256"],
            "baseCheckpointSha256": base_hash,
        }
        atomic_write_json(progress_path, {
            "schema": "tcn-loss-ensemble-progress-v1", "status": "training",
            "currentMember": index, "currentSeed": seed, "memberCount": len(seeds),
            "completedMembers": len(members), "seeds": seeds,
            "modelVariant": "oq-profile-offbook-level18" if use_offbook else ("oq-profile" if use_oq_profile else "baseline"),
            "targetMaxEpochs": target_max_epochs,
        })
        if not _member_complete(
            member_dir, expected, target_max_epochs if extend_completed else None, evaluate_test=evaluate_test
        ):
            resume = member_dir / "latest.pt"
            initial_profile_checkpoint = None
            initial_offbook_checkpoint = None
            if warm_start_ensemble is not None:
                warm_path, warm_manifest_path, warm_member = resolve_warm_start_member(
                    warm_start_ensemble, seed, warm_start_checkpoint_name
                )
                if use_offbook:
                    initial_offbook_checkpoint = warm_path
                else:
                    initial_profile_checkpoint = warm_path
            else:
                warm_manifest_path = None
                warm_member = None
            train_cuda(
                split_path, base_checkpoint, member_config, member_dir,
                f"ensemble_member_{index:02d}_seed_{seed}", context_metadata,
                resume if resume.is_file() else None,
                evaluate_test=evaluate_test,
                use_oq_profile=use_oq_profile,
                initial_profile_checkpoint=initial_profile_checkpoint,
                use_offbook=use_offbook,
                initial_offbook_checkpoint=initial_offbook_checkpoint,
            )
        run_manifest = json.loads((member_dir / "run_manifest.json").read_text(encoding="utf-8"))
        test_metrics = (
            json.loads((member_dir / "test_metrics.json").read_text(encoding="utf-8"))
            if evaluate_test else None
        )
        validation_metrics_path = member_dir / "final_validation_metrics.json"
        if not validation_metrics_path.is_file():
            validation_metrics_path = member_dir / "best_validation_metrics.json"
        validation_metrics = json.loads(validation_metrics_path.read_text(encoding="utf-8"))
        member_progress = json.loads((member_dir / "progress.json").read_text(encoding="utf-8"))
        member_record = {
            "member": index, "seed": seed, "split": split_manifest,
            "bestCheckpoint": str((member_dir / "best.pt").resolve()),
            "bestCheckpointSha256": sha256_file(member_dir / "best.pt"),
            "bestEpoch": member_progress["best_epoch"],
            "bestSemanticEpoch": int(run_manifest.get("warmStartEpoch", 0)) + int(member_progress["best_epoch"]),
            "selectionMetric": run_manifest.get("selectionMetric", "validation_total_loss"),
            "bestValidationMetric": member_progress["best_metric"],
            "validationMetrics": validation_metrics,
            "testMetrics": test_metrics,
            "runManifest": run_manifest,
            "trainingConfig": asdict(cfg),
        }
        if member_record["selectionMetric"] == "validation_total_loss":
            member_record["bestValidationTotalLoss"] = member_progress["best_metric"]
        else:
            member_record["bestValidationWldClassificationLoss"] = member_progress["best_metric"]
        members.append(member_record)
    manifest = {
        "schema": "tcn-loss-ensemble-manifest-v1", "status": "completed",
        "sourceData": str(data_path.resolve()), "sourceDataSha256": source_hash,
        "baseCheckpoint": str(base_checkpoint.resolve()), "baseCheckpointSha256": base_hash,
        "modelVariant": "oq-profile-offbook-level18" if use_offbook else ("oq-profile" if use_oq_profile else "baseline"),
        "offbookSchema": run_manifest.get("offbookSchema", "") if use_offbook else "",
        "offbookLabelSource": run_manifest.get("offbookLabelSource", "") if use_offbook else "",
        "offbookAlgorithmVersion": run_manifest.get("offbookAlgorithmVersion", "") if use_offbook else "",
        "offbookNormalization": run_manifest.get("offbookNormalization", "") if use_offbook else "",
        "offbookSourceEngineLevel": run_manifest.get("offbookSourceEngineLevel", 0) if use_offbook else 0,
        "offbookEngineContract": run_manifest.get("offbookEngineContract", "") if use_offbook else "",
        "offbookSourceDataSha256": run_manifest.get("offbookSourceDataSha256", "") if use_offbook else "",
        "offbookSourceCheckpointSha256": run_manifest.get("offbookSourceCheckpointSha256", "") if use_offbook else "",
        "offbookRecordsSha256": run_manifest.get("offbookRecordsSha256", "") if use_offbook else "",
        "offbookMaterializationSha256": run_manifest.get("offbookMaterializationSha256", "") if use_offbook else "",
        "offbookRetrospectiveDisclosure": run_manifest.get("offbookRetrospectiveDisclosure", "") if use_offbook else "",
        "testEvaluationPerformed": bool(evaluate_test),
        "targetMaxEpochs": target_max_epochs,
        "fixedSplit": fixed_split,
        "warmStartEnsemble": str(_warm_start_manifest_path(warm_start_ensemble).resolve()) if warm_start_ensemble else "",
        "warmStartCheckpointName": warm_start_checkpoint_name if warm_start_ensemble else "",
        "seeds": seeds, "members": members,
    }
    atomic_write_json(output_dir / "ensemble_manifest.json", manifest)
    atomic_write_json(progress_path, {
        "schema": "tcn-loss-ensemble-progress-v1", "status": "completed",
        "memberCount": len(seeds), "completedMembers": len(members), "seeds": seeds,
        "modelVariant": "oq-profile-offbook-level18" if use_offbook else ("oq-profile" if use_oq_profile else "baseline"),
        "targetMaxEpochs": target_max_epochs,
    })
    return manifest
