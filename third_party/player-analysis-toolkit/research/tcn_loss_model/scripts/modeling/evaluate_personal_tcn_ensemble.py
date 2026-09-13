#!/usr/bin/env python3
"""Predict reported games with personal TCN members and whole-game bootstrap."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.checkpoint import (
    load_checkpoint_payload,
    load_trained_state_with_offbook_migration,
    load_transferred_model,
    load_transferred_offbook_profile_model,
    load_transferred_profile_model,
    sha256_file,
)
from src.oq_profile_features import OQ_PROFILE_FEATURE_NAMES, profile_ablation_hash
from src.data_contract import validate_model_ready_npz
from src.offbook import OFFBOOK_SCHEMA

METRICS = {
    "ge4": ("actual_loss_ge4", "probability_loss_ge4"),
    "zero": ("actual_loss_zero", "probability_loss_zero"),
    "ge10": ("actual_loss_ge10", "probability_loss_ge10"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--personal-ensemble-manifest", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--reported-game", action="append", required=True)
    parser.add_argument(
        "--offbook-ply", action="append", default=[], help="optional GAME_ID=PLY; games without one use all target moves"
    )
    parser.add_argument("--node-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument(
        "--control-calibration-output", type=Path,
        help="optional reusable pre/post-adaptation calibration JSON for all control-split nodes",
    )
    parser.add_argument("--bootstrap-replicates", type=int, default=10000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260803)
    parser.add_argument("--device", default="cuda:0")
    return parser.parse_args()


def parse_mapping(items: list[str]) -> dict[str, int]:
    result = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator:
            raise ValueError(f"expected GAME_ID=PLY, got {item!r}")
        result[key] = int(value)
    return result


def validate_reported_selection(
    reported: list[str], offbook: dict[str, int], split_reported: set[str]
) -> None:
    requested = set(reported)
    unknown_anchors = set(offbook) - requested
    if unknown_anchors:
        raise ValueError(f"offbook ply supplied for a non-reported game: {sorted(unknown_anchors)}")
    if requested != split_reported:
        raise ValueError(
            f"reported arguments do not match NPZ test split: "
            f"arguments={sorted(requested)}, split={sorted(split_reported)}"
        )


def bootstrap_draws(replicates: int, member_count: int, game_count: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    member_draws = rng.integers(0, member_count, size=(replicates, member_count))
    game_draws = rng.integers(0, game_count, size=(replicates, game_count))
    return member_draws, game_draws


def quantiles(values: np.ndarray) -> dict[str, float]:
    lower, upper = np.quantile(values, [0.025, 0.975])
    return {"lower": float(lower), "upper": float(upper)}


def summarize_group(frame: pd.DataFrame, member_probabilities: dict[str, np.ndarray], game_ids: list[str],
                    member_draws: np.ndarray, game_draws: np.ndarray) -> dict[str, Any]:
    use = frame["game_id"].isin(game_ids).to_numpy()
    group = frame.loc[use].reset_index(drop=True)
    games = np.asarray(game_ids)
    group_member_probabilities = {name: values[:, use] for name, values in member_probabilities.items()}
    point_estimates: dict[str, Any] = {}
    group_game_values = group["game_id"].to_numpy()
    for metric, (actual_column, _probability_column) in METRICS.items():
        actual = group[actual_column].to_numpy(dtype=float)
        ensemble_probability = group_member_probabilities[metric].mean(axis=0)
        residual = actual - ensemble_probability
        per_game = []
        for game_id in game_ids:
            game_mask = group_game_values == game_id
            per_game.append({
                "gameId": game_id, "nodes": int(game_mask.sum()),
                "actualRate": float(actual[game_mask].mean()),
                "meanEnsembleProbability": float(ensemble_probability[game_mask].mean()),
                "actualMinusExpected": float(residual[game_mask].mean()),
            })
        point_estimates[metric] = {
            "gameEqualActualRate": float(np.mean([item["actualRate"] for item in per_game])),
            "gameEqualMeanEnsembleProbability": float(np.mean([item["meanEnsembleProbability"] for item in per_game])),
            "gameEqualActualMinusExpected": float(np.mean([item["actualMinusExpected"] for item in per_game])),
            "perGame": per_game,
        }

    replicates = len(member_draws)
    bootstrap_values = {metric: np.zeros(replicates, dtype=np.float64) for metric in METRICS}
    for replicate in range(replicates):
        selected_members = member_draws[replicate]
        selected_game_ids = games[game_draws[replicate]]
        for metric, (actual_column, _probability_column) in METRICS.items():
            probabilities = group_member_probabilities[metric][selected_members].mean(axis=0)
            residual = group[actual_column].to_numpy(dtype=float) - probabilities
            bootstrap_values[metric][replicate] = np.mean([
                residual[group_game_values == game_id].mean() for game_id in selected_game_ids
            ])
    intervals = {metric: quantiles(values) for metric, values in bootstrap_values.items()}
    return {
        "gameIds": game_ids, "nodes": int(len(group)), "aggregationUnit": "whole-game",
        "pointEstimates": point_estimates, "bootstrap95PercentIntervals": intervals,
    }


def summarize_predicted_group(
    frame: pd.DataFrame,
    member_probabilities: dict[str, np.ndarray],
    member_expected_wld_loss: np.ndarray,
    game_ids: list[str],
    member_draws: np.ndarray,
    game_draws: np.ndarray,
) -> dict[str, Any]:
    """Game-equal ensemble predictions and member+whole-game bootstrap intervals."""
    use = frame["game_id"].isin(game_ids).to_numpy()
    group = frame.loc[use].reset_index(drop=True)
    group_games = group["game_id"].to_numpy()
    games = np.asarray(game_ids)
    probabilities = {name: values[:, use] for name, values in member_probabilities.items()}
    wld = member_expected_wld_loss[:, use]
    wld_applicable = group["wld_applicable"].to_numpy(dtype=bool)

    values_by_metric = {**probabilities, "expected_wld_loss": wld}
    applicability = {
        "zero": np.ones(len(group), dtype=bool),
        "ge4": np.ones(len(group), dtype=bool),
        "ge10": np.ones(len(group), dtype=bool),
        "expected_wld_loss": wld_applicable,
    }
    point: dict[str, float] = {}
    per_game: dict[str, list[dict[str, Any]]] = {}
    bootstrap: dict[str, np.ndarray] = {
        name: np.zeros(len(member_draws), dtype=np.float64) for name in values_by_metric
    }
    for name, member_values in values_by_metric.items():
        eligible = applicability[name]
        ensemble = member_values.mean(axis=0)
        rows = []
        for game_id in game_ids:
            game_mask = (group_games == game_id) & eligible
            if not game_mask.any():
                if name != "expected_wld_loss":
                    raise ValueError(f"game {game_id} has no applicable nodes for {name}")
                rows.append({
                    "gameId": game_id,
                    "nodes": 0,
                    "meanEnsemblePrediction": 0.0,
                    "noApplicableNodes": True,
                })
                continue
            rows.append({
                "gameId": game_id,
                "nodes": int(game_mask.sum()),
                "meanEnsemblePrediction": float(ensemble[game_mask].mean()),
                "noApplicableNodes": False,
            })
        per_game[name] = rows
        point[name] = float(np.mean([row["meanEnsemblePrediction"] for row in rows]))
        for replicate, (selected_members, selected_game_indexes) in enumerate(zip(member_draws, game_draws, strict=True)):
            replicate_prediction = member_values[selected_members].mean(axis=0)
            game_predictions = []
            for index in selected_game_indexes:
                game_mask = (group_games == games[index]) & eligible
                if game_mask.any():
                    game_predictions.append(float(replicate_prediction[game_mask].mean()))
                elif name == "expected_wld_loss":
                    game_predictions.append(0.0)
                else:
                    raise ValueError(f"game {games[index]} has no applicable nodes for {name}")
            bootstrap[name][replicate] = np.mean(game_predictions)
    return {
        "aggregationUnit": "whole-game",
        "pointEstimates": point,
        "bootstrap95PercentIntervals": {name: quantiles(values) for name, values in bootstrap.items()},
        "perGame": per_game,
        "nodes": int(len(group)),
        "wldApplicableNodes": int(wld_applicable.sum()),
    }


def summarize_control_adaptation(
    severity_targets: np.ndarray,
    wld_targets: np.ndarray,
    wld_losses: np.ndarray,
    wld_applicable: np.ndarray,
    base_severity: np.ndarray,
    adapted_severity: np.ndarray,
    base_wld: np.ndarray,
    adapted_wld: np.ndarray,
    game_count: int,
) -> dict[str, Any]:
    actual = {
        "zero": severity_targets == 0,
        "ge4": severity_targets >= 2,
        "ge10": severity_targets == 3,
    }

    def severity_probability(values: np.ndarray, metric: str) -> np.ndarray:
        return {
            "zero": values[:, 0],
            "ge4": values[:, 2] + values[:, 3],
            "ge10": values[:, 3],
        }[metric]

    hard = {
        "fourClassExact": {
            "before": float((base_severity.argmax(axis=1) == severity_targets).mean()),
            "after": float((adapted_severity.argmax(axis=1) == severity_targets).mean()),
        },
        "wldThreeClassExact": {
            "before": float((base_wld[wld_applicable].argmax(axis=1) == wld_targets[wld_applicable]).mean()),
            "after": float((adapted_wld[wld_applicable].argmax(axis=1) == wld_targets[wld_applicable]).mean()),
        },
    }
    probability = {}
    for metric in ("zero", "ge4", "ge10"):
        observed = float(actual[metric].mean())
        before = float(severity_probability(base_severity, metric).mean())
        after = float(severity_probability(adapted_severity, metric).mean())
        hard[metric] = {
            "before": float(((severity_probability(base_severity, metric) >= 0.5) == actual[metric]).mean()),
            "after": float(((severity_probability(adapted_severity, metric) >= 0.5) == actual[metric]).mean()),
        }
        probability[metric] = {
            "actual": observed, "before": before, "beforeError": before - observed,
            "after": after, "afterError": after - observed,
        }
    base_expected_wld = 0.5 * base_wld[:, 1] + base_wld[:, 2]
    adapted_expected_wld = 0.5 * adapted_wld[:, 1] + adapted_wld[:, 2]
    observed_wld = float(wld_losses[wld_applicable].mean())
    before_wld = float(base_expected_wld[wld_applicable].mean())
    after_wld = float(adapted_expected_wld[wld_applicable].mean())
    probability["expectedWldLoss"] = {
        "actual": observed_wld, "before": before_wld, "beforeError": before_wld - observed_wld,
        "after": after_wld, "afterError": after_wld - observed_wld,
    }
    for values in hard.values():
        values["improvement"] = values["after"] - values["before"]
    return {
        "schema": "personal-tcn-control-adaptation-calibration-v2", "status": "completed",
        "evaluationRole": "in-sample-control-adapter-fit",
        "controlGames": game_count, "severityNodes": int(len(severity_targets)),
        "wldApplicableNodes": int(wld_applicable.sum()),
        "hardDecisionMatchRates": hard, "probabilityRateCalibration": probability,
        "aggregation": "node-weighted display statistics; adapter optimization itself is game-equal",
        "wldApplicabilityPolicy": "wld_label_available and global_placement_ply >= 39",
    }
@torch.no_grad()
def main() -> int:
    started_clock = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    args = parse_args()
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    base = load_checkpoint_payload(args.base_checkpoint)
    manifest = json.loads(args.personal_ensemble_manifest.read_text(encoding="utf-8"))
    members = manifest.get("members", [])
    if len(members) != 12:
        raise ValueError(f"expected exactly 12 personal members, found {len(members)}")
    first_base_member = torch.load(Path(members[0]["baseEnsembleCheckpoint"]), map_location="cpu", weights_only=False)
    use_offbook = first_base_member.get("schema") == "tcn-loss-profile-offbook-level18-wld-checkpoint-v1"
    use_oq_profile = use_offbook or first_base_member.get("schema") in {
        "tcn-loss-profile-checkpoint-v1", "tcn-loss-profile-wld-checkpoint-v2"
    }
    profile_manifest = first_base_member.get("manifest", {})
    profile_ablation = str(profile_manifest.get("oqProfileAblation") or "")
    validation = validate_model_ready_npz(
        args.data, expected_input_features=base["input_features"],
        expected_board_channels=base["board_encoding"]["cnn_channels"],
        expected_preprocessing_sha256=hashlib.sha256(json.dumps(base["preprocessing"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
        require_oq_profile=use_oq_profile,
        expected_oq_profile_feature_names=OQ_PROFILE_FEATURE_NAMES if use_oq_profile else None,
        expected_oq_profile_preprocessing_sha256=profile_manifest.get("oqProfilePreprocessingSha256") if use_oq_profile else None,
        expected_oq_profile_policy=profile_manifest.get("oqProfilePolicy") if use_oq_profile else None,
        require_offbook=use_offbook,
        expected_offbook_schema=OFFBOOK_SCHEMA,
        expected_offbook_source_data_sha256=(
            profile_manifest.get("offbookSourceDataSha256") if use_offbook else None
        ),
        expected_offbook_source_checkpoint_sha256=sha256_file(args.base_checkpoint) if use_offbook else None,
        expected_offbook_records_sha256=(
            profile_manifest.get("offbookRecordsSha256") if use_offbook else None
        ),
        expected_offbook_materialization_sha256=(
            profile_manifest.get("offbookMaterializationSha256") if use_offbook else None
        ),
    )
    if use_oq_profile and profile_manifest.get("oqProfileAblationSha256") != profile_ablation_hash(profile_ablation):
        raise ValueError("OQ profile ablation hash mismatch")
    reported = args.reported_game
    level22_offbook = parse_mapping(args.offbook_ply)
    device = torch.device(args.device)
    with np.load(args.data, allow_pickle=False) as data:
        games, steps = data["X"].shape[:2]
        valid = data["mask"].astype(bool)
        game_grid = np.broadcast_to(data["game_id"][:, None], (games, steps)).astype(str)
        selected = valid & np.isin(game_grid, reported)
        if use_offbook:
            if level22_offbook:
                raise ValueError("Level22 --offbook-ply cannot be used to select nodes for the Level18 TCN model")
            materialized_ply = data["offbook_ply"].astype(np.int16)
            materialized_present = data["offbook_present"].astype(bool)
            anchors: dict[str, int] = {}
            no_anchor: list[str] = []
            for game_id in reported:
                game_mask = (game_grid == game_id) & valid & materialized_present
                values = np.unique(materialized_ply[game_mask])
                if len(values) > 1:
                    raise ValueError(f"Level18 materialized anchor is not unique for reported game {game_id}: {values.tolist()}")
                if len(values) == 0:
                    no_anchor.append(game_id)
                else:
                    anchors[game_id] = int(values[0])
            ply = data["global_placement_ply"][selected]
            selected_games = game_grid[selected]
            after_offbook = np.asarray([
                game_id not in anchors or node_ply >= anchors[game_id]
                for node_ply, game_id in zip(ply, selected_games, strict=True)
            ])
        else:
            anchors = level22_offbook
            no_anchor = sorted(set(reported) - set(anchors))
            ply = data["global_placement_ply"][selected]
            selected_games = game_grid[selected]
            after_offbook = np.asarray([
                game_id not in level22_offbook or node_ply >= level22_offbook[game_id]
                for node_ply, game_id in zip(ply, selected_games, strict=True)
            ])
        flat_indexes = np.flatnonzero(selected.reshape(-1))[after_offbook]
        splits = data["split"].astype(str)
        split_reported = set(data["game_id"].astype(str)[splits == "test"])
        validate_reported_selection(reported, level22_offbook if not use_offbook else {}, split_reported)
        control_grid = np.broadcast_to((splits == "train")[:, None], (games, steps))
        control_selected = valid & control_grid
        control_flat_indexes = np.flatnonzero(control_selected.reshape(-1))
        control_game_count = int((splits == "train").sum())
        control_severity_targets = data["severity_class"].reshape(-1)[control_flat_indexes].astype(int)
        control_wld_targets = data["wld_class"].reshape(-1)[control_flat_indexes].astype(int)
        control_wld_losses = data["wld_loss"].reshape(-1)[control_flat_indexes].astype(float)
        control_wld_applicable = (
            data["wld_label_available"].reshape(-1)[control_flat_indexes].astype(bool)
            & (data["global_placement_ply"].reshape(-1)[control_flat_indexes] >= 39)
        )
        base_columns = {
            "game_id": game_grid.reshape(-1)[flat_indexes],
            "player_id": data["player_id"].reshape(-1)[flat_indexes],
            "move_index": data["move_index"].reshape(-1)[flat_indexes],
            "global_placement_ply": data["global_placement_ply"].reshape(-1)[flat_indexes],
            "side_to_move": data["side_to_move"].reshape(-1)[flat_indexes],
            "actual_disc_loss": data["disc_loss"].reshape(-1)[flat_indexes],
            "actual_loss_zero": data["label_zero"].reshape(-1)[flat_indexes],
            "actual_loss_ge4": data["label_ge4"].reshape(-1)[flat_indexes],
            "actual_loss_ge10": data["label_ge10"].reshape(-1)[flat_indexes],
            "raw_thinking_time_ms": data["raw_thinking_time_ms"].reshape(-1)[flat_indexes],
            "effective_thinking_time_ms": data["effective_thinking_time_ms"].reshape(-1)[flat_indexes],
            "wld_applicable": (
                data["wld_label_available"].reshape(-1)[flat_indexes].astype(bool)
                & (data["global_placement_ply"].reshape(-1)[flat_indexes] >= 39)
            ),
        }
        if use_offbook:
            base_columns["offbook_ply"] = data["offbook_ply"].reshape(-1)[flat_indexes].astype(int)
            base_columns["offbook_present"] = data["offbook_present"].reshape(-1)[flat_indexes].astype(bool)
            base_columns["offbook_feature"] = data["offbook_feature"].reshape(-1)[flat_indexes].astype(float)
        source_by_game = dict(zip(data["game_id"].astype(str), data["source_time_limit_ms"].astype(int), strict=True))
        scale_by_game = dict(zip(data["game_id"].astype(str), data["time_scale_factor"].astype(float), strict=True))
        reported_source_limits = {game_id: int(source_by_game[game_id]) for game_id in reported}
        base_columns["source_time_limit_ms"] = [source_by_game[game_id] for game_id in base_columns["game_id"]]
        base_columns["effective_time_limit_ms"] = 300000
        base_columns["time_scale_factor"] = [scale_by_game[game_id] for game_id in base_columns["game_id"]]
        base_columns["time_control_policy"] = str(data["time_control_policy"].item())
        model_inputs = {
            name: torch.from_numpy(data[name]).to(device)
            for name in ("X", "board_tokens", "board_move_tokens", "current_hint_tokens", "current_hint_values", "prev_own_hint_values", "actual_thinking_time_ms")
        }
        if use_oq_profile:
            model_inputs["oq_profile_features"] = torch.from_numpy(data["oq_profile_features"]).float().to(device)
            model_inputs["oq_profile_missing"] = torch.from_numpy(data["oq_profile_missing"]).to(device)
        if use_offbook:
            model_inputs["offbook_feature"] = torch.from_numpy(data["offbook_feature"]).float().to(device)
    frame = pd.DataFrame(base_columns)
    member_classes = []
    member_wld = []
    control_base_classes, control_adapted_classes = [], []
    control_base_wld, control_adapted_wld = [], []
    for member in members:
        checkpoint_path = Path(member["personalCheckpoint"])
        saved = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        if saved.get("schema") not in {"personal-tcn-adapter-v1", "personal-tcn-adapter-v2"}:
            raise ValueError(f"personal adapter checkpoint schema mismatch: {checkpoint_path}")
        base_member = torch.load(Path(member["baseEnsembleCheckpoint"]), map_location="cpu", weights_only=False)
        expected_schema = (
            "tcn-loss-profile-offbook-level18-wld-checkpoint-v1"
            if use_offbook else ("tcn-loss-profile-wld-checkpoint-v2" if use_oq_profile else "tcn-loss-wld-checkpoint-v2")
        )
        if base_member.get("schema") != expected_schema:
            raise ValueError("personal adapter base ensemble checkpoint schema mismatch")
        model, _ = (
            load_transferred_offbook_profile_model(args.base_checkpoint, profile_ablation)
            if use_offbook else (
                load_transferred_profile_model(args.base_checkpoint, profile_ablation)
                if use_oq_profile else load_transferred_model(args.base_checkpoint)
            )
        )
        if use_offbook:
            load_trained_state_with_offbook_migration(model, base_member["modelStateDict"])
        else:
            model.load_state_dict(base_member["modelStateDict"], strict=True)
        model.to(device).eval()
        model_args = (
            model_inputs["X"].float(), model_inputs["board_tokens"], model_inputs["board_move_tokens"],
            model_inputs["current_hint_tokens"], model_inputs["current_hint_values"].float(),
            model_inputs["prev_own_hint_values"].float(), model_inputs["actual_thinking_time_ms"].float(),
        )
        if use_offbook:
            output = model(
                *model_args, model_inputs["offbook_feature"],
                model_inputs["oq_profile_features"], model_inputs["oq_profile_missing"],
            )
        elif use_oq_profile:
            output = model(*model_args, model_inputs["oq_profile_features"], model_inputs["oq_profile_missing"])
        else:
            output = model(*model_args)
        base_logits = output.severity_logits.detach().cpu().double().reshape(-1, 4)[flat_indexes]
        hidden = output.severity_hidden.detach().cpu().double().reshape(-1, 64)[flat_indexes]
        probabilities = torch.softmax(base_logits + hidden @ saved["deltaW"].double() + saved["deltaB"].double(), dim=-1).numpy()
        if not np.all(np.isfinite(probabilities)) or not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-6):
            raise ValueError("non-finite or non-unit personal class probabilities")
        member_classes.append(probabilities)
        wld_logits = output.wld_logits.detach().cpu().double().reshape(-1, 3)[flat_indexes]
        if saved.get("schema") == "personal-tcn-adapter-v2":
            wld_probabilities = torch.softmax(
                wld_logits + hidden @ saved["wldDeltaW"].double() + saved["wldDeltaB"].double(), dim=-1
            ).numpy()
        else:
            wld_probabilities = torch.softmax(wld_logits, dim=-1).numpy()
        expected_wld = 0.5 * wld_probabilities[:, 1] + wld_probabilities[:, 2]
        if not np.all(np.isfinite(expected_wld)) or np.any((expected_wld < 0) | (expected_wld > 1)):
            raise ValueError("non-finite or out-of-range expected WLD loss")
        member_wld.append(expected_wld)
        if args.control_calibration_output:
            all_hidden = output.severity_hidden.detach().cpu().double().reshape(-1, 64)[control_flat_indexes]
            all_severity_logits = output.severity_logits.detach().cpu().double().reshape(-1, 4)[control_flat_indexes]
            all_wld_logits = output.wld_logits.detach().cpu().double().reshape(-1, 3)[control_flat_indexes]
            control_base_classes.append(torch.softmax(all_severity_logits, dim=-1).numpy())
            control_adapted_classes.append(torch.softmax(
                all_severity_logits + all_hidden @ saved["deltaW"].double() + saved["deltaB"].double(), dim=-1
            ).numpy())
            control_base_wld.append(torch.softmax(all_wld_logits, dim=-1).numpy())
            if saved.get("schema") == "personal-tcn-adapter-v2":
                adapted_wld_logits = all_wld_logits + all_hidden @ saved["wldDeltaW"].double() + saved["wldDeltaB"].double()
            else:
                adapted_wld_logits = all_wld_logits
            control_adapted_wld.append(torch.softmax(adapted_wld_logits, dim=-1).numpy())
        member_index = int(member["member"])
        for class_index, class_name in enumerate(("zero", "1_3", "4_9", "ge10")):
            frame[f"member_{member_index:02d}_probability_class_{class_name}"] = probabilities[:, class_index]
        frame[f"member_{member_index:02d}_probability_loss_ge4"] = probabilities[:, 2] + probabilities[:, 3]
        frame[f"member_{member_index:02d}_expected_wld_loss"] = np.where(
            frame["wld_applicable"], expected_wld, np.nan
        )
        del model, output
        if device.type == "cuda":
            torch.cuda.empty_cache()
    classes = np.stack(member_classes)
    wld_values = np.stack(member_wld)
    ensemble_classes = classes.mean(axis=0)
    for class_index, class_name in enumerate(("zero", "1_3", "4_9", "ge10")):
        frame[f"ensemble_probability_class_{class_name}"] = ensemble_classes[:, class_index]
    frame["probability_loss_zero"] = ensemble_classes[:, 0]
    frame["probability_loss_ge4"] = ensemble_classes[:, 2] + ensemble_classes[:, 3]
    frame["probability_loss_ge10"] = ensemble_classes[:, 3]
    frame["expected_wld_loss"] = np.where(frame["wld_applicable"], wld_values.mean(axis=0), np.nan)
    for metric, (actual_column, probability_column) in METRICS.items():
        frame[f"actual_minus_expected_{metric}"] = frame[actual_column] - frame[probability_column]
    args.node_output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.node_output, index=False, encoding="utf-8")

    member_probabilities = {
        "zero": classes[:, :, 0], "ge4": classes[:, :, 2] + classes[:, :, 3], "ge10": classes[:, :, 3],
    }
    group_results = {}
    for game_id in reported:
        member_draws, game_draws = bootstrap_draws(args.bootstrap_replicates, len(members), 1, args.bootstrap_seed)
        group_results[game_id] = summarize_group(frame, member_probabilities, [game_id], member_draws, game_draws)
    member_draws, game_draws = bootstrap_draws(args.bootstrap_replicates, len(members), len(reported), args.bootstrap_seed)
    group_results["combined"] = summarize_group(frame, member_probabilities, reported, member_draws, game_draws)
    requested_metrics = summarize_predicted_group(
        frame, member_probabilities, wld_values, reported, member_draws, game_draws
    )
    compositions, counts = np.unique(np.sort(game_draws, axis=1), axis=0, return_counts=True)
    summary = {
        "schema": "personal-tcn-reported-bootstrap-v1", "status": "completed",
        "interpretation": "actual indicator minus twelve-personal-model ensemble probability",
        "notCheatingProbability": True, "reportedGames": reported,
        "fullGameFallbackNoOffbookGameIds": no_anchor,
        "level22SentinelOffbookGlobalPlacementPly": level22_offbook,
        "level18TcnFeature": {
            "schema": OFFBOOK_SCHEMA if use_offbook else "not-used-by-legacy-model",
            "materializedFromModelReadyArrays": use_offbook,
            "labelSource": profile_manifest.get("offbookLabelSource", "") if use_offbook else "",
            "algorithmVersion": profile_manifest.get("offbookAlgorithmVersion", "") if use_offbook else "",
            "normalization": profile_manifest.get("offbookNormalization", "") if use_offbook else "",
            "sourceEngineLevel": profile_manifest.get("offbookSourceEngineLevel", 0) if use_offbook else 0,
            "engineContract": profile_manifest.get("offbookEngineContract", "") if use_offbook else "",
            "sourceDataSha256": profile_manifest.get("offbookSourceDataSha256", "") if use_offbook else "",
            "sourceCheckpointSha256": profile_manifest.get("offbookSourceCheckpointSha256", "") if use_offbook else "",
            "recordsSha256": profile_manifest.get("offbookRecordsSha256", "") if use_offbook else "",
            "materializationSha256": profile_manifest.get("offbookMaterializationSha256", "") if use_offbook else "",
            "retrospectiveDisclosure": profile_manifest.get("offbookRetrospectiveDisclosure", "") if use_offbook else "",
            "anchorGlobalPlacementPlyInclusive": anchors if use_offbook else {},
            "fullGameFallbackNoOffbookGameIds": no_anchor if use_offbook else [],
            "postOffbookSelectionUsesSameMaterializedArray": use_offbook,
        },
        "memberCount": len(members), "bootstrapReplicates": args.bootstrap_replicates,
        "bootstrapSeed": args.bootstrap_seed,
        "modelVariant": "oq-profile-offbook-level18" if use_offbook else ("oq-profile" if use_oq_profile else "baseline"),
        "oqProfileAblation": profile_ablation if use_oq_profile else "",
        "bootstrapProtocol": {
            "memberDraw": "12 members with replacement, averaged within replicate",
            "gameDraw": "whole reported games with replacement, same count as observed games",
            "sharedDraws": "one member draw and one whole-game draw shared by ge4/zero/ge10 within each replicate",
            "phaseBinning": "none",
            "minimumStatisticalUnit": "whole-game",
            "controlGamesResampled": False,
            "conditionedOnCompleteControlSet": True,
            "firstFiveMemberDraws": member_draws[:5].tolist(),
            "firstFiveGameDraws": game_draws[:5].tolist(),
            "combinedGameDrawCompositionCounts": [
                {"gameIndexes": row.tolist(), "count": int(count)} for row, count in zip(compositions, counts, strict=True)
            ],
        },
        "groups": group_results,
        "requestedReportedGroupMetrics": requested_metrics,
        "nodePredictions": str(args.node_output.resolve()), "nodePredictionsSha256": sha256_file(args.node_output),
        "dataValidation": validation,
        "timing": {
            "startedAtUtc": started_at,
            "completedAtUtc": datetime.now(timezone.utc).isoformat(),
            "elapsedSeconds": time.perf_counter() - started_clock,
        },
        "reportedSourceTimeLimitMs": reported_source_limits,
        "limitations": [
            f"The reported group contains only {len(reported)} games.",
            *(
                ["At least one reported game was clock-normalized; those results are exploratory rather than native support for that time control."]
                if any(source_by_game[game_id] != 300000 for game_id in reported)
                else ["All reported games use the model's native five-minute time control; no clock scaling was applied."]
            ),
            "Intervals quantify finite ensemble and whole-game sampling under the complete fixed personal control set; they are not cheating probabilities.",
        ],
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.control_calibration_output:
        control_report = summarize_control_adaptation(
            control_severity_targets, control_wld_targets, control_wld_losses, control_wld_applicable,
            np.mean(control_base_classes, axis=0), np.mean(control_adapted_classes, axis=0),
            np.mean(control_base_wld, axis=0), np.mean(control_adapted_wld, axis=0),
            control_game_count,
        )
        control_report["personalEnsembleManifest"] = str(args.personal_ensemble_manifest.resolve())
        control_report["personalEnsembleManifestSha256"] = sha256_file(args.personal_ensemble_manifest)
        args.control_calibration_output.parent.mkdir(parents=True, exist_ok=True)
        args.control_calibration_output.write_text(
            json.dumps(control_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
