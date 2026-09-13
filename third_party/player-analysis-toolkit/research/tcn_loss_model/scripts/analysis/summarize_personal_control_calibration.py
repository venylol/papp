#!/usr/bin/env python3
"""Summarize in-sample calibration of a personal TCN ensemble on control games."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score


METRICS = {
    "zero": ("actual_loss_zero", "probability_loss_zero"),
    "ge4": ("actual_loss_ge4", "probability_loss_ge4"),
    "ge10": ("actual_loss_ge10", "probability_loss_ge10"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def calibration_bins(labels: np.ndarray, probabilities: np.ndarray, count: int = 10) -> list[dict[str, Any]]:
    edges = np.linspace(0.0, 1.0, count + 1)
    bins = np.minimum(np.searchsorted(edges, probabilities, side="right") - 1, count - 1)
    output = []
    for index in range(count):
        selected = bins == index
        output.append({
            "lower": float(edges[index]),
            "upper": float(edges[index + 1]),
            "nodes": int(selected.sum()),
            "meanProbability": float(probabilities[selected].mean()) if selected.any() else None,
            "actualRate": float(labels[selected].mean()) if selected.any() else None,
        })
    return output


def metric_summary(frame: pd.DataFrame, actual: str, probability: str) -> dict[str, Any]:
    labels = frame[actual].to_numpy(dtype=int)
    probabilities = np.clip(frame[probability].to_numpy(dtype=float), 1e-7, 1 - 1e-7)
    bins = calibration_bins(labels, probabilities)
    ece = sum(
        item["nodes"] * abs(item["actualRate"] - item["meanProbability"])
        for item in bins if item["nodes"]
    ) / len(frame)
    per_game = frame.groupby("game_id", sort=True).agg(
        actualRate=(actual, "mean"), meanProbability=(probability, "mean")
    )
    return {
        "actualRate": float(labels.mean()),
        "meanPredictedProbability": float(probabilities.mean()),
        "actualMinusPredicted": float(labels.mean() - probabilities.mean()),
        "absoluteProbabilityGap": float(abs(labels.mean() - probabilities.mean())),
        "gameEqualActualRate": float(per_game["actualRate"].mean()),
        "gameEqualMeanPredictedProbability": float(per_game["meanProbability"].mean()),
        "gameEqualActualMinusPredicted": float((per_game["actualRate"] - per_game["meanProbability"]).mean()),
        "brierScore": float(brier_score_loss(labels, probabilities)),
        "logLoss": float(log_loss(labels, probabilities, labels=[0, 1])),
        "rocAuc": float(roc_auc_score(labels, probabilities)),
        "prAuc": float(average_precision_score(labels, probabilities)),
        "expectedCalibrationError10Bin": float(ece),
        "calibrationBins": bins,
    }


def segment_summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "games": int(frame["game_id"].nunique()),
        "nodes": int(len(frame)),
        "metrics": {
            name: metric_summary(frame, actual, probability)
            for name, (actual, probability) in METRICS.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--offbook-records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite: {args.output}")

    frame = pd.read_csv(args.predictions, encoding="utf-8", dtype={"game_id": str})
    required = {"game_id", "global_placement_ply"}
    for actual, probability in METRICS.values():
        required.update((actual, probability))
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"predictions lack columns: {missing}")
    if frame.empty or frame[list(required)].isna().any().any():
        raise ValueError("predictions are empty or contain missing required values")

    records = json.loads(args.offbook_records.read_text(encoding="utf-8"))
    anchors = {
        str(item["gameId"]): int(item["offBookPly"])
        for item in records.get("records", [])
        if item.get("judgment") == "offbook"
    }
    anchors = {game_id: ply for game_id, ply in anchors.items() if game_id in set(frame["game_id"])}
    anchored = frame.loc[frame["game_id"].isin(anchors)].copy()
    post_offbook = anchored.loc[
        anchored["global_placement_ply"] >= anchored["game_id"].map(anchors)
    ].reset_index(drop=True)
    if post_offbook.empty:
        raise ValueError("post-off-book control segment is empty")

    report = {
        "schema": "personal-tcn-control-calibration-v1",
        "status": "completed",
        "evaluationRole": "in-sample-personal-adapter-control-fit",
        "probabilitySource": "mean of twelve personal TCN adapter probabilities",
        "segments": {
            "fullControl": segment_summary(frame),
            "postOffBookInclusive": segment_summary(post_offbook),
        },
        "inputs": {
            "predictions": str(args.predictions.resolve()),
            "predictionsSha256": sha256_file(args.predictions),
            "offbookRecords": str(args.offbook_records.resolve()),
            "offbookRecordsSha256": sha256_file(args.offbook_records),
        },
        "limitations": [
            "All control-split games were used to fit each personal adapter, so these are in-sample calibration metrics.",
            "Post-off-book metrics include only games with a deterministic agent-reviewed off-book anchor.",
            "Current cumulative OQ Player profiles are retrospectively applied to historical games.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
