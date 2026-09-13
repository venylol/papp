"""Explicit inference for trained severity/time/WLD checkpoints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from .checkpoint import (
    load_checkpoint_payload, load_trained_state_with_offbook_migration,
    load_trained_state_with_wld_migration, load_transferred_model,
    load_transferred_offbook_profile_model, load_transferred_profile_model,
    sha256_file,
)
from .data_contract import validate_model_ready_npz
from .offbook import OFFBOOK_SCHEMA
from .oq_profile_features import OQ_PROFILE_FEATURE_NAMES, profile_ablation_hash


def _json_hash(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


@torch.no_grad()
def predict_to_csv(data_path: Path, trained_checkpoint: Path, base_checkpoint: Path,
                   output_path: Path, device_name: str = "cpu", batch_size: int = 64,
                   use_oq_profile: bool = False, use_offbook: bool = False) -> dict:
    if device_name.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("requested CUDA inference but torch.cuda.is_available() is false")
    base_payload = load_checkpoint_payload(base_checkpoint)
    trained = torch.load(trained_checkpoint, map_location="cpu", weights_only=False)
    if use_offbook and not use_oq_profile:
        raise ValueError("Level18 offbook inference requires OQ profile context")
    expected_schema = (
        "tcn-loss-profile-offbook-level18-wld-checkpoint-v1"
        if use_offbook else ("tcn-loss-profile-wld-checkpoint-v2" if use_oq_profile else "tcn-loss-wld-checkpoint-v2")
    )
    if trained.get("schema") != expected_schema:
        raise ValueError(f"trained checkpoint schema mismatch: expected {expected_schema!r}")
    trained_manifest = trained["manifest"]
    validation = validate_model_ready_npz(
        data_path, expected_input_features=base_payload["input_features"],
        expected_board_channels=base_payload["board_encoding"]["cnn_channels"],
        expected_preprocessing_sha256=_json_hash(base_payload["preprocessing"]),
        require_oq_profile=use_oq_profile,
        expected_oq_profile_feature_names=OQ_PROFILE_FEATURE_NAMES if use_oq_profile else None,
        expected_oq_profile_preprocessing_sha256=(
            trained_manifest.get("oqProfilePreprocessingSha256") if use_oq_profile else None
        ),
        expected_oq_profile_policy=trained_manifest.get("oqProfilePolicy") if use_oq_profile else None,
        require_offbook=use_offbook,
        expected_offbook_schema=OFFBOOK_SCHEMA,
        expected_offbook_source_data_sha256=(
            trained_manifest.get("offbookSourceDataSha256") if use_offbook else None
        ),
        expected_offbook_source_checkpoint_sha256=sha256_file(base_checkpoint) if use_offbook else None,
        expected_offbook_records_sha256=(
            trained_manifest.get("offbookRecordsSha256") if use_offbook else None
        ),
        expected_offbook_materialization_sha256=(
            trained_manifest.get("offbookMaterializationSha256") if use_offbook else None
        ),
    )
    if trained_manifest.get("baseCheckpointSha256") != sha256_file(base_checkpoint):
        raise ValueError("trained checkpoint was initialized from a different base checkpoint")
    profile_ablation = str(trained_manifest.get("oqProfileAblation") or "")
    if use_oq_profile:
        if trained_manifest.get("oqProfileAblationSha256") != profile_ablation_hash(profile_ablation):
            raise ValueError("trained checkpoint OQ profile ablation hash mismatch")
        if trained_manifest.get("oqProfileFeatureOrderSha256") != hashlib.sha256(
            "\n".join(OQ_PROFILE_FEATURE_NAMES).encode("utf-8")
        ).hexdigest():
            raise ValueError("trained checkpoint OQ profile feature-order hash mismatch")
        model, _ = (
            load_transferred_offbook_profile_model(base_checkpoint, profile_ablation)
            if use_offbook else load_transferred_profile_model(base_checkpoint, profile_ablation)
        )
    else:
        model, _ = load_transferred_model(base_checkpoint)
    migration = (
        load_trained_state_with_offbook_migration(model, trained["modelStateDict"])
        if use_offbook else load_trained_state_with_wld_migration(model, trained["modelStateDict"])
    )
    if not use_offbook and migration["migratedLegacyCheckpoint"]:
        raise ValueError("legacy checkpoint has an untrained WLD head and cannot produce calibrated WLD inference")
    device = torch.device(device_name)
    model.to(device).eval()
    with np.load(data_path, allow_pickle=False) as data:
        games, steps = data["X"].shape[:2]
        class_probabilities = np.zeros((games, steps, 4), dtype=np.float32)
        predicted_time_log = np.zeros((games, steps), dtype=np.float32)
        wld_probabilities = np.zeros((games, steps, 3), dtype=np.float32)
        for start in range(0, games, batch_size):
            stop = min(start + batch_size, games)
            sl = slice(start, stop)
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
                if use_offbook:
                    output = model(
                        *model_args,
                        torch.from_numpy(data["offbook_feature"][sl]).float().to(device),
                        *profile_args,
                    )
                else:
                    output = model(*model_args, *profile_args)
            else:
                output = model(*model_args)
            class_probabilities[sl] = output.severity_class_probabilities.cpu().numpy()
            predicted_time_log[sl] = output.pred_time_log_seconds.cpu().numpy()
            wld_probabilities[sl] = output.wld_probabilities.cpu().numpy()
        valid = data["mask"].astype(bool)
        game_grid = np.broadcast_to(data["game_id"][:, None], (games, steps))
        split_grid = np.broadcast_to(data["split"][:, None], (games, steps))
        p_zero = class_probabilities[..., 0]
        p_positive = 1 - p_zero
        p_ge4 = class_probabilities[..., 2] + class_probabilities[..., 3]
        p_ge10 = class_probabilities[..., 3]
        probability_wld_any = wld_probabilities[..., 1] + wld_probabilities[..., 2]
        expected_wld_loss = 0.5 * wld_probabilities[..., 1] + wld_probabilities[..., 2]
        applicable = data["global_placement_ply"] >= 39
        if np.any(p_ge10 > p_ge4 + 1e-6) or np.any(p_ge4 > p_positive + 1e-6):
            raise AssertionError("inference probability monotonicity failure")
        columns = {
            "game_id": game_grid[valid], "player_id": data["player_id"][valid],
            "move_index": data["move_index"][valid],
            "source_ply_including_pass": data["source_ply_including_pass"][valid],
            "global_placement_ply": data["global_placement_ply"][valid],
            "side_to_move": data["side_to_move"][valid], "split": split_grid[valid],
            "actual_thinking_time_ms": data["actual_thinking_time_ms"][valid],
            "predicted_thinking_time_ms": np.expm1(predicted_time_log[valid]) * 1000,
            "raw_loss": data["raw_loss"][valid], "actual_disc_loss": data["disc_loss"][valid],
            "actual_loss_zero": data["label_zero"][valid],
            "actual_loss_ge4": data["label_ge4"][valid], "actual_loss_ge10": data["label_ge10"][valid],
            "probability_loss_zero": p_zero[valid], "probability_loss_positive": p_positive[valid],
            "probability_loss_ge4": p_ge4[valid], "probability_loss_ge10": p_ge10[valid],
            "probability_class_zero": class_probabilities[..., 0][valid],
            "probability_class_1_3": class_probabilities[..., 1][valid],
            "probability_class_4_9": class_probabilities[..., 2][valid],
            "probability_class_ge10": class_probabilities[..., 3][valid],
            "wld_applicable": applicable[valid],
            "wld_label_available": data["wld_label_available"][valid],
            "actual_wld_loss": np.where(data["wld_label_available"], data["wld_loss"], np.nan)[valid],
            "probability_class_no_wld_loss": np.where(applicable, wld_probabilities[..., 0], np.nan)[valid],
            "probability_class_half_wld_loss": np.where(applicable, wld_probabilities[..., 1], np.nan)[valid],
            "probability_class_full_wld_loss": np.where(applicable, wld_probabilities[..., 2], np.nan)[valid],
            "probability_wld_any": np.where(applicable, probability_wld_any, np.nan)[valid],
            "expected_wld_loss": np.where(applicable, expected_wld_loss, np.nan)[valid],
            "label_available": data["label_available"][valid],
            "has_consecutive_child": data["has_consecutive_child"][valid],
            "child_continuity_ok": data["child_continuity_ok"][valid],
            "same_side_after_move": data["same_side_after_move"][valid],
        }
        if use_oq_profile:
            raw_profile = data["oq_profile_raw_features"]
            missing_profile = data["oq_profile_missing"]
            for index, name in enumerate(OQ_PROFILE_FEATURE_NAMES):
                columns[f"raw_{name}"] = raw_profile[..., index][valid]
                columns[f"missing_{name}"] = missing_profile[..., index][valid]
            columns["oq_profile_policy"] = np.full(int(valid.sum()), str(data["oq_profile_policy"].item()))
            columns["oq_profile_preprocessing_sha256"] = np.full(
                int(valid.sum()), str(data["oq_profile_preprocessing_sha256"].item())
            )
        if use_offbook:
            columns["offbook_ply"] = data["offbook_ply"][valid]
            columns["offbook_present"] = data["offbook_present"][valid]
            columns["offbook_feature"] = data["offbook_feature"][valid]
            columns["offbook_schema"] = np.full(int(valid.sum()), str(data["offbook_schema"].item()))
            columns["offbook_materialization_sha256"] = np.full(
                int(valid.sum()), str(data["offbook_materialization_sha256"].item())
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns).to_csv(output_path, index=False, encoding="utf-8")
    return {
        "ok": True, "rows": int(valid.sum()), "output": str(output_path.resolve()),
        "modelVariant": "oq-profile-offbook-level18" if use_offbook else ("oq-profile" if use_oq_profile else "baseline"),
        "oqProfileAblation": profile_ablation if use_oq_profile else "",
        "wldMinimumGlobalPlacementPlyInclusive": 39,
        "data": validation,
    }
