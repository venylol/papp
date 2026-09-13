#!/usr/bin/env python3
"""Safely recompute hint6 for a fixed sample of uniquely fingerprinted boards.

The older investigation CSV's ``board_setboard`` is the request board for the local
hint6 transaction; because that run used Console output-suppression flags, it is not
native response-board evidence and is not sufficient for formal acceptance.  Future
formal runs must parse and retain the native Console board from every hint6 response.
Keep the sample manifest, CSV, report, and engine log until the final complete
model-ready dataset passes acceptance; this script never cleans them up.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from types import ModuleType
from typing import Any


RANK_FIELDS = ("move", "score", "nodes", "depth", "is_book")


def load_analyzer(path: Path) -> ModuleType:
    name = "oq_hint6_sample_engine"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import analyzer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def template_key(row: dict[str, str]) -> tuple[str, ...]:
    return (
        row["ply"], row["side_to_move"], row["legal_moves"],
        row["local_hint1_move"], row["local_hint1_score"], row["local_hint1_depth"],
    )


def source_key(row: dict[str, str]) -> tuple[str, ...]:
    return (
        row["ply"], row["side_to_move"], row["legal_moves"],
        row["hint1_move"], row["hint1_score"], row["hint1_depth"],
    )


def row_key(row: dict[str, Any]) -> tuple[str, int]:
    return str(row["game_id"]), int(row["move_index"])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_server_hints(row: dict[str, str], count: int) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for rank in range(1, count + 1):
        item: dict[str, Any] = {}
        for field in RANK_FIELDS:
            text = row[f"hint6_{rank}_{field}"].strip()
            if field in {"score", "nodes"}:
                item[field] = int(text) if text else None
            elif field == "is_book":
                item[field] = text.lower() in {"1", "true", "yes"}
            else:
                item[field] = text.lower() if field == "move" else text
        hints.append(item)
    return hints


def normalize_local_hints(hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "move": str(item.get("move", "")).lower(),
            "score": item.get("score"),
            "nodes": item.get("nodes"),
            "depth": str(item.get("depth", "")),
            "is_book": bool(item.get("is_book", False)),
        }
        for item in hints
    ]


def compare_hints(server: list[dict[str, Any]], local: list[dict[str, Any]]) -> dict[str, int]:
    server_moves = [item["move"] for item in server]
    local_moves = [item["move"] for item in local]
    server_move_scores = [(item["move"], item["score"]) for item in server]
    local_move_scores = [(item["move"], item["score"]) for item in local]
    server_by_move = {item["move"]: item["score"] for item in server}
    local_by_move = {item["move"]: item["score"] for item in local}
    strict_no_nodes = [
        (item["move"], item["score"], item["depth"], item["is_book"]) for item in server
    ] == [
        (item["move"], item["score"], item["depth"], item["is_book"]) for item in local
    ]
    strict_with_nodes = server == local
    return {
        "top1_move_match": int(bool(server and local) and server[0]["move"] == local[0]["move"]),
        "top1_move_score_match": int(
            bool(server and local)
            and (server[0]["move"], server[0]["score"]) == (local[0]["move"], local[0]["score"])
        ),
        "ordered_moves_match": int(server_moves == local_moves),
        "ordered_move_scores_match": int(server_move_scores == local_move_scores),
        "unordered_moves_match": int(set(server_moves) == set(local_moves)),
        "scores_by_move_match": int(server_by_move == local_by_move),
        "strict_no_nodes_match": int(strict_no_nodes),
        "strict_with_nodes_match": int(strict_with_nodes),
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--templates", required=True, type=Path)
    parser.add_argument("--source-raw", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--analyzer-module", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--template-limit", type=int, default=10000)
    parser.add_argument("--sample-size", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20260804)
    parser.add_argument("--absolute-node-tolerance", type=int, default=10)
    parser.add_argument("--relative-node-tolerance", type=float, default=0.20)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--hash-level", type=int, default=25)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()

    templates_path = args.templates.resolve()
    source_path = args.source_raw.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_path = output_dir / "sample_manifest.json"
    results_path = output_dir / "hint6_sample_results.csv"
    report_path = output_dir / "report.json"

    templates: list[dict[str, str]] = []
    with templates_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            templates.append(row)
            if args.template_limit > 0 and len(templates) >= args.template_limit:
                break
    if not templates:
        raise ValueError("template sample is empty")

    wanted_keys = {template_key(row) for row in templates}
    candidates: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    source_rows: dict[tuple[str, int], dict[str, str]] = {}
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["actual_move"] == "-":
                continue
            source_rows[row_key(row)] = row
            key = source_key(row)
            if key in wanted_keys:
                candidates[key].append({
                    "board_setboard": row["board_setboard"],
                    "nodes": int(row["hint1_nodes"]),
                })

    unique_templates: list[dict[str, str]] = []
    for template in templates:
        local_nodes = int(template["local_hint1_nodes"])
        tolerance = max(
            int(args.absolute_node_tolerance),
            float(args.relative_node_tolerance) * max(local_nodes, 1),
        )
        matched_boards = {
            item["board_setboard"]
            for item in candidates.get(template_key(template), [])
            if abs(item["nodes"] - local_nodes) <= tolerance
        }
        if len(matched_boards) == 1 and template["board_setboard"] in matched_boards:
            unique_templates.append(template)
    unique_templates.sort(key=row_key)
    if len(unique_templates) < args.sample_size:
        raise ValueError(
            f"only {len(unique_templates)} uniquely fingerprinted boards; requested {args.sample_size}"
        )

    rng = random.Random(args.seed)
    selected = sorted(rng.sample(unique_templates, args.sample_size), key=row_key)
    sample_payload = {
        "schema": "oq-unique-board-hint6-sample-v1",
        "seed": args.seed,
        "templateLimit": args.template_limit,
        "uniqueBoardPool": len(unique_templates),
        "sampleSize": args.sample_size,
        "fingerprintNodeTolerance": {
            "absolute": args.absolute_node_tolerance,
            "relative": args.relative_node_tolerance,
        },
        "samples": [
            {
                "game_id": row["game_id"],
                "move_index": int(row["move_index"]),
                "ply": int(row["ply"]),
                "side_to_move": row["side_to_move"],
                "board_setboard": row["board_setboard"],
            }
            for row in selected
        ],
    }
    if sample_path.exists():
        existing = json.loads(sample_path.read_text(encoding="utf-8"))
        if existing != sample_payload:
            raise RuntimeError(f"existing sample manifest differs: {sample_path}")
    else:
        write_json(sample_path, sample_payload)

    fields = [
        "game_id", "move_index", "ply", "side_to_move", "board_setboard",
        "hint6_request_board_setboard", "hint6_board_setboard", "legal_moves",
        "expected_hint_count", "server_all_legal", "local_all_legal", "elapsed_seconds",
        "top1_move_match", "top1_move_score_match", "ordered_moves_match",
        "ordered_move_scores_match", "unordered_moves_match", "scores_by_move_match",
        "strict_no_nodes_match", "strict_with_nodes_match", "server_hints_json", "local_hints_json",
    ]
    completed: set[tuple[str, int]] = set()
    if results_path.exists():
        with results_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != fields:
                raise RuntimeError(
                    "existing hint6 sample CSV predates native Console-board provenance; "
                    "use a new output directory instead of appending"
                )
            completed = {row_key(row) for row in reader}
    analyzer = load_analyzer(args.analyzer_module.resolve())
    engine = None
    started = time.time()
    new_file = not results_path.exists()
    try:
        engine = analyzer.PersistentEngine(
            args.engine.resolve(), 18, int(args.threads), int(args.hash_level),
            output_dir / "egaroucid_level18_book.log", True,
        )
        with results_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if new_file:
                writer.writeheader()
            for index, template in enumerate(selected, start=1):
                key = row_key(template)
                if key in completed:
                    continue
                source = source_rows[key]
                legal_moves = source["legal_moves"].split()
                expected_count = min(6, len(legal_moves))
                server_hints = expected_server_hints(source, expected_count)
                position_started = time.time()
                raw_hints, console_board = engine.hint_for_board(
                    template["board_setboard"], expected_count, timeout=float(args.timeout)
                )
                local_hints = normalize_local_hints(raw_hints)
                elapsed = time.time() - position_started
                if console_board != template["board_setboard"]:
                    raise RuntimeError(
                        f"hint6 Console board mismatch for {key}: "
                        f"request={template['board_setboard']} console={console_board}"
                    )
                if len(local_hints) != expected_count:
                    raise RuntimeError(
                        f"expected {expected_count} hints for {key}, got {len(local_hints)}"
                    )
                comparison = compare_hints(server_hints, local_hints)
                writer.writerow({
                    "game_id": key[0],
                    "move_index": key[1],
                    "ply": source["ply"],
                    "side_to_move": source["side_to_move"],
                    "board_setboard": source["board_setboard"],
                    "hint6_request_board_setboard": template["board_setboard"],
                    "hint6_board_setboard": console_board,
                    "legal_moves": source["legal_moves"],
                    "expected_hint_count": expected_count,
                    "server_all_legal": int(all(item["move"] in legal_moves for item in server_hints)),
                    "local_all_legal": int(all(item["move"] in legal_moves for item in local_hints)),
                    "elapsed_seconds": f"{elapsed:.6f}",
                    **comparison,
                    "server_hints_json": json.dumps(server_hints, separators=(",", ":")),
                    "local_hints_json": json.dumps(local_hints, separators=(",", ":")),
                })
                handle.flush()
                print(
                    json.dumps({
                        "completed": index,
                        "total": len(selected),
                        "game_id": key[0],
                        "move_index": key[1],
                        "elapsedSeconds": round(elapsed, 3),
                        **comparison,
                    }, ensure_ascii=False),
                    flush=True,
                )
    finally:
        if engine is not None:
            engine.close()

    result_rows: list[dict[str, str]] = []
    with results_path.open("r", encoding="utf-8", newline="") as handle:
        result_rows = list(csv.DictReader(handle))
    metrics = [
        "server_all_legal", "local_all_legal", "top1_move_match", "top1_move_score_match",
        "ordered_moves_match", "ordered_move_scores_match", "unordered_moves_match",
        "scores_by_move_match", "strict_no_nodes_match", "strict_with_nodes_match",
    ]
    report = {
        "schema": "oq-safe-hint6-sample-recompute-report-v1",
        "status": "complete",
        "templates": str(templates_path),
        "sourceRaw": str(source_path),
        "engine": str(args.engine.resolve()),
        "engineSha256": sha256(args.engine.resolve()),
        "sampleManifest": str(sample_path),
        "results": str(results_path),
        "sampleSize": len(result_rows),
        "uniqueBoardPool": len(unique_templates),
        "configuration": {
            "level": 18,
            "hintCount": "min(6, legal move count)",
            "book": True,
            "threads": args.threads,
            "hashLevel": args.hash_level,
            "noAutoCacheClear": True,
            "engineProcesses": 1,
            "atomicSequence": "one exclusive engine; setboard then hint without interleaving",
            "nativeConsoleBoardEchoRequired": True,
        },
        "metrics": {
            metric: {
                "matches": sum(int(row[metric]) for row in result_rows),
                "total": len(result_rows),
                "rate": sum(int(row[metric]) for row in result_rows) / len(result_rows),
            }
            for metric in metrics
        },
        "elapsedSecondsThisInvocation": round(time.time() - started, 3),
        "sumPositionSeconds": round(sum(float(row["elapsed_seconds"]) for row in result_rows), 3),
    }
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
