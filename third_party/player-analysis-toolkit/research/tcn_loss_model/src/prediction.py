"""Stable per-node prediction export for later player calibration/bootstrap."""

from __future__ import annotations

import numpy as np
import pandas as pd
import torch

from .model import ModelOutput

PREDICTION_COLUMNS = (
    "game_id", "player_id", "global_placement_ply", "side_to_move",
    "actual_thinking_time_ms", "predicted_thinking_time_ms", "actual_disc_loss",
    "actual_loss_zero", "actual_loss_ge4", "actual_loss_ge10",
    "probability_loss_zero", "probability_loss_positive",
    "probability_loss_ge4", "probability_loss_ge10",
    "probability_class_zero", "probability_class_1_3",
    "probability_class_4_9", "probability_class_ge10",
    "wld_applicable", "wld_label_available", "actual_wld_loss",
    "probability_class_no_wld_loss", "probability_class_half_wld_loss",
    "probability_class_full_wld_loss", "probability_wld_any", "expected_wld_loss",
    "label_available", "is_pass_record", "has_consecutive_child",
    "child_continuity_ok", "same_side_after_move", "raw_loss",
)


def prediction_frame(metadata: pd.DataFrame, output: ModelOutput, actual_loss: torch.Tensor,
                     mask: torch.Tensor) -> pd.DataFrame:
    """Flatten model results while preserving all required audit identifiers."""
    valid = mask.detach().cpu().numpy().astype(bool).reshape(-1)
    meta = metadata.reset_index(drop=True)
    if len(meta) != int(valid.sum()):
        raise ValueError(f"metadata rows {len(meta)} != valid prediction nodes {int(valid.sum())}")

    def values(tensor: torch.Tensor) -> np.ndarray:
        return tensor.detach().cpu().numpy().reshape(-1)[valid]

    result = meta.copy()
    result["actual_disc_loss"] = values(actual_loss)
    result["predicted_thinking_time_ms"] = np.expm1(values(output.pred_time_log_seconds)) * 1000.0
    result["actual_loss_zero"] = (result["actual_disc_loss"] == 0).astype("int8")
    result["actual_loss_ge4"] = (result["actual_disc_loss"] >= 4).astype("int8")
    result["actual_loss_ge10"] = (result["actual_disc_loss"] >= 10).astype("int8")
    result["probability_loss_zero"] = values(output.probability_loss_zero)
    result["probability_loss_positive"] = values(output.probability_loss_positive)
    result["probability_loss_ge4"] = values(output.probability_loss_ge4)
    result["probability_loss_ge10"] = values(output.probability_loss_ge10)
    class_probabilities = output.severity_class_probabilities.detach().cpu().numpy().reshape(-1, 4)[valid]
    for index, name in enumerate(("zero", "1_3", "4_9", "ge10")):
        result[f"probability_class_{name}"] = class_probabilities[:, index]
    applicable = result["global_placement_ply"].to_numpy() >= 39
    result["wld_applicable"] = applicable
    if "wld_label_available" not in result:
        result["wld_label_available"] = False
    if "actual_wld_loss" not in result:
        result["actual_wld_loss"] = np.nan
    wld = output.wld_probabilities.detach().cpu().numpy().reshape(-1, 3)[valid]
    for index, name in enumerate(("no_wld_loss", "half_wld_loss", "full_wld_loss")):
        result[f"probability_class_{name}"] = np.where(applicable, wld[:, index], np.nan)
    result["probability_wld_any"] = np.where(applicable, wld[:, 1] + wld[:, 2], np.nan)
    result["expected_wld_loss"] = np.where(applicable, 0.5 * wld[:, 1] + wld[:, 2], np.nan)
    missing = [column for column in PREDICTION_COLUMNS if column not in result]
    if missing:
        raise ValueError(f"prediction metadata missing required audit fields: {missing}")
    return result.loc[:, PREDICTION_COLUMNS]


def write_predictions(frame: pd.DataFrame, path) -> None:
    frame.to_csv(path, index=False, encoding="utf-8")
