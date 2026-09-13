#!/usr/bin/env python3
"""Validate a frozen OQ bilateral hint/label handoff without training."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BOARD_RE = re.compile(r"^[XO-]{64}$")
INPUT_POLICY = "uniform-no-current-player-loss-history-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def severity(loss: int) -> int:
    if loss == 0:
        return 0
    if loss <= 3:
        return 1
    if loss <= 9:
        return 2
    return 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))

    errors: list[str] = []
    for name, expected in manifest["files"].items():
        path = data_dir / name
        if not path.exists():
            errors.append(f"missing file: {name}")
            continue
        actual = sha256_file(path)
        if actual != expected["sha256"]:
            errors.append(f"sha256 mismatch: {name}")

    raw_keys: set[tuple[str, int]] = set()
    raw_game_indices: dict[str, list[int]] = defaultdict(list)
    raw_splits: dict[str, set[str]] = defaultdict(set)
    raw_rows = pass_rows = 0
    with (data_dir / "raw_nodes_with_pass.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_rows += 1
            game_id = row["game_id"]
            move_index = int(row["move_index"])
            key = (game_id, move_index)
            if key in raw_keys:
                errors.append(f"duplicate raw key: {key}")
            raw_keys.add(key)
            raw_game_indices[game_id].append(move_index)
            raw_splits[game_id].add(row["split"])
            if row["tcb"] != "300000":
                errors.append(f"non-five-minute raw row: {key}")
            if not BOARD_RE.fullmatch(row["board"]):
                errors.append(f"invalid board: {key}")
            if row["input_policy"] != INPUT_POLICY:
                errors.append(f"bad input policy: {key}")
            if row["actual_move"] == "-":
                pass_rows += 1
            elif not row["hint6_1_score"]:
                errors.append(f"missing placement hint6_1_score: {key}")
    for game_id, indices in raw_game_indices.items():
        ordered = sorted(indices)
        if ordered != list(range(len(ordered))):
            errors.append(f"nonconsecutive source rows: {game_id}")
        if len(raw_splits[game_id]) != 1:
            errors.append(f"split leakage: {game_id}")

    decision_keys: set[tuple[str, int]] = set()
    decisions_by_game: Counter[str] = Counter()
    labelled_by_game: Counter[str] = Counter()
    decision_nodes = labelled_nodes = negative_raw_loss = formula_errors = 0
    with (data_dir / "decision_labels.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            decision_nodes += 1
            game_id = row["game_id"]
            move_index = int(row["move_index"])
            key = (game_id, move_index)
            decision_keys.add(key)
            decisions_by_game[game_id] += 1
            if row["actual_move"] == "-":
                errors.append(f"pass included as decision: {key}")
            available = row["label_available"] == "1"
            if not available:
                continue
            labelled_nodes += 1
            labelled_by_game[game_id] += 1
            current_best = int(row["hint6_1_score"])
            next_best = int(row["next_best_score"])
            same_side = row["same_side_after_move"] == "1"
            expected_raw = current_best - next_best if same_side else current_best + next_best
            expected_loss = max(0, expected_raw)
            checks = (
                int(row["raw_loss"]) == expected_raw,
                int(row["disc_loss"]) == expected_loss,
                int(row["severity_class"]) == severity(expected_loss),
                int(row["label_zero"]) == int(expected_loss == 0),
                int(row["label_ge4"]) == int(expected_loss >= 4),
                int(row["label_ge10"]) == int(expected_loss >= 10),
            )
            if not all(checks):
                formula_errors += 1
            if expected_raw < 0:
                negative_raw_loss += 1
    if formula_errors:
        errors.append(f"label formula errors: {formula_errors}")
    for game_id, count in decisions_by_game.items():
        if labelled_by_game[game_id] != count - 1:
            errors.append(f"expected one childless terminal decision: {game_id}")

    context_keys: set[tuple[str, int]] = set()
    with (data_dir / "context_metadata.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            context_keys.add((row["game_id"], int(row["move_index"])))
    if context_keys != raw_keys:
        errors.append("context metadata keys differ from raw node keys")

    split_counts: Counter[str] = Counter()
    split_games: set[str] = set()
    with (data_dir / "split_manifest.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["game_id"] in split_games:
                errors.append(f"duplicate split game: {row['game_id']}")
            split_games.add(row["game_id"])
            split_counts[row["split"]] += 1
    if split_games != set(raw_game_indices):
        errors.append("split manifest game ids differ from raw game ids")

    report: dict[str, Any] = {
        "schema": "oq-bilateral-cross-pass-validation-v1",
        "ok": not errors,
        "data_dir": str(data_dir.resolve()),
        "games": len(raw_game_indices),
        "players": manifest["counts"]["players"],
        "raw_rows": raw_rows,
        "pass_rows": pass_rows,
        "decision_nodes": decision_nodes,
        "labelled_nodes": labelled_nodes,
        "expected_labelled_nodes": decision_nodes - len(raw_game_indices),
        "label_coverage": labelled_nodes / decision_nodes,
        "negative_raw_loss_nodes": negative_raw_loss,
        "formula_errors": formula_errors,
        "splits": dict(split_counts),
        "input_policy": INPUT_POLICY,
        "errors": errors[:100],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
