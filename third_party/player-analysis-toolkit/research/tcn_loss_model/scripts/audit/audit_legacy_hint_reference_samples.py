#!/usr/bin/env python3
"""Compare exact-index legacy hints with current safe hint1/hint6 evidence."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any, Iterator


def iter_records(stage_dir: Path) -> Iterator[dict[str, Any]]:
    for path in sorted((stage_dir / "batches").glob("batch_*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                yield json.loads(line)


def key_of(row: dict[str, Any]) -> tuple[str, int]:
    return str(row["game_id"]), int(row["move_index"])


def old_hint1(row: dict[str, str]) -> tuple[str, int]:
    return row["hint1_move"].strip().lower(), int(float(row["hint1_score"]))


def hints_signature(hints: list[dict[str, Any]]) -> tuple[tuple[str, ...], dict[str, int]]:
    moves = tuple(str(item["move"]).lower() for item in hints)
    scores = {str(item["move"]).lower(): int(float(item["score"])) for item in hints}
    return moves, scores


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--legacy", required=True, action="append")
    parser.add_argument("--safe-hint1", required=True, type=Path)
    parser.add_argument("--safe-hint6", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--hint1-sample", type=int, default=10000)
    parser.add_argument("--hint6-sample", type=int, default=400)
    parser.add_argument("--seed-value", type=int, default=20260804)
    parser.add_argument("--score-tolerance", type=int, default=1)
    args = parser.parse_args()

    legacy_paths = {}
    for value in args.legacy:
        label, raw = value.split("=", 1)
        legacy_paths[label] = Path(raw).resolve()

    seed_keys = []
    seed_origins = {}
    with args.seed.resolve().open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            key = key_of(row)
            seed_keys.append(key)
            seed_origins[key] = row["legacySourceLabel"]
    rng = random.Random(args.seed_value)
    hint1_keys = set(rng.sample(seed_keys, args.hint1_sample))

    safe_hint6_by_key = {}
    overlap_keys = []
    seed_key_set = set(seed_keys)
    for record in iter_records(args.safe_hint6.resolve()):
        key = key_of(record)
        if key in seed_key_set:
            overlap_keys.append(key)
            safe_hint6_by_key[key] = hints_signature(record["hints"])
    if len(overlap_keys) < args.hint6_sample:
        raise ValueError(f"only {len(overlap_keys)} safe hint6 overlaps, need {args.hint6_sample}")
    hint6_keys = set(rng.sample(overlap_keys, args.hint6_sample))
    safe_hint6_by_key = {key: safe_hint6_by_key[key] for key in hint6_keys}

    legacy_hint6 = {}
    with args.seed.resolve().open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            key = key_of(row)
            if key in hint6_keys:
                legacy_hint6[key] = hints_signature(row["hints"])

    safe_hint1 = {}
    for record in iter_records(args.safe_hint1.resolve()):
        key = key_of(record)
        if key in hint1_keys:
            item = record["hints"][0]
            safe_hint1[key] = (str(item["move"]).lower(), int(float(item["score"])))
    if len(safe_hint1) != args.hint1_sample:
        raise ValueError(f"safe hint1 sample incomplete: {len(safe_hint1)}")

    legacy_hint1 = {}
    for label, path in legacy_paths.items():
        wanted = {key for key in hint1_keys if seed_origins[key] == label}
        if not wanted:
            continue
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                key = key_of(row)
                if key in wanted and row.get("actual_move") != "-":
                    legacy_hint1[key] = old_hint1(row)
    if len(legacy_hint1) != args.hint1_sample:
        raise ValueError(f"legacy hint1 sample incomplete: {len(legacy_hint1)}")

    h1 = Counter()
    h1_examples = []
    for key in sorted(hint1_keys):
        old = legacy_hint1[key]
        new = safe_hint1[key]
        h1["checked"] += 1
        if old[0] == new[0]:
            h1["moveSame"] += 1
        if old == new:
            h1["moveAndScoreSame"] += 1
        if old != new and len(h1_examples) < 20:
            h1_examples.append({"game_id": key[0], "move_index": key[1], "legacy": old, "safe": new})

    h6 = Counter()
    score_differences = Counter()
    h6_examples = []
    for key in sorted(hint6_keys):
        old_moves, old_scores = legacy_hint6[key]
        new_moves, new_scores = safe_hint6_by_key[key]
        h6["checked"] += 1
        if old_moves[0] == new_moves[0]:
            h6["top1MoveSame"] += 1
        if old_moves == new_moves:
            h6["orderedMovesSame"] += 1
        if set(old_moves) == set(new_moves):
            h6["unorderedMovesSame"] += 1
        common = set(old_scores) & set(new_scores)
        if common:
            differences = [abs(old_scores[move] - new_scores[move]) for move in common]
            h6["commonMoveScoresCompared"] += len(differences)
            h6["commonMoveScoresWithinTolerance"] += sum(
                difference <= args.score_tolerance for difference in differences
            )
            for difference in differences:
                score_differences[str(difference)] += 1
        if (old_moves[0] != new_moves[0] or set(old_moves) != set(new_moves)) and len(h6_examples) < 20:
            h6_examples.append({
                "game_id": key[0], "move_index": key[1],
                "legacyMoves": old_moves, "safeMoves": new_moves,
            })

    hint1_manifest = json.loads((args.safe_hint1.resolve() / "run_manifest.json").read_text(encoding="utf-8"))
    hint6_manifest = json.loads((args.safe_hint6.resolve() / "run_manifest.json").read_text(encoding="utf-8"))
    acceptance = {
        "hint1MoveAndScoreSameMinimum": 1.0,
        "hint6Top1MoveSameMinimum": 0.90,
        "hint6UnorderedMovesSameMinimum": 0.80,
        "hint6CommonMoveScoresWithinToleranceMinimum": 0.90,
    }
    rates = {
        "hint1MoveAndScoreSame": h1["moveAndScoreSame"] / h1["checked"],
        "hint6Top1MoveSame": h6["top1MoveSame"] / h6["checked"],
        "hint6UnorderedMovesSame": h6["unorderedMovesSame"] / h6["checked"],
        "hint6CommonMoveScoresWithinTolerance": (
            h6["commonMoveScoresWithinTolerance"] / h6["commonMoveScoresCompared"]
        ),
    }
    ok = (
        rates["hint1MoveAndScoreSame"] >= acceptance["hint1MoveAndScoreSameMinimum"]
        and rates["hint6Top1MoveSame"] >= acceptance["hint6Top1MoveSameMinimum"]
        and rates["hint6UnorderedMovesSame"] >= acceptance["hint6UnorderedMovesSameMinimum"]
        and rates["hint6CommonMoveScoresWithinTolerance"]
        >= acceptance["hint6CommonMoveScoresWithinToleranceMinimum"]
    )
    report = {
        "schema": "oq-legacy-current-safe-reference-sample-audit-v1",
        "ok": ok,
        "samplingSeed": args.seed_value,
        "scoreTolerance": args.score_tolerance,
        "acceptance": acceptance,
        "rates": rates,
        "hint1Config": {name: hint1_manifest[name] for name in ("level", "threads", "use_book", "count", "hashLevel")},
        "hint6Config": {name: hint6_manifest[name] for name in ("level", "threads", "use_book", "count", "hashLevel")},
        "hint1": {**dict(h1), "examples": h1_examples},
        "hint6": {
            **dict(h6),
            "safeOverlapAvailable": len(overlap_keys),
            "absoluteScoreDifferenceCounts": dict(sorted(score_differences.items(), key=lambda item: int(item[0]))),
            "examples": h6_examples,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
