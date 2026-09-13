#!/usr/bin/env python3
"""Compare old and WLD ensemble predictions on one identical test-node table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score


def binary_metrics(labels: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    return {
        "logLoss": float(log_loss(labels, probabilities, labels=[0, 1])),
        "brier": float(brier_score_loss(labels, probabilities)),
        "rocAuc": float(roc_auc_score(labels, probabilities)),
        "prAuc": float(average_precision_score(labels, probabilities)),
    }


def severity_metrics(frame: pd.DataFrame) -> dict[str, object]:
    actual_loss = frame["actual_disc_loss"].to_numpy(float)
    classes = np.select((actual_loss == 0, actual_loss <= 3, actual_loss <= 9), (0, 1, 2), default=3)
    p0 = frame["probability_loss_zero"].to_numpy(float)
    p_ge4 = frame["probability_loss_ge4"].to_numpy(float)
    p3 = frame["probability_loss_ge10"].to_numpy(float)
    probabilities = np.column_stack((p0, 1.0 - p0 - p_ge4, p_ge4 - p3, p3))
    if np.any(probabilities < -1e-6):
        raise ValueError("derived severity probabilities contain negative values")
    probabilities = np.clip(probabilities, 1e-7, 1.0)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    return {
        "nodes": int(len(frame)),
        "crossEntropy": float(log_loss(classes, probabilities, labels=[0, 1, 2, 3])),
        "zero": binary_metrics((classes == 0).astype(int), p0),
        "ge4": binary_metrics((classes >= 2).astype(int), p_ge4),
        "ge10": binary_metrics((classes == 3).astype(int), p3),
    }


def wld_metrics(frame: pd.DataFrame) -> dict[str, object]:
    valid = frame["wld_applicable"].astype(str).str.lower().eq("true")
    selected = frame.loc[valid].copy()
    actual = (selected["actual_wld_loss"].to_numpy(float) * 2).astype(int)
    probabilities = selected[[
        "probability_class_no_wld_loss",
        "probability_class_half_wld_loss",
        "probability_class_full_wld_loss",
    ]].to_numpy(float)
    probabilities = np.clip(probabilities, 1e-7, 1.0)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    expected = selected["expected_wld_loss"].to_numpy(float)
    any_probability = probabilities[:, 1] + probabilities[:, 2]
    return {
        "nodes": int(len(selected)),
        "crossEntropy": float(log_loss(actual, probabilities, labels=[0, 1, 2])),
        "accuracy": float((probabilities.argmax(axis=1) == actual).mean()),
        "expectedWldLossMae": float(np.abs(expected - actual / 2.0).mean()),
        "expectedWldLossMean": float(expected.mean()),
        "actualWldLossMean": float((actual / 2.0).mean()),
        "anyLoss": binary_metrics((actual > 0).astype(int), any_probability),
        "fullLoss": binary_metrics((actual == 2).astype(int), probabilities[:, 2]),
    }


def member_loss_means(manifest_path: Path) -> dict[str, float]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metrics = [member["testMetrics"] for member in manifest["members"]]
    return {
        "thinkingTimeLoss": float(np.mean([item["thinking_time"] for item in metrics])),
        "severityClassificationLoss": float(np.mean([item["severity_classification"] for item in metrics])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-predictions", type=Path, required=True)
    parser.add_argument("--new-predictions", type=Path, required=True)
    parser.add_argument("--old-manifest", type=Path, required=True)
    parser.add_argument("--new-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite: {args.output}")
    old = pd.read_csv(args.old_predictions, encoding="utf-8")
    new = pd.read_csv(args.new_predictions, encoding="utf-8")
    identity = ["game_id", "split", "global_placement_ply", "actual_disc_loss"]
    if not old[identity].equals(new[identity]):
        raise ValueError("old and new prediction rows are not identical")
    old_metrics = {"severity": severity_metrics(old), "memberLossMeans": member_loss_means(args.old_manifest)}
    new_metrics = {
        "severity": severity_metrics(new), "wld": wld_metrics(new),
        "memberLossMeans": member_loss_means(args.new_manifest),
    }
    result = {
        "schema": "tcn-loss-ensemble-test-comparison-v1",
        "sameRows": True,
        "old": old_metrics,
        "new": new_metrics,
        "deltaNewMinusOld": {
            "severityCrossEntropy": new_metrics["severity"]["crossEntropy"] - old_metrics["severity"]["crossEntropy"],
            "thinkingTimeLossMemberMean": new_metrics["memberLossMeans"]["thinkingTimeLoss"] - old_metrics["memberLossMeans"]["thinkingTimeLoss"],
            "severityClassificationLossMemberMean": new_metrics["memberLossMeans"]["severityClassificationLoss"] - old_metrics["memberLossMeans"]["severityClassificationLoss"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
