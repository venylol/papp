#!/usr/bin/env python3
"""Evaluate the frozen base thinking-time head against manual off-book anchors."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import average_precision_score, roc_auc_score


MODEL_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(MODEL_ROOT))

from src.checkpoint import load_checkpoint_payload, load_transferred_model, sha256_file


RATIO_THRESHOLDS = (1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
LOG_RESIDUAL_THRESHOLDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-manifest", type=Path, required=True)
    parser.add_argument("--base-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=32)
    return parser.parse_args()


def json_hash(value: dict[str, Any]) -> str:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def resolve_repository_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def load_manifest(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "manual-offbook-time-baseline-cohort-v1":
        raise ValueError("unexpected cohort manifest schema")
    cohorts = manifest.get("cohorts")
    if not isinstance(cohorts, list) or not cohorts:
        raise ValueError("cohort manifest requires a non-empty cohorts list")
    normalized: list[dict[str, Any]] = []
    seen_accounts: set[str] = set()
    for item in cohorts:
        account = str(item.get("account") or "").strip()
        key = account.casefold()
        if not account or key in seen_accounts:
            raise ValueError(f"empty or duplicate cohort account: {account!r}")
        seen_accounts.add(key)
        normalized.append({
            "account": account,
            "data": resolve_repository_path(str(item["data"])),
            "marks": resolve_repository_path(str(item["marks"])),
        })
    return manifest, normalized


def recover_numeric_feature(
    x: np.ndarray,
    checkpoint: dict[str, Any],
    feature_name: str,
) -> np.ndarray:
    input_features = list(checkpoint["input_features"])
    preprocessing = checkpoint["preprocessing"]
    numeric_features = list(preprocessing["numeric_features"])
    if feature_name not in input_features or feature_name not in numeric_features:
        raise ValueError(f"checkpoint lacks numeric feature {feature_name!r}")
    input_index = input_features.index(feature_name)
    numeric_index = numeric_features.index(feature_name)
    mean = float(preprocessing["means"][numeric_index])
    std = float(preprocessing["stds"][numeric_index])
    if not np.isfinite(std) or std <= 0:
        raise ValueError(f"invalid preprocessing std for {feature_name!r}: {std}")
    return x[..., input_index].astype(np.float64) * std + mean


def validate_time_model_npz(path: Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    required = {
        "X", "board_tokens", "board_move_tokens", "current_hint_tokens",
        "current_hint_values", "prev_own_hint_values", "actual_thinking_time_ms",
        "game_id", "split", "player_id", "side_to_move", "move_index",
        "source_ply_including_pass", "global_placement_ply", "input_features",
        "board_cnn_channels", "preprocessing_sha256", "input_policy",
    }
    with np.load(path, allow_pickle=False) as data:
        missing = sorted(required - set(data.files))
        if missing:
            raise ValueError(f"time-model NPZ missing arrays: {missing}")
        x = data["X"]
        if x.ndim != 3 or x.shape[-1] != len(checkpoint["input_features"]):
            raise ValueError(f"invalid X shape for time inference: {x.shape}")
        shape = x.shape[:2]
        expected_shapes = {
            "board_tokens": (*shape, 3, 64),
            "board_move_tokens": (*shape, 3),
            "current_hint_tokens": (*shape, 6),
            "current_hint_values": (*shape, 4),
            "prev_own_hint_values": (*shape, 2),
            "actual_thinking_time_ms": shape,
            "player_id": shape,
            "side_to_move": shape,
            "move_index": shape,
            "source_ply_including_pass": shape,
            "global_placement_ply": shape,
        }
        for name, expected in expected_shapes.items():
            if tuple(data[name].shape) != expected:
                raise ValueError(f"invalid {name} shape: expected {expected}, got {data[name].shape}")
        if data["input_features"].astype(str).tolist() != list(checkpoint["input_features"]):
            raise ValueError("time-model NPZ input feature order differs from base checkpoint")
        if data["board_cnn_channels"].astype(str).tolist() != list(checkpoint["board_encoding"]["cnn_channels"]):
            raise ValueError("time-model NPZ board channel order differs from base checkpoint")
        expected_preprocessing = json_hash(checkpoint["preprocessing"])
        actual_preprocessing = str(data["preprocessing_sha256"].reshape(-1)[0])
        if actual_preprocessing != expected_preprocessing:
            raise ValueError(
                f"time-model NPZ preprocessing hash mismatch: {actual_preprocessing} != {expected_preprocessing}"
            )
        input_policy = str(data["input_policy"].reshape(-1)[0])
        if input_policy != "uniform-no-current-player-loss-history-v1":
            raise ValueError(f"unexpected time-model input policy: {input_policy!r}")
        valid_nodes = data["global_placement_ply"] > 0
        if not bool(valid_nodes.any()):
            raise ValueError("time-model NPZ has no actual placement nodes")
        if not bool(np.isfinite(data["actual_thinking_time_ms"][valid_nodes]).all()):
            raise ValueError("time-model NPZ has non-finite actual thinking times")
        return {
            "ok": True,
            "games": int(len(data["game_id"])),
            "sequenceLength": int(shape[1]),
            "inputFeatures": int(x.shape[-1]),
            "actualPlacementNodes": int(valid_nodes.sum()),
            "preprocessingSha256": actual_preprocessing,
            "inputPolicy": input_policy,
            "validationScope": "strict-time-inference-arrays-v1",
        }


@torch.no_grad()
def predict_time_log(
    model: torch.nn.Module,
    data: np.lib.npyio.NpzFile,
    device: torch.device,
    batch_size: int,
) -> np.ndarray:
    games, steps = data["X"].shape[:2]
    result = np.zeros((games, steps), dtype=np.float32)
    for start in range(0, games, batch_size):
        stop = min(start + batch_size, games)
        sl = slice(start, stop)
        result[sl] = model.backbone(
            torch.from_numpy(data["X"][sl]).float().to(device),
            torch.from_numpy(data["board_tokens"][sl]).to(device),
            torch.from_numpy(data["board_move_tokens"][sl]).to(device),
            torch.from_numpy(data["current_hint_tokens"][sl]).to(device),
            torch.from_numpy(data["current_hint_values"][sl]).float().to(device),
            torch.from_numpy(data["prev_own_hint_values"][sl]).float().to(device),
        ).cpu().numpy()
    return result


def load_marks(path: Path, expected_account: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "player-offbook-agent-marks-input-v1":
        raise ValueError(f"unexpected marks schema: {path}")
    if str(payload.get("account") or "").casefold() != expected_account.casefold():
        raise ValueError(f"marks account mismatch for {expected_account!r}: {path}")
    marks = payload.get("marks")
    if not isinstance(marks, list) or not marks:
        raise ValueError(f"marks file is empty: {path}")
    result: dict[str, dict[str, Any]] = {}
    for item in marks:
        game_id = str(item.get("gameId") or "")
        judgment = str(item.get("judgment") or "")
        if not game_id or game_id in result:
            raise ValueError(f"empty or duplicate marked game in {path}: {game_id!r}")
        if judgment not in {"offbook", "no_offbook"}:
            raise ValueError(f"invalid judgment for {game_id!r}: {judgment!r}")
        offbook_ply = item.get("offBookPly")
        if judgment == "offbook" and (isinstance(offbook_ply, bool) or not isinstance(offbook_ply, int)):
            raise ValueError(f"offbook game {game_id!r} requires an integer offBookPly")
        if judgment == "no_offbook" and offbook_ply is not None:
            raise ValueError(f"no_offbook game {game_id!r} requires a null offBookPly")
        result[game_id] = {
            "judgment": judgment,
            "offbook_ply": offbook_ply,
            "agent_note": str(item.get("agentNote") or ""),
        }
    return result


def cohort_prediction_frame(
    cohort: dict[str, Any],
    model: torch.nn.Module,
    checkpoint: dict[str, Any],
    device: torch.device,
    batch_size: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    account = cohort["account"]
    data_path = cohort["data"]
    marks_path = cohort["marks"]
    marks = load_marks(marks_path, account)
    validation = validate_time_model_npz(data_path, checkpoint)
    with np.load(data_path, allow_pickle=False) as data:
        game_ids = data["game_id"].astype(str)
        if set(game_ids) != set(marks):
            raise ValueError(
                f"marked games and NPZ games differ for {account!r}: "
                f"marks-only={sorted(set(marks) - set(game_ids))}, "
                f"data-only={sorted(set(game_ids) - set(marks))}"
            )
        pred_log = predict_time_log(model, data, device, batch_size)
        raw_seconds = np.expm1(pred_log.astype(np.float64))
        remaining_seconds = recover_numeric_feature(data["X"], checkpoint, "remaining_before_s")
        upper_seconds = np.maximum(0.05, remaining_seconds * 0.95)
        official_seconds = np.minimum(np.maximum(raw_seconds, 0.05), upper_seconds)

        games, steps = data["X"].shape[:2]
        game_grid = np.broadcast_to(game_ids[:, None], (games, steps))
        split_grid = np.broadcast_to(data["split"].astype(str)[:, None], (games, steps))
        actual_players = data["player_id"].astype(str)
        valid = (
            (data["global_placement_ply"] > 0)
            & (np.char.lower(actual_players) == account.casefold())
        )
        frame = pd.DataFrame({
            "account": np.full(int(valid.sum()), account),
            "game_id": game_grid[valid],
            "source_split": split_grid[valid],
            "player_id": actual_players[valid],
            "side_to_move": data["side_to_move"].astype(str)[valid],
            "move_index": data["move_index"][valid],
            "source_ply_including_pass": data["source_ply_including_pass"][valid],
            "global_placement_ply": data["global_placement_ply"][valid],
            "actual_thinking_time_ms": data["actual_thinking_time_ms"][valid],
            "predicted_time_log_seconds": pred_log[valid],
            "predicted_thinking_time_raw_ms": raw_seconds[valid] * 1000.0,
            "predicted_thinking_time_ms": official_seconds[valid] * 1000.0,
            "remaining_before_ms": remaining_seconds[valid] * 1000.0,
        })

    frame = frame.sort_values(["game_id", "move_index"], kind="stable").reset_index(drop=True)
    frame["target_decision_number"] = frame.groupby("game_id", sort=False).cumcount() + 1
    frame["manual_judgment"] = frame["game_id"].map(lambda game_id: marks[game_id]["judgment"])
    frame["manual_offbook_ply"] = frame["game_id"].map(lambda game_id: marks[game_id]["offbook_ply"])
    frame["agent_note"] = frame["game_id"].map(lambda game_id: marks[game_id]["agent_note"])
    frame["is_manual_anchor"] = (
        frame["manual_judgment"].eq("offbook")
        & frame["global_placement_ply"].eq(frame["manual_offbook_ply"])
    )

    expected_anchors = sum(item["judgment"] == "offbook" for item in marks.values())
    if int(frame["is_manual_anchor"].sum()) != expected_anchors:
        found = set(frame.loc[frame["is_manual_anchor"], "game_id"])
        missing = sorted(game_id for game_id, item in marks.items() if item["judgment"] == "offbook" and game_id not in found)
        raise ValueError(f"manual anchors do not map to target-player model nodes for {account!r}: {missing}")

    frame["actual_time_log_seconds"] = np.log1p(frame["actual_thinking_time_ms"] / 1000.0)
    frame["predicted_time_official_log_seconds"] = np.log1p(frame["predicted_thinking_time_ms"] / 1000.0)
    frame["time_residual_ms"] = frame["actual_thinking_time_ms"] - frame["predicted_thinking_time_ms"]
    frame["time_log_residual"] = (
        frame["actual_time_log_seconds"] - frame["predicted_time_official_log_seconds"]
    )
    frame["actual_to_predicted_ratio"] = (
        frame["actual_thinking_time_ms"] / frame["predicted_thinking_time_ms"].clip(lower=50.0)
    )
    frame["absolute_time_log_error"] = frame["time_log_residual"].abs()
    grouped = frame.groupby("game_id", sort=False)
    frame["within_game_positive_residual_rank"] = grouped["time_log_residual"].rank(
        method="min", ascending=False
    ).astype(int)
    frame["within_game_residual_percentile"] = grouped["time_log_residual"].rank(
        method="average", pct=True, ascending=True
    )
    anchor_decisions = frame.loc[frame["is_manual_anchor"]].set_index("game_id")["target_decision_number"]
    frame["manual_anchor_decision_number"] = frame["game_id"].map(anchor_decisions)
    frame["target_decision_offset_from_anchor"] = (
        frame["target_decision_number"] - frame["manual_anchor_decision_number"]
    )
    marked_games = pd.DataFrame([
        {
            "account": account,
            "game_id": game_id,
            "manual_judgment": item["judgment"],
            "manual_offbook_ply": item["offbook_ply"],
        }
        for game_id, item in marks.items()
    ])
    return frame, marked_games, {
        "account": account,
        "games": len(marks),
        "offbookGames": expected_anchors,
        "noOffbookGames": sum(item["judgment"] == "no_offbook" for item in marks.values()),
        "targetNodes": int(len(frame)),
        "data": str(data_path),
        "dataSha256": sha256_file(data_path),
        "marks": str(marks_path),
        "marksSha256": sha256_file(marks_path),
        "dataValidation": validation,
    }


def finite_summary(values: pd.Series) -> dict[str, float | int | None]:
    clean = values.to_numpy(dtype=float)
    clean = clean[np.isfinite(clean)]
    if len(clean) == 0:
        return {"count": 0, "mean": None, "q10": None, "median": None, "q90": None}
    q10, median, q90 = np.quantile(clean, [0.1, 0.5, 0.9])
    return {
        "count": int(len(clean)),
        "mean": float(np.mean(clean)),
        "q10": float(q10),
        "median": float(median),
        "q90": float(q90),
    }


def game_summary(frame: pd.DataFrame, marked_games: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (account, game_id), game in frame.groupby(["account", "game_id"], sort=True):
        ranked = game.sort_values(
            ["time_log_residual", "target_decision_number"], ascending=[False, True], kind="stable"
        )
        top = ranked.iloc[0]
        anchor_rows = game.loc[game["is_manual_anchor"]]
        anchor = anchor_rows.iloc[0] if len(anchor_rows) == 1 else None
        rows.append({
            "account": account,
            "game_id": game_id,
            "manual_judgment": game["manual_judgment"].iloc[0],
            "manual_offbook_ply": game["manual_offbook_ply"].iloc[0],
            "target_nodes": int(len(game)),
            "top_residual_ply": int(top["global_placement_ply"]),
            "top_residual_target_decision": int(top["target_decision_number"]),
            "top_time_log_residual": float(top["time_log_residual"]),
            "top_actual_to_predicted_ratio": float(top["actual_to_predicted_ratio"]),
            "anchor_actual_thinking_time_ms": float(anchor["actual_thinking_time_ms"]) if anchor is not None else np.nan,
            "anchor_predicted_thinking_time_ms": float(anchor["predicted_thinking_time_ms"]) if anchor is not None else np.nan,
            "anchor_time_residual_ms": float(anchor["time_residual_ms"]) if anchor is not None else np.nan,
            "anchor_time_log_residual": float(anchor["time_log_residual"]) if anchor is not None else np.nan,
            "anchor_actual_to_predicted_ratio": float(anchor["actual_to_predicted_ratio"]) if anchor is not None else np.nan,
            "anchor_positive_residual_rank": int(anchor["within_game_positive_residual_rank"]) if anchor is not None else np.nan,
            "anchor_residual_percentile": float(anchor["within_game_residual_percentile"]) if anchor is not None else np.nan,
            "top_matches_anchor": bool(anchor is not None and int(top["global_placement_ply"]) == int(anchor["global_placement_ply"])),
            "top_within_one_target_decision": bool(
                anchor is not None
                and abs(int(top["target_decision_number"]) - int(anchor["target_decision_number"])) <= 1
            ),
            "top_within_two_target_decisions": bool(
                anchor is not None
                and abs(int(top["target_decision_number"]) - int(anchor["target_decision_number"])) <= 2
            ),
        })
    result = pd.DataFrame(rows)
    existing = set(zip(result["account"], result["game_id"], strict=True))
    for mark in marked_games.itertuples(index=False):
        if (mark.account, mark.game_id) in existing:
            continue
        rows.append({
            "account": mark.account,
            "game_id": mark.game_id,
            "manual_judgment": mark.manual_judgment,
            "manual_offbook_ply": mark.manual_offbook_ply,
            "target_nodes": 0,
            "top_residual_ply": np.nan,
            "top_residual_target_decision": np.nan,
            "top_time_log_residual": np.nan,
            "top_actual_to_predicted_ratio": np.nan,
            "anchor_actual_thinking_time_ms": np.nan,
            "anchor_predicted_thinking_time_ms": np.nan,
            "anchor_time_residual_ms": np.nan,
            "anchor_time_log_residual": np.nan,
            "anchor_actual_to_predicted_ratio": np.nan,
            "anchor_positive_residual_rank": np.nan,
            "anchor_residual_percentile": np.nan,
            "top_matches_anchor": False,
            "top_within_one_target_decision": False,
            "top_within_two_target_decisions": False,
        })
    return pd.DataFrame(rows).sort_values(["account", "game_id"], kind="stable").reset_index(drop=True)


def threshold_scan(
    frame: pd.DataFrame,
    game_index: pd.DataFrame,
    score_column: str,
    thresholds: tuple[float, ...],
) -> list[dict[str, Any]]:
    grouped = {
        key: game for key, game in frame.groupby(["account", "game_id"], sort=True)
    }
    anchored_games = int(game_index["manual_judgment"].eq("offbook").sum())
    no_offbook_games = int(game_index["manual_judgment"].eq("no_offbook").sum())
    results = []
    for threshold in thresholds:
        predicted = 0
        exact = within_one = within_two = anchored_with_candidate = no_offbook_without_candidate = 0
        for game_row in game_index.itertuples(index=False):
            game = grouped.get((game_row.account, game_row.game_id))
            if game is None:
                if game_row.manual_judgment != "no_offbook":
                    raise ValueError(
                        f"anchored game has no target nodes: {(game_row.account, game_row.game_id)}"
                    )
                no_offbook_without_candidate += 1
                continue
            candidates = game.loc[game[score_column] >= threshold].sort_values("target_decision_number")
            candidate = candidates.iloc[0] if not candidates.empty else None
            judgment = game["manual_judgment"].iloc[0]
            if candidate is not None:
                predicted += 1
            if judgment == "no_offbook":
                no_offbook_without_candidate += candidate is None
                continue
            anchor = game.loc[game["is_manual_anchor"]].iloc[0]
            if candidate is None:
                continue
            anchored_with_candidate += 1
            distance = abs(int(candidate["target_decision_number"]) - int(anchor["target_decision_number"]))
            exact += distance == 0
            within_one += distance <= 1
            within_two += distance <= 2
        results.append({
            "threshold": float(threshold),
            "gamesWithCandidate": int(predicted),
            "candidateGameRate": float(predicted / len(game_index)),
            "anchoredGamesWithCandidateRate": float(anchored_with_candidate / anchored_games),
            "exactAnchorRate": float(exact / anchored_games),
            "withinOneTargetDecisionRate": float(within_one / anchored_games),
            "withinTwoTargetDecisionsRate": float(within_two / anchored_games),
            "noOffbookSpecificity": float(no_offbook_without_candidate / no_offbook_games),
        })
    return results


def per_player_summary(games: pd.DataFrame) -> list[dict[str, Any]]:
    output = []
    for account, group in games.groupby("account", sort=True):
        anchored = group.loc[group["manual_judgment"].eq("offbook")]
        output.append({
            "account": account,
            "games": int(len(group)),
            "offbookGames": int(len(anchored)),
            "noOffbookGames": int(group["manual_judgment"].eq("no_offbook").sum()),
            "anchorPositiveResidualRate": float((anchored["anchor_time_log_residual"] > 0).mean()),
            "anchorTop1Rate": float(anchored["top_matches_anchor"].mean()),
            "anchorTop3Rate": float((anchored["anchor_positive_residual_rank"] <= 3).mean()),
            "topWithinOneTargetDecisionRate": float(anchored["top_within_one_target_decision"].mean()),
            "medianAnchorActualToPredictedRatio": float(anchored["anchor_actual_to_predicted_ratio"].median()),
            "medianAnchorLogResidual": float(anchored["anchor_time_log_residual"].median()),
        })
    return output


def build_report(
    frame: pd.DataFrame,
    games: pd.DataFrame,
    cohorts: list[dict[str, Any]],
    checkpoint_path: Path,
    device: torch.device,
) -> dict[str, Any]:
    anchors = frame.loc[frame["is_manual_anchor"]]
    offbook = frame.loc[frame["manual_judgment"].eq("offbook")]
    pre_anchor = offbook.loc[offbook["target_decision_offset_from_anchor"] < 0]
    post_other = offbook.loc[offbook["target_decision_offset_from_anchor"] > 0]
    no_offbook = frame.loc[frame["manual_judgment"].eq("no_offbook")]
    labels = frame["is_manual_anchor"].to_numpy(dtype=int)
    anchor_games = games.loc[games["manual_judgment"].eq("offbook")]
    return {
        "schema": "manual-offbook-base-time-residual-evaluation-v1",
        "status": "completed",
        "purpose": (
            "Evaluate whether Agent-reviewed off-book anchors have unusually large actual thinking time "
            "relative to the frozen single base CNN+causal-TCN time head."
        ),
        "predictionPolicy": {
            "model": "single frozen base checkpoint",
            "timeHeadUsesCurrentActualTime": False,
            "sequence": "full alternating placement sequence; causal TCN output evaluated only at target-player nodes",
            "primaryResidual": "log1p(actual seconds) - log1p(official-clamped predicted seconds)",
            "officialPredictionClamp": "[0.05 seconds, 0.95 * remaining_before_s]",
            "thresholdSelection": "none; fixed descriptive scans only",
        },
        "counts": {
            "players": int(frame["account"].nunique()),
            "playerGameEvaluations": int(len(games)),
            "uniqueSourceGames": int(games["game_id"].nunique()),
            "offbookGames": int(games["manual_judgment"].eq("offbook").sum()),
            "noOffbookGames": int(games["manual_judgment"].eq("no_offbook").sum()),
            "targetNodes": int(len(frame)),
            "manualAnchorNodes": int(frame["is_manual_anchor"].sum()),
        },
        "anchorIdentification": {
            "anchorActualGreaterThanPredictedRate": float((anchors["time_log_residual"] > 0).mean()),
            "anchorTop1WithinGameRate": float(anchor_games["top_matches_anchor"].mean()),
            "anchorTop3WithinGameRate": float((anchor_games["anchor_positive_residual_rank"] <= 3).mean()),
            "anchorTop5WithinGameRate": float((anchor_games["anchor_positive_residual_rank"] <= 5).mean()),
            "maximumResidualWithinOneTargetDecisionRate": float(
                anchor_games["top_within_one_target_decision"].mean()
            ),
            "maximumResidualWithinTwoTargetDecisionsRate": float(
                anchor_games["top_within_two_target_decisions"].mean()
            ),
            "medianAnchorPositiveResidualRank": float(anchor_games["anchor_positive_residual_rank"].median()),
            "medianAnchorResidualPercentile": float(anchor_games["anchor_residual_percentile"].median()),
        },
        "nodeClassification": {
            "positiveClass": "exact manual anchor node only",
            "prevalence": float(labels.mean()),
            "logResidualRocAuc": float(roc_auc_score(labels, frame["time_log_residual"])),
            "logResidualAveragePrecision": float(average_precision_score(labels, frame["time_log_residual"])),
            "timeRatioRocAuc": float(roc_auc_score(labels, frame["actual_to_predicted_ratio"])),
            "timeRatioAveragePrecision": float(
                average_precision_score(labels, frame["actual_to_predicted_ratio"])
            ),
        },
        "residualDistributions": {
            "manualAnchorLogResidual": finite_summary(anchors["time_log_residual"]),
            "preAnchorLogResidual": finite_summary(pre_anchor["time_log_residual"]),
            "postAnchorOtherLogResidual": finite_summary(post_other["time_log_residual"]),
            "noOffbookLogResidual": finite_summary(no_offbook["time_log_residual"]),
            "manualAnchorActualToPredictedRatio": finite_summary(anchors["actual_to_predicted_ratio"]),
        },
        "firstExceedanceScans": {
            "actualToPredictedRatio": threshold_scan(
                frame, games, "actual_to_predicted_ratio", RATIO_THRESHOLDS
            ),
            "timeLogResidual": threshold_scan(
                frame, games, "time_log_residual", LOG_RESIDUAL_THRESHOLDS
            ),
        },
        "perPlayer": per_player_summary(games),
        "inputs": {
            "baseCheckpoint": str(checkpoint_path.resolve()),
            "baseCheckpointSha256": sha256_file(checkpoint_path),
            "device": str(device),
            "cohorts": cohorts,
        },
        "limitations": [
            "This is a retrospective association test on Agent-reviewed games, not a fitted off-book classifier.",
            "The manual anchors were primarily chosen from timing continuity, so strong association is expected and is not independent validation of psychological book knowledge.",
            "The base time head is causal and does not use future nodes; this evaluation does not yet combine the target player's time with later opponent thinking time.",
            "No residual threshold is selected on these 180 games; the scans are descriptive and must not be reported as held-out performance.",
        ],
    }


def markdown_report(report: dict[str, Any]) -> str:
    counts = report["counts"]
    ident = report["anchorIdentification"]
    classification = report["nodeClassification"]
    anchor_dist = report["residualDistributions"]["manualAnchorActualToPredictedRatio"]
    lines = [
        "# 单一 CNN+TCN 时间基线与人工脱谱锚点评估",
        "",
        "## 样本",
        "",
        f"- {counts['players']} 位选手，{counts['playerGameEvaluations']} 个选手－对局评估（{counts['uniqueSourceGames']} 个唯一源对局）；其中 {counts['offbookGames']} 个有人工锚点，{counts['noOffbookGames']} 个为 `no_offbook`。",
        f"- 共评估 {counts['targetNodes']} 个目标选手节点，人工锚点 {counts['manualAnchorNodes']} 个。",
        "",
        "## 核心结果",
        "",
        f"- 人工锚点实际用时高于模型预期：{ident['anchorActualGreaterThanPredictedRate']:.1%}。",
        f"- 人工锚点是该局最大正残差节点：{ident['anchorTop1WithinGameRate']:.1%}。",
        f"- 人工锚点进入该局残差 Top-3 / Top-5：{ident['anchorTop3WithinGameRate']:.1%} / {ident['anchorTop5WithinGameRate']:.1%}。",
        f"- 该局最大残差落在人工锚点前后 1 / 2 个目标决策内：{ident['maximumResidualWithinOneTargetDecisionRate']:.1%} / {ident['maximumResidualWithinTwoTargetDecisionsRate']:.1%}。",
        f"- 人工锚点的实际/预测时间比中位数：{anchor_dist['median']:.3f}。",
        f"- 以精确人工锚点为正类，节点级对数残差 ROC-AUC / AP：{classification['logResidualRocAuc']:.3f} / {classification['logResidualAveragePrecision']:.3f}。",
        "",
        "## 解释边界",
        "",
        "本结果检验的是现有单模型时间头的低估残差是否集中在人工锚点。没有在这 180 局上选择正式异常阈值；JSON 中的阈值扫描仅用于观察命中率与 `no_offbook` 误报率之间的关系。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing output directory: {args.output_dir}")
    if args.batch_size <= 0:
        raise ValueError("batch size must be positive")
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("requested CUDA inference but torch.cuda.is_available() is false")

    manifest, cohort_specs = load_manifest(args.cohort_manifest.resolve())
    checkpoint_path = args.base_checkpoint.resolve()
    checkpoint = load_checkpoint_payload(checkpoint_path)
    model, _ = load_transferred_model(checkpoint_path)
    device = torch.device(args.device)
    model.to(device).eval()

    frames = []
    marked_game_frames = []
    cohort_reports = []
    for cohort in cohort_specs:
        frame, marked_games, cohort_report = cohort_prediction_frame(
            cohort, model, checkpoint, device, args.batch_size
        )
        frames.append(frame)
        marked_game_frames.append(marked_games)
        cohort_reports.append(cohort_report)
    nodes = pd.concat(frames, ignore_index=True)
    marked_games = pd.concat(marked_game_frames, ignore_index=True)
    if nodes.duplicated(["account", "game_id", "move_index"]).any():
        raise ValueError("combined node output contains duplicate account/game/move keys")
    if marked_games.duplicated(["account", "game_id"]).any():
        raise ValueError("combined manual marks contain duplicate account/game keys")
    games = game_summary(nodes, marked_games)
    report = build_report(nodes, games, cohort_reports, checkpoint_path, device)
    report["inputs"]["cohortManifest"] = str(args.cohort_manifest.resolve())
    report["inputs"]["cohortManifestSha256"] = sha256_file(args.cohort_manifest.resolve())
    report["inputs"]["cohortManifestSchema"] = manifest["schema"]

    args.output_dir.mkdir(parents=True, exist_ok=False)
    nodes.to_csv(args.output_dir / "node_predictions.csv", index=False, encoding="utf-8")
    games.to_csv(args.output_dir / "game_summary.csv", index=False, encoding="utf-8")
    (args.output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "REPORT.md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({
        "status": "completed",
        "outputDir": str(args.output_dir.resolve()),
        "counts": report["counts"],
        "anchorIdentification": report["anchorIdentification"],
        "nodeClassification": report["nodeClassification"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
