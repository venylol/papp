#!/usr/bin/env python3
"""Compare control and reported predictions before and after manual off-book anchors."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

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


def game_values(frame: pd.DataFrame, actual: str, probability: str) -> pd.DataFrame:
    grouped = frame.groupby("game_id", sort=True)
    return grouped.agg(
        nodes=(actual, "size"),
        actualRate=(actual, "mean"),
        expectedRate=(probability, "mean"),
    ).assign(residual=lambda value: value["actualRate"] - value["expectedRate"])


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "games": int(frame["game_id"].nunique()),
        "nodes": int(len(frame)),
        "metrics": {},
    }
    for name, (actual, probability) in METRICS.items():
        per_game = game_values(frame, actual, probability)
        result["metrics"][name] = {
            "actualCount": float(frame[actual].sum()),
            "expectedCount": float(frame[probability].sum()),
            "nodeActualRate": float(frame[actual].mean()),
            "nodeExpectedRate": float(frame[probability].mean()),
            "nodeActualMinusExpected": float((frame[actual] - frame[probability]).mean()),
            "gameEqualActualRate": float(per_game["actualRate"].mean()),
            "gameEqualExpectedRate": float(per_game["expectedRate"].mean()),
            "gameEqualActualMinusExpected": float(per_game["residual"].mean()),
        }
    return result


def bootstrap_difference(
    control: pd.DataFrame,
    reported: pd.DataFrame,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    output: dict[str, Any] = {}
    for name, (actual, probability) in METRICS.items():
        control_values = game_values(control, actual, probability)["residual"].to_numpy(float)
        reported_values = game_values(reported, actual, probability)["residual"].to_numpy(float)
        control_draws = rng.choice(control_values, size=(replicates, len(control_values)), replace=True).mean(axis=1)
        reported_draws = rng.choice(reported_values, size=(replicates, len(reported_values)), replace=True).mean(axis=1)
        differences = reported_draws - control_draws
        lower, upper = np.quantile(differences, [0.025, 0.975])
        output[name] = {
            "reportedMinusControlGameEqualActualMinusExpected": float(
                reported_values.mean() - control_values.mean()
            ),
            "bootstrap95PercentInterval": {"lower": float(lower), "upper": float(upper)},
        }
    return output


def validate_frame(frame: pd.DataFrame, label: str) -> None:
    required = {"game_id", "global_placement_ply"}
    for actual, probability in METRICS.values():
        required.update((actual, probability))
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} predictions lack columns: {missing}")
    if frame.empty or frame[list(required)].isna().any().any():
        raise ValueError(f"{label} predictions contain no rows or missing required values")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--reported", type=Path, required=True)
    parser.add_argument("--offbook-records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=10000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260805)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite: {args.output}")

    control = pd.read_csv(args.control, encoding="utf-8", dtype={"game_id": str})
    reported = pd.read_csv(args.reported, encoding="utf-8", dtype={"game_id": str})
    validate_frame(control, "control")
    validate_frame(reported, "reported")
    control_ids = set(control["game_id"])
    reported_ids = set(reported["game_id"])
    if control_ids & reported_ids:
        raise ValueError("control and reported game IDs overlap")

    records = json.loads(args.offbook_records.read_text(encoding="utf-8"))
    if records.get("schema") != "player-offbook-manual-records-v1":
        raise ValueError("off-book records have the wrong schema")
    by_game = {str(item["gameId"]): item for item in records.get("records", [])}
    if set(by_game) != control_ids | reported_ids:
        raise ValueError("off-book records do not exactly cover prediction game IDs")
    anchors = {
        game_id: int(item["offBookPly"])
        for game_id, item in by_game.items()
        if item.get("judgment") == "offbook"
    }

    def post_offbook(frame: pd.DataFrame) -> pd.DataFrame:
        anchored = frame[frame["game_id"].isin(anchors)].copy()
        threshold = anchored["game_id"].map(anchors)
        return anchored.loc[anchored["global_placement_ply"] >= threshold].reset_index(drop=True)

    segments = {
        "fullGame": (control, reported),
        "postOffBookInclusive": (post_offbook(control), post_offbook(reported)),
    }
    output_segments: dict[str, Any] = {}
    for index, (segment, (control_frame, reported_frame)) in enumerate(segments.items()):
        if control_frame.empty or reported_frame.empty:
            raise ValueError(f"segment {segment} has an empty group")
        output_segments[segment] = {
            "control": summarize(control_frame),
            "reported": summarize(reported_frame),
            "difference": bootstrap_difference(
                control_frame,
                reported_frame,
                args.bootstrap_replicates,
                args.bootstrap_seed + index,
            ),
        }

    report = {
        "schema": "profile-tcn-control-reported-comparison-v1",
        "status": "completed",
        "interpretation": "Actual indicator minus the fixed twelve-member Profile ensemble probability.",
        "notCheatingProbability": True,
        "aggregationUnit": "whole-game",
        "bootstrapReplicates": args.bootstrap_replicates,
        "bootstrapSeed": args.bootstrap_seed,
        "controlGameIds": sorted(control_ids),
        "reportedGameIds": sorted(reported_ids),
        "manualOffbookGlobalPlacementPly": anchors,
        "segments": output_segments,
        "inputs": {
            "controlPredictions": str(args.control.resolve()),
            "controlPredictionsSha256": sha256_file(args.control),
            "reportedPredictions": str(args.reported.resolve()),
            "reportedPredictionsSha256": sha256_file(args.reported),
            "offbookRecords": str(args.offbook_records.resolve()),
            "offbookRecordsSha256": sha256_file(args.offbook_records),
        },
        "limitations": [
            "The reported group contains only two games.",
            "Current cumulative OQ Player profiles are retrospectively applied to historical games.",
            "Intervals describe whole-game sampling stability and are not cheating probabilities.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
