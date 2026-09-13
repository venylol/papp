#!/usr/bin/env python3
"""Write compact node predictions for a completed Profile ensemble diagnostic plot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.checkpoint import (
    load_checkpoint_payload, load_trained_state_with_offbook_migration,
    load_trained_state_with_wld_migration, load_transferred_offbook_profile_model,
    load_transferred_profile_model, sha256_file,
)
from src.data_contract import validate_model_ready_npz
from src.ensemble import resolve_member_checkpoint
from src.offbook import OFFBOOK_SCHEMA
from src.oq_profile_features import OQ_PROFILE_FEATURE_NAMES, profile_ablation_hash


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--ensemble-manifest", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("train", "validation", "test"), default="test")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite: {args.output}")
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")

    ensemble = json.loads(args.ensemble_manifest.read_text(encoding="utf-8"))
    members = ensemble.get("members", [])
    if ensemble.get("status") != "completed" or not members:
        raise ValueError("ensemble manifest is not completed")
    first = torch.load(
        resolve_member_checkpoint(args.ensemble_manifest, members[0]["bestCheckpoint"]),
        map_location="cpu", weights_only=False,
    )
    trained_manifest = first["manifest"]
    use_offbook = first.get("schema") == "tcn-loss-profile-offbook-level18-wld-checkpoint-v1"
    expected_member_schema = (
        "tcn-loss-profile-offbook-level18-wld-checkpoint-v1"
        if use_offbook else "tcn-loss-profile-wld-checkpoint-v2"
    )
    ablation = str(trained_manifest["oqProfileAblation"])
    if trained_manifest.get("oqProfileAblationSha256") != profile_ablation_hash(ablation):
        raise ValueError("Profile ablation hash mismatch")
    base = load_checkpoint_payload(args.base_checkpoint)
    validation = validate_model_ready_npz(
        args.data,
        expected_input_features=base["input_features"],
        expected_board_channels=base["board_encoding"]["cnn_channels"],
        require_oq_profile=True,
        expected_oq_profile_feature_names=OQ_PROFILE_FEATURE_NAMES,
        expected_oq_profile_preprocessing_sha256=trained_manifest["oqProfilePreprocessingSha256"],
        expected_oq_profile_policy=trained_manifest["oqProfilePolicy"],
        require_offbook=use_offbook,
        expected_offbook_schema=OFFBOOK_SCHEMA,
        expected_offbook_source_checkpoint_sha256=sha256_file(args.base_checkpoint) if use_offbook else None,
        expected_offbook_materialization_sha256=trained_manifest.get("offbookMaterializationSha256") if use_offbook else None,
    )
    device = torch.device(args.device)
    with np.load(args.data, allow_pickle=False) as data:
        selected_games = np.flatnonzero(data["split"].astype(str) == args.split)
        if not len(selected_games):
            raise ValueError(f"data contain no {args.split} games")
        names = [
            "X", "board_tokens", "board_move_tokens", "current_hint_tokens", "current_hint_values",
            "prev_own_hint_values", "actual_thinking_time_ms", "oq_profile_features",
            "oq_profile_missing", "mask", "game_id", "global_placement_ply", "disc_loss",
            "label_zero", "label_ge4", "label_ge10",
            "wld_label_available", "wld_loss",
        ]
        if use_offbook:
            names.extend(["offbook_ply", "offbook_present", "offbook_feature"])
        selected = {name: data[name][selected_games].copy() for name in names}
        probability_sum = np.zeros((*selected["X"].shape[:2], 4), dtype=np.float64)
        wld_probability_sum = np.zeros((*selected["X"].shape[:2], 3), dtype=np.float64)
        for member in members:
            checkpoint_path = resolve_member_checkpoint(args.ensemble_manifest, member["bestCheckpoint"])
            saved = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
            if saved.get("schema") != expected_member_schema:
                raise ValueError(f"not an expected Profile checkpoint: {checkpoint_path}")
            identity = saved["manifest"]
            for key in ("oqProfileAblation", "oqProfileAblationSha256", "oqProfilePreprocessingSha256", "oqProfilePolicy"):
                if identity.get(key) != trained_manifest.get(key):
                    raise ValueError(f"ensemble member Profile identity mismatch: {key}")
            if identity.get("baseCheckpointSha256") != sha256_file(args.base_checkpoint):
                raise ValueError("ensemble member base checkpoint mismatch")
            model, _ = (
                load_transferred_offbook_profile_model(args.base_checkpoint, ablation)
                if use_offbook else load_transferred_profile_model(args.base_checkpoint, ablation)
            )
            migration = (
                load_trained_state_with_offbook_migration(model, saved["modelStateDict"])
                if use_offbook else load_trained_state_with_wld_migration(model, saved["modelStateDict"])
            )
            if not use_offbook and migration["migratedLegacyCheckpoint"]:
                raise ValueError("ensemble member has an untrained legacy WLD head")
            model.to(device).eval()
            with torch.no_grad():
                for start in range(0, len(selected_games), args.batch_size):
                    indexes = slice(start, min(start + args.batch_size, len(selected_games)))
                    base_args = (
                        torch.from_numpy(selected["X"][indexes]).float().to(device),
                        torch.from_numpy(selected["board_tokens"][indexes]).to(device),
                        torch.from_numpy(selected["board_move_tokens"][indexes]).to(device),
                        torch.from_numpy(selected["current_hint_tokens"][indexes]).to(device),
                        torch.from_numpy(selected["current_hint_values"][indexes]).float().to(device),
                        torch.from_numpy(selected["prev_own_hint_values"][indexes]).float().to(device),
                        torch.from_numpy(selected["actual_thinking_time_ms"][indexes]).float().to(device),
                    )
                    profile_args = (
                        torch.from_numpy(selected["oq_profile_features"][indexes]).float().to(device),
                        torch.from_numpy(selected["oq_profile_missing"][indexes]).to(device),
                    )
                    output = (
                        model(
                            *base_args,
                            torch.from_numpy(selected["offbook_feature"][indexes]).float().to(device),
                            *profile_args,
                        )
                        if use_offbook else model(*base_args, *profile_args)
                    )
                    probability_sum[indexes] += output.severity_class_probabilities.cpu().numpy()
                    wld_probability_sum[indexes] += output.wld_probabilities.cpu().numpy()
            del model
            if device.type == "cuda":
                torch.cuda.empty_cache()
        probabilities = probability_sum / len(members)
        wld_probabilities = wld_probability_sum / len(members)
        selected_mask = selected["mask"].astype(bool)
        games, steps = selected_mask.shape
        game_grid = np.broadcast_to(selected["game_id"][:, None], (games, steps))
        split_grid = np.full((games, steps), args.split)
        applicable = selected["global_placement_ply"] >= 39
        frame = pd.DataFrame({
            "game_id": game_grid[selected_mask], "split": split_grid[selected_mask],
            "global_placement_ply": selected["global_placement_ply"][selected_mask],
            "actual_disc_loss": selected["disc_loss"][selected_mask],
            "actual_loss_zero": selected["label_zero"][selected_mask],
            "actual_loss_ge4": selected["label_ge4"][selected_mask],
            "actual_loss_ge10": selected["label_ge10"][selected_mask],
            "probability_loss_zero": probabilities[..., 0][selected_mask],
            "probability_loss_ge4": (probabilities[..., 2] + probabilities[..., 3])[selected_mask],
            "probability_loss_ge10": probabilities[..., 3][selected_mask],
            "wld_applicable": applicable[selected_mask],
            "wld_label_available": selected["wld_label_available"][selected_mask],
            "actual_wld_loss": np.where(selected["wld_label_available"], selected["wld_loss"], np.nan)[selected_mask],
            "probability_class_no_wld_loss": np.where(applicable, wld_probabilities[..., 0], np.nan)[selected_mask],
            "probability_class_half_wld_loss": np.where(applicable, wld_probabilities[..., 1], np.nan)[selected_mask],
            "probability_class_full_wld_loss": np.where(applicable, wld_probabilities[..., 2], np.nan)[selected_mask],
            "probability_wld_any": np.where(applicable, wld_probabilities[..., 1] + wld_probabilities[..., 2], np.nan)[selected_mask],
            "expected_wld_loss": np.where(applicable, 0.5 * wld_probabilities[..., 1] + wld_probabilities[..., 2], np.nan)[selected_mask],
        })
        if use_offbook:
            frame["offbook_ply"] = selected["offbook_ply"][selected_mask]
            frame["offbook_present"] = selected["offbook_present"][selected_mask]
            frame["offbook_feature"] = selected["offbook_feature"][selected_mask]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False, encoding="utf-8")
    print(json.dumps({
        "ok": True, "members": len(members), "split": args.split,
        "games": int(frame["game_id"].nunique()), "rows": len(frame),
        "output": str(args.output.resolve()), "dataValidation": validation["ok"],
        "modelVariant": "oq-profile-offbook-level18" if use_offbook else "oq-profile",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
