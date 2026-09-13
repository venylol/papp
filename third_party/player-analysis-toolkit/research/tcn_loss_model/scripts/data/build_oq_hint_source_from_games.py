#!/usr/bin/env python3
"""Replay pulled OQ games into a pass-aware, hint-free engine source table."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

SOURCE_FIELDS = [
    "game_id", "mode", "gtype", "tcb", "created", "finalStatus", "move_index", "ply",
    "source_ply_including_pass", "global_placement_ply", "side_to_move", "player_id",
    "actual_move", "actual_thinking_time_ms", "board", "board_setboard", "n_legal_moves",
    "legal_moves", "is_pass_record", "split", "input_policy",
]
INPUT_POLICY = "uniform-no-current-player-loss-history-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_split(game_id: str) -> str:
    bucket = int(hashlib.sha256(game_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", required=True, type=Path)
    parser.add_argument("--moves", required=True, type=Path)
    parser.add_argument("--baseline-games", required=True, type=Path)
    parser.add_argument("--analyzer-module-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-games", type=int, default=1000)
    args = parser.parse_args()
    module_dir = args.analyzer_module_dir.resolve()
    sys.path.insert(0, str(module_dir))
    from analyze_oq_reversi_5min_hints import OthelloBoard

    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    output.mkdir(parents=True)
    games = {row["game_id"]: row for row in read_rows(args.games.resolve())}
    baseline_ids = {row["game_id"] for row in read_rows(args.baseline_games.resolve())}
    if len(games) != args.expected_games or set(games) & baseline_ids:
        raise ValueError(f"game cohort mismatch: games={len(games)} overlap={len(set(games) & baseline_ids)}")
    by_game: dict[str, dict[int, dict[str, str]]] = defaultdict(dict)
    for row in read_rows(args.moves.resolve()):
        game_id = row["game_id"]
        if game_id not in games:
            raise ValueError(f"move references unknown game: {game_id}")
        index = int(row["move_index"])
        if index in by_game[game_id]:
            raise ValueError(f"duplicate move key: {(game_id, index)}")
        by_game[game_id][index] = row

    raw_path = output / "raw_nodes_with_pass.csv"
    context_path = output / "context_metadata.csv"
    split_path = output / "split_manifest.csv"
    rows = placements = passes = 0
    max_source_ply = max_placement_ply = 0
    split_counts: dict[str, int] = defaultdict(int)
    with raw_path.open("w", encoding="utf-8", newline="") as raw_handle, context_path.open(
        "w", encoding="utf-8", newline=""
    ) as context_handle:
        writer = csv.DictWriter(raw_handle, fieldnames=SOURCE_FIELDS)
        writer.writeheader()
        context_fields = ["game_id", "ply", "move_index", "split", "input_policy", "label_quality"]
        context_writer = csv.DictWriter(context_handle, fieldnames=context_fields)
        context_writer.writeheader()
        for game_id in sorted(games):
            game = games[game_id]
            length = int(game["length"])
            moves = by_game.get(game_id, {})
            if set(moves) != set(range(length)):
                missing = sorted(set(range(length)) - set(moves))[:10]
                extra = sorted(set(moves) - set(range(length)))[:10]
                raise ValueError(f"non-contiguous original OQ index for {game_id}: missing={missing} extra={extra}")
            board = OthelloBoard()
            placement_ply = 0
            split = stable_split(game_id)
            split_counts[split] += 1
            for move_index in range(length):
                move = moves[move_index]
                actual = move["move"].strip().lower()
                setboard = board.to_setboard_str()
                side = "black" if board.current == "X" else "white"
                if move["color_to_move"].strip().lower() != side:
                    raise ValueError(f"side mismatch at {(game_id, move_index)}")
                legal = board.legal_moves()
                is_pass = actual == "-"
                if is_pass:
                    if legal:
                        raise ValueError(f"pass with legal move at {(game_id, move_index)}")
                    passes += 1
                else:
                    if actual not in legal:
                        raise ValueError(f"illegal transcript move at {(game_id, move_index)}: {actual} not in {legal}")
                    placement_ply += 1
                    placements += 1
                source_ply = move_index + 1
                max_source_ply = max(max_source_ply, source_ply)
                max_placement_ply = max(max_placement_ply, placement_ply)
                row = {
                    "game_id": game_id, "mode": "reversi_5min", "gtype": "reversi", "tcb": 300000,
                    "created": game["created"], "finalStatus": game["finalStatus"],
                    "move_index": move_index, "ply": source_ply,
                    "source_ply_including_pass": source_ply, "global_placement_ply": placement_ply,
                    "side_to_move": side, "player_id": move["player_id"].strip().lower(),
                    "actual_move": actual, "actual_thinking_time_ms": int(move["thinking_time_ms"] or 0),
                    "board": setboard[:64], "board_setboard": setboard,
                    "n_legal_moves": len(legal), "legal_moves": " ".join(legal),
                    "is_pass_record": int(is_pass), "split": split, "input_policy": INPUT_POLICY,
                }
                writer.writerow(row)
                context_writer.writerow({
                    "game_id": game_id, "ply": source_ply, "move_index": move_index, "split": split,
                    "input_policy": INPUT_POLICY, "label_quality": "extension-source-no-hints-v1",
                })
                rows += 1
                board.apply_move(actual)
        raw_handle.flush(); os.fsync(raw_handle.fileno())
        context_handle.flush(); os.fsync(context_handle.fileno())

    with split_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["game_id", "split"])
        writer.writeheader()
        for game_id in sorted(games):
            writer.writerow({"game_id": game_id, "split": stable_split(game_id)})
    shutil.copy2(args.games.resolve(), output / "games.csv")
    shape = {
        "games": len(games), "rows": rows, "placements": placements, "passes": passes,
        "maxSourcePlyIncludingPass": max_source_ply, "maxGlobalPlacementPly": max_placement_ply,
        "splits": dict(split_counts), "baselineOverlap": 0,
    }
    if rows != placements + passes or max_placement_ply > 60:
        raise ValueError(f"pass-aware shape failed: {shape}")
    files = []
    for path in sorted(item for item in output.iterdir() if item.is_file()):
        files.append({"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "schema": "oq-bilateral-extension-hint-source-v1", "ok": True,
        "createdAtUnix": time.time(), "shape": shape,
        "indexContract": {
            "key": "exact (game_id, move_index)",
            "moveIndex": "original OQ index; explicit '-' pass consumes an index",
            "sourcePlyIncludingPass": "move_index + 1",
            "globalPlacementPly": "increments only on actual placements",
        },
        "files": files,
    }
    (output / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
