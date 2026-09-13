#!/usr/bin/env python3
"""Train one universal Polynomial Ridge Elo model from sentinel Level22 data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import joblib
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler


TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SENTINEL_DIR = (
    TOOLKIT_ROOT
    / "research"
    / "offbook_detection"
    / "data"
    / "oq_sentinel_reference_level22_1600plus_v6_20260819"
)
DEFAULT_SOURCE_DIR = (
    TOOLKIT_ROOT
    / "research"
    / "offbook_detection"
    / "data"
    / "oq_elo_matchup400_reference_level22_1600plus_20260815"
)
DEFAULT_CASES = (
    TOOLKIT_ROOT
    / "research"
    / "offbook_detection"
    / "data"
    / "oq_sentinel_elo_reference_level22_1600plus_v5_20260819"
    / "elo_calibration_cases.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    TOOLKIT_ROOT
    / "research"
    / "offbook_detection"
    / "data"
    / "oq_universal_ridge_elo_v1_20260819"
)

RAW_FEATURE_NAMES = [
    "avg_actual_eval",
    "std_actual_eval",
    "avg_best_eval",
    "std_best_eval",
    "avg_empties_before",
    "std_empties_before",
]


def account_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def load_directed_records(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_jsonl(path):
        key = (str(row.get("gameId") or ""), account_key(row.get("targetPlayerId")))
        if not all(key):
            continue
        if key in records:
            raise ValueError(f"duplicate directed record: {key}")
        records[key] = row
    return records


def empties_before(node: dict[str, Any]) -> int:
    board = str(node.get("boardBefore") or "")
    if len(board) < 64:
        raise ValueError(f"invalid boardBefore at ply {node.get('ply')}: {board!r}")
    return board[:64].count("-")


def target_steps(
    engine_game: dict[str, Any],
    account: str,
    analysis_start_ply: int,
) -> list[tuple[float, float, float]]:
    result: list[tuple[float, float, float]] = []
    for node in engine_game.get("nodes", []):
        if account_key(node.get("playerAccount")) != account_key(account):
            continue
        if int(node.get("ply") or 0) < analysis_start_ply:
            continue
        if not finite_number(node.get("actualEval")) or not finite_number(node.get("bestEval")):
            continue
        result.append(
            (
                float(node["actualEval"]),
                float(node["bestEval"]),
                float(empties_before(node)),
            )
        )
    return result


def aggregate_features(steps: Sequence[tuple[float, float, float]]) -> list[float]:
    if not steps:
        raise ValueError("cannot aggregate an empty step sequence")
    values = np.asarray(steps, dtype=np.float64)
    return [
        float(np.mean(values[:, 0])),
        float(np.std(values[:, 0])),
        float(np.mean(values[:, 1])),
        float(np.std(values[:, 1])),
        float(np.mean(values[:, 2])),
        float(np.std(values[:, 2])),
    ]


def build_feature_rows(
    cases: Sequence[dict[str, Any]],
    directed_records: dict[tuple[str, str], dict[str, Any]],
    source_dir: Path,
) -> list[dict[str, Any]]:
    engine_cache: dict[Path, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    seen_accounts: set[str] = set()
    for case in cases:
        account = account_key(case.get("account"))
        if not account or account in seen_accounts:
            raise ValueError(f"missing or duplicate case account: {account!r}")
        seen_accounts.add(account)
        steps: list[tuple[float, float, float]] = []
        selected_game_ids = [str(game_id) for game_id in case.get("selectedGameIds", [])]
        if len(selected_game_ids) != int(case.get("selectedGameCount") or 0):
            raise ValueError(f"selected game count mismatch for {account}")
        for game_id in selected_game_ids:
            directed = directed_records.get((game_id, account))
            if directed is None:
                raise ValueError(f"missing directed record for {account}/{game_id}")
            start_ply = int(directed.get("postOffBookStartsAtPly") or 0)
            relative_engine_path = Path(str(directed.get("sourceLevel22File") or ""))
            engine_path = source_dir / relative_engine_path
            if not engine_path.is_file():
                raise FileNotFoundError(engine_path)
            if engine_path not in engine_cache:
                engine_cache[engine_path] = read_json(engine_path)
            game_steps = target_steps(engine_cache[engine_path], account, start_ply)
            if not game_steps:
                raise ValueError(f"no usable post-offbook target steps for {account}/{game_id}")
            steps.extend(game_steps)
        feature_values = aggregate_features(steps)
        row: dict[str, Any] = {
            "account": account,
            "role": str(case.get("role") or ""),
            "target_elo": float(case["knownElo"]),
            "selected_game_count": len(selected_game_ids),
            "moves_count": len(steps),
        }
        row.update(dict(zip(RAW_FEATURE_NAMES, feature_values, strict=True)))
        rows.append(row)
    return rows


def build_model(alpha: float) -> Pipeline:
    return Pipeline(
        [
            ("poly", PolynomialFeatures(degree=2, include_bias=False)),
            ("scaler", StandardScaler()),
            ("ridge", Ridge(alpha=alpha)),
        ]
    )


def arrays(rows: Sequence[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = np.asarray([[row[name] for name in RAW_FEATURE_NAMES] for row in rows], dtype=np.float64)
    y = np.asarray([row["target_elo"] for row in rows], dtype=np.float64)
    weights = np.log1p(np.asarray([row["moves_count"] for row in rows], dtype=np.float64))
    return x, y, weights


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    residual = y_pred - y_true
    return {
        "count": int(len(y_true)),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(np.mean(np.square(residual)))),
        "r2": float(r2_score(y_true, y_pred)),
        "bias_pred_minus_true": float(np.mean(residual)),
        "median_absolute_error": float(np.median(np.abs(residual))),
        "p90_absolute_error": float(np.quantile(np.abs(residual), 0.9)),
        "p95_absolute_error": float(np.quantile(np.abs(residual), 0.95)),
    }


def band_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    bands = {
        "1600-1899": (y_true >= 1600) & (y_true < 1900),
        "1900-2199": (y_true >= 1900) & (y_true < 2200),
        ">=2200": y_true >= 2200,
    }
    return {name: metrics(y_true[mask], y_pred[mask]) for name, mask in bands.items() if np.any(mask)}


def prediction_rows(
    rows: Sequence[dict[str, Any]],
    raw_predictions: np.ndarray,
    clipped_predictions: np.ndarray,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row, raw_prediction, prediction in zip(rows, raw_predictions, clipped_predictions, strict=True):
        output.append(
            {
                "account": row["account"],
                "target_elo": row["target_elo"],
                "predicted_elo_raw": float(raw_prediction),
                "predicted_elo": float(prediction),
                "error_pred_minus_true": float(prediction - row["target_elo"]),
                "absolute_error": float(abs(prediction - row["target_elo"])),
                "selected_game_count": row["selected_game_count"],
                "moves_count": row["moves_count"],
            }
        )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sentinel-dir", type=Path, default=DEFAULT_SENTINEL_DIR)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--alpha", type=float, default=1.0)
    parser.add_argument("--minimum-elo", type=float, default=1600.0)
    parser.add_argument("--maximum-elo", type=float, default=2495.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.minimum_elo >= args.maximum_elo:
        raise ValueError("minimum Elo must be lower than maximum Elo")
    directed_path = args.sentinel_dir.resolve() / "directed_target_records.jsonl"
    cases_path = args.cases.resolve()
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()

    cases = read_jsonl(cases_path)
    directed_records = load_directed_records(directed_path)
    feature_rows = build_feature_rows(cases, directed_records, source_dir)
    train_rows = [row for row in feature_rows if row["role"] == "calibration"]
    validation_rows = [row for row in feature_rows if row["role"] == "validation"]
    train_accounts = {row["account"] for row in train_rows}
    validation_accounts = {row["account"] for row in validation_rows}
    overlap = sorted(train_accounts & validation_accounts)
    if overlap:
        raise ValueError(f"training/validation account overlap: {overlap[:5]}")
    if not train_rows or not validation_rows:
        raise ValueError("both training and validation rows are required")

    x_train, y_train, train_weights = arrays(train_rows)
    x_validation, y_validation, _ = arrays(validation_rows)
    model = build_model(float(args.alpha))
    model.fit(x_train, y_train, ridge__sample_weight=train_weights)
    raw_train_predictions = model.predict(x_train)
    raw_validation_predictions = model.predict(x_validation)
    train_predictions = np.clip(raw_train_predictions, args.minimum_elo, args.maximum_elo)
    validation_predictions = np.clip(raw_validation_predictions, args.minimum_elo, args.maximum_elo)

    train_output = prediction_rows(train_rows, raw_train_predictions, train_predictions)
    validation_output = prediction_rows(validation_rows, raw_validation_predictions, validation_predictions)
    poly_feature_names = model.named_steps["poly"].get_feature_names_out(RAW_FEATURE_NAMES).tolist()
    report = {
        "schema": "oq-universal-polynomial-ridge-elo-report-v1",
        "model": {
            "type": "single_universal_polynomial_ridge",
            "alpha": float(args.alpha),
            "sampleWeight": "log1p(moves_count)",
            "preprocessing": [
                "PolynomialFeatures(degree=2, include_bias=False)",
                "StandardScaler",
            ],
            "predictionRange": [float(args.minimum_elo), float(args.maximum_elo)],
        },
        "features": {
            "raw": RAW_FEATURE_NAMES,
            "rawCount": len(RAW_FEATURE_NAMES),
            "expanded": poly_feature_names,
            "expandedCount": len(poly_feature_names),
            "scope": "target-player Level22 nodes at or after postOffBookStartsAtPly",
        },
        "split": {
            "source": str(cases_path),
            "trainingRole": "calibration",
            "validationRole": "validation",
            "trainingAccounts": len(train_rows),
            "validationAccounts": len(validation_rows),
            "accountOverlapCount": 0,
        },
        "trainingMetricsClipped": metrics(y_train, train_predictions),
        "validationMetricsRaw": metrics(y_validation, raw_validation_predictions),
        "validationMetricsClipped": metrics(y_validation, validation_predictions),
        "validationMetricsClippedByEloBand": band_metrics(y_validation, validation_predictions),
        "inputs": {
            "directedRecords": str(directed_path),
            "directedRecordsSha256": sha256_file(directed_path),
            "calibrationCases": str(cases_path),
            "calibrationCasesSha256": sha256_file(cases_path),
            "sourceReferenceDirectory": str(source_dir),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output_dir / "universal_polynomial_ridge.joblib")
    write_csv(output_dir / "player_features.csv", feature_rows)
    write_csv(output_dir / "training_predictions.csv", train_output)
    write_csv(output_dir / "validation_predictions.csv", validation_output)
    write_json(output_dir / "report.json", report)
    write_json(
        output_dir / "model_contract.json",
        {
            "schema": "oq-universal-polynomial-ridge-elo-contract-v1",
            "rawFeaturesInOrder": RAW_FEATURE_NAMES,
            "postOffbookInclusive": True,
            "predictionRange": [float(args.minimum_elo), float(args.maximum_elo)],
            "modelFile": "universal_polynomial_ridge.joblib",
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
