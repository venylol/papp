#!/usr/bin/env python3
"""Freeze personal-game source rows for safe hint recomputation, without reusing old hints."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.checkpoint import sha256_file
from scripts.materialize_personal_oq_tcn_model_ready import normalize_bundle
from scripts.safe_recompute_egaroucid_hints import task_from_row


def engine_fields() -> list[str]:
    fields = [
        "hint1_level", "hint1_move", "hint1_score", "hint1_nodes", "hint1_depth", "hint1_is_book",
    ]
    for rank in range(1, 7):
        fields.extend(f"hint6_{rank}_{name}" for name in ("move", "score", "nodes", "depth", "is_book"))
    return fields


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
    os.replace(temporary, path)


def normalized_move_index(bundle: dict[str, Any]) -> dict[tuple[str, int], tuple[str, int]]:
    result: dict[tuple[str, int], tuple[str, int]] = {}
    for detail in bundle["details"]:
        game_id = str(detail["id"])
        moves = [move for move in (detail.get("position") or {}).get("moves", []) if "m" in move]
        for move_index, move in enumerate(moves):
            result[(game_id, move_index)] = (
                str(move["m"]).strip().lower(),
                int(move.get("t", 0) or 0),
            )
    return result


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    source_hash = sha256_file(args.source_raw)
    bundle_hash = sha256_file(args.account_bundle)
    if args.expected_bundle_sha256 and bundle_hash != args.expected_bundle_sha256.lower():
        raise ValueError("account bundle hash differs from the frozen expected hash")
    normalized, normalization = normalize_bundle(
        json.loads(args.account_bundle.read_text(encoding="utf-8")), args.effective_time_limit_ms
    )
    expected_moves = normalized_move_index(normalized)
    frame = pd.read_csv(args.source_raw, encoding="utf-8", low_memory=False, dtype={"game_id": str})
    required = {
        "game_id", "move_index", "board", "side_to_move", "actual_move",
        "actual_thinking_time_ms", "n_legal_moves", "legal_moves",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"personal source is missing required columns: {missing}")
    if frame.duplicated(["game_id", "move_index"]).any():
        raise ValueError("personal source contains duplicate game_id/move_index keys")
    frame["game_id"] = frame["game_id"].astype(str)
    frame["move_index"] = frame["move_index"].astype(int)
    source_keys = set(zip(frame["game_id"], frame["move_index"], strict=True))
    if source_keys != set(expected_moves):
        raise ValueError(
            f"personal source keys differ from normalized bundle: "
            f"missing={len(set(expected_moves) - source_keys)} extra={len(source_keys - set(expected_moves))}"
        )
    side = frame["side_to_move"].astype(str).str.strip().str.lower().map({"black": "X", "white": "O"})
    if side.isna().any():
        raise ValueError("personal source contains an invalid side_to_move")
    source_board = frame["board"].astype(str).str.strip().str.upper()
    lengths = set(source_board.str.len().unique())
    if lengths == {65}:
        if not source_board.str[-1].reset_index(drop=True).equals(side.reset_index(drop=True)):
            raise ValueError("65-character personal board side disagrees with side_to_move")
        frame["board"] = source_board.str[:64]
        frame["board_setboard"] = source_board
    elif lengths == {64}:
        frame["board"] = source_board
        frame["board_setboard"] = source_board + side
    else:
        raise ValueError(f"personal board must be uniformly 64 or 65 characters, got lengths {sorted(lengths)}")
    frame["is_pass_record"] = (frame["actual_move"].astype(str).str.strip() == "-").astype(int)
    frame["source_ply_including_pass"] = pd.to_numeric(frame["ply"], errors="raise").astype(int)
    frame["global_placement_ply"] = (
        (frame["is_pass_record"] == 0).astype(int).groupby(frame["game_id"], sort=False).cumsum().astype(int)
    )
    for field in engine_fields():
        if field not in frame.columns:
            frame[field] = ""
        else:
            frame[field] = ""
    if "analyzed_at" in frame.columns:
        frame["analyzed_at"] = ""
    placements = passes = 0
    for row in frame.to_dict(orient="records"):
        key = (str(row["game_id"]), int(row["move_index"]))
        expected_move, expected_time = expected_moves[key]
        actual_move = str(row["actual_move"]).strip().lower()
        if actual_move != expected_move:
            raise ValueError(f"source move differs from normalized account bundle for {key}")
        if int(row["actual_thinking_time_ms"]) != expected_time:
            raise ValueError(f"effective thinking time differs from normalized account bundle for {key}")
        if actual_move == "-":
            passes += 1
            continue
        if task_from_row({name: "" if pd.isna(value) else str(value) for name, value in row.items()}) is None:
            raise AssertionError(f"placement unexpectedly parsed as pass for {key}")
        placements += 1
    games = int(frame["game_id"].nunique())
    if (len(frame), placements, passes, games) != (
        args.expected_rows, args.expected_placements, args.expected_passes, args.expected_games
    ):
        raise ValueError(
            f"personal frozen shape mismatch: got {(len(frame), placements, passes, games)} "
            f"expected {(args.expected_rows, args.expected_placements, args.expected_passes, args.expected_games)}"
        )
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    if args.output_csv.exists() or args.output_manifest.exists():
        raise FileExistsError("refusing to overwrite an existing personal safe source or manifest")
    temporary = args.output_csv.with_name(args.output_csv.name + f".{os.getpid()}.tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8", lineterminator="\n")
    os.replace(temporary, args.output_csv)
    manifest = {
        "schema": "personal-safe-hint-source-v1", "ok": True,
        "sourceRaw": str(args.source_raw.resolve()), "sourceRawSha256": source_hash,
        "accountBundle": str(args.account_bundle.resolve()), "accountBundleSha256": bundle_hash,
        "effectiveTimeLimitMs": args.effective_time_limit_ms,
        "normalizationPolicy": normalization["policy"],
        "rows": len(frame), "placements": placements, "passes": passes, "games": games,
        "oldHintValuesCleared": True,
        "outputCsv": str(args.output_csv.resolve()), "outputSha256": sha256_file(args.output_csv),
    }
    atomic_write_json(args.output_manifest, manifest)
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source-raw", required=True, type=Path)
    result.add_argument("--account-bundle", required=True, type=Path)
    result.add_argument("--expected-bundle-sha256", default="")
    result.add_argument("--effective-time-limit-ms", type=int, default=300000)
    result.add_argument("--output-csv", required=True, type=Path)
    result.add_argument("--output-manifest", required=True, type=Path)
    result.add_argument("--expected-rows", type=int, default=1700)
    result.add_argument("--expected-placements", type=int, default=1669)
    result.add_argument("--expected-passes", type=int, default=31)
    result.add_argument("--expected-games", type=int, default=30)
    return result


def main() -> int:
    report = prepare(parser().parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
