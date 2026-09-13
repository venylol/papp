#!/usr/bin/env python3
"""Audit a pass-aware OQ extension source against its pull and baseline cohort."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--pull-dir", required=True, type=Path)
    parser.add_argument("--baseline-games", required=True, type=Path)
    parser.add_argument("--expected-games", required=True, type=int)
    parser.add_argument("--expected-rows", required=True, type=int)
    parser.add_argument("--expected-placements", required=True, type=int)
    parser.add_argument("--expected-passes", required=True, type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    pull_dir = args.pull_dir.resolve()
    manifest = json.loads((source_dir / "source_manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("ok"):
        raise ValueError("source manifest is not passing")
    for item in manifest["files"]:
        path = source_dir / item["path"]
        if not path.is_file() or path.stat().st_size != int(item["bytes"]) or sha256_file(path) != item["sha256"]:
            raise ValueError(f"source manifest identity mismatch: {item['path']}")

    games = read_rows(pull_dir / "games.csv")
    pulls = read_rows(pull_dir / "move_times.csv")
    summaries = read_rows(pull_dir / "game_player_summaries.csv")
    source = read_rows(source_dir / "raw_nodes_with_pass.csv")
    baseline_ids = {row["game_id"] for row in read_rows(args.baseline_games.resolve())}
    game_ids = [row["game_id"] for row in games]
    if len(game_ids) != args.expected_games or len(set(game_ids)) != args.expected_games:
        raise ValueError("extension games are missing or duplicated")
    if set(game_ids) & baseline_ids:
        raise ValueError("extension overlaps the frozen baseline cohort")

    pull_by_key = {(row["game_id"], int(row["move_index"])): row for row in pulls}
    source_by_key = {(row["game_id"], int(row["move_index"])): row for row in source}
    if len(pull_by_key) != len(pulls) or len(source_by_key) != len(source) or set(pull_by_key) != set(source_by_key):
        raise ValueError("pull/source exact (game_id, move_index) key sets differ")
    by_game: dict[str, list[int]] = defaultdict(list)
    placements = passes = 0
    for key, row in source_by_key.items():
        original = pull_by_key[key]
        if (
            row["actual_move"].lower() != original["move"].lower()
            or row["side_to_move"].lower() != original["color_to_move"].lower()
            or row["player_id"].lower() != original["player_id"].lower()
            or int(row["source_ply_including_pass"]) != key[1] + 1
        ):
            raise ValueError(f"pull/source identity mismatch: {key}")
        is_pass = row["actual_move"] == "-"
        if int(row["is_pass_record"]) != int(is_pass):
            raise ValueError(f"pass flag mismatch: {key}")
        passes += int(is_pass)
        placements += int(not is_pass)
        by_game[key[0]].append(key[1])
    for game_id, indexes in by_game.items():
        if sorted(indexes) != list(range(len(indexes))):
            raise ValueError(f"non-contiguous original OQ indices: {game_id}")
    summary_counts: dict[str, int] = defaultdict(int)
    for row in summaries:
        summary_counts[row["game_id"]] += 1
    if any(summary_counts[game_id] != 2 for game_id in game_ids):
        raise ValueError("each extension game must have exactly two player summaries")

    shape = (len(source), placements, passes, len(game_ids))
    expected = (args.expected_rows, args.expected_placements, args.expected_passes, args.expected_games)
    if shape != expected or max(int(row["global_placement_ply"]) for row in source) > 60:
        raise ValueError(f"pass-aware shape mismatch: actual={shape} expected={expected}")
    report: dict[str, Any] = {
        "schema": "oq-extension-source-audit-v1", "ok": True,
        "games": len(game_ids), "rows": len(source), "placements": placements, "passes": passes,
        "summaries": len(summaries), "baselineOverlap": 0,
        "maxSourcePlyIncludingPass": max(int(row["source_ply_including_pass"]) for row in source),
        "maxGlobalPlacementPly": max(int(row["global_placement_ply"]) for row in source),
        "indexContract": "exact (game_id, original pass-inclusive move_index)",
    }
    body = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body + "\n", encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
