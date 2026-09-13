#!/usr/bin/env python3
"""Match locally recomputed hint1 templates against a frozen handoff."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


RULES = (
    ("abs2_or_5pct", 2, 0.05),
    ("abs5_or_10pct", 5, 0.10),
    ("abs10_or_20pct", 10, 0.20),
    ("abs20_or_30pct", 20, 0.30),
)


def local_key(row: dict[str, str]) -> tuple[str, ...]:
    return (
        row["ply"], row["side_to_move"], row["legal_moves"],
        row["local_hint1_move"], row["local_hint1_score"], row["local_hint1_depth"],
    )


def server_key(row: dict[str, str]) -> tuple[str, ...]:
    return (
        row["ply"], row["side_to_move"], row["legal_moves"],
        row["hint1_move"], row["hint1_score"], row["hint1_depth"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--templates", required=True, type=Path)
    parser.add_argument("--source-raw", required=True, type=Path)
    parser.add_argument("--sample-limit", type=int, default=10000)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    samples: list[dict[str, str]] = []
    with args.templates.resolve().open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            samples.append(row)
            if args.sample_limit > 0 and len(samples) >= args.sample_limit:
                break
    if not samples:
        raise ValueError("template sample is empty")

    wanted = {local_key(row) for row in samples}
    candidates: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    placement_rows = 0
    with args.source_raw.resolve().open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["actual_move"] == "-":
                continue
            placement_rows += 1
            key = server_key(row)
            if key not in wanted:
                continue
            candidates[key].append({
                "game_id": row["game_id"],
                "move_index": int(row["move_index"]),
                "board_setboard": row["board_setboard"],
                "nodes": int(row["hint1_nodes"]),
            })

    reports: dict[str, Any] = {}
    for name, absolute, relative in RULES:
        no_candidate = unique_row = ambiguous_row = 0
        unique_board = ambiguous_board = correct_board = true_row_present = 0
        counts: Counter[int] = Counter()
        examples: list[dict[str, Any]] = []
        for sample in samples:
            local_nodes = int(sample["local_hint1_nodes"])
            tolerance = max(absolute, relative * max(local_nodes, 1))
            matched = [
                item for item in candidates.get(local_key(sample), [])
                if abs(item["nodes"] - local_nodes) <= tolerance
            ]
            counts[len(matched)] += 1
            if not matched:
                no_candidate += 1
                if len(examples) < 10:
                    examples.append({
                        "kind": "no-candidate", "gameId": sample["game_id"],
                        "moveIndex": int(sample["move_index"]), "localNodes": local_nodes,
                    })
                continue
            true_key = (sample["game_id"], int(sample["move_index"]))
            if any((item["game_id"], item["move_index"]) == true_key for item in matched):
                true_row_present += 1
            if len(matched) == 1:
                unique_row += 1
            else:
                ambiguous_row += 1
            boards = {item["board_setboard"] for item in matched}
            if len(boards) == 1:
                unique_board += 1
                if sample["board_setboard"] in boards:
                    correct_board += 1
            else:
                ambiguous_board += 1
                if len(examples) < 10:
                    examples.append({
                        "kind": "multiple-boards", "gameId": sample["game_id"],
                        "moveIndex": int(sample["move_index"]), "localNodes": local_nodes,
                        "candidateRows": len(matched), "candidateBoards": len(boards),
                    })
        reports[name] = {
            "absoluteTolerance": absolute,
            "relativeTolerance": relative,
            "noCandidate": no_candidate,
            "uniqueRow": unique_row,
            "ambiguousRow": ambiguous_row,
            "uniqueBoard": unique_board,
            "ambiguousBoard": ambiguous_board,
            "correctBoardWhenUnique": correct_board,
            "trueRowPresent": true_row_present,
            "uniqueBoardRate": unique_board / len(samples),
            "candidateCountDistribution": {str(key): value for key, value in sorted(counts.items())},
            "examples": examples,
        }

    report = {
        "schema": "oq-local-hint1-full-candidate-match-v1",
        "templates": str(args.templates.resolve()),
        "sourceRaw": str(args.source_raw.resolve()),
        "samples": len(samples),
        "candidatePlacementRows": placement_rows,
        "strictFields": [
            "ply", "side_to_move", "legal_moves", "hint1_move", "hint1_score", "hint1_depth"
        ],
        "excludedFromMatching": ["game_id", "board_setboard"],
        "rules": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
