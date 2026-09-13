#!/usr/bin/env python3
"""Recompute resumable local hint1 templates from a frozen handoff CSV.

Future formal runs must retain both the request board and the native Console board
parsed from every hint1 response.  The older investigation CSV's ``board_setboard``
is request-side provenance only and is not sufficient for formal acceptance.  Keep
the CSV, progress, and engine logs until the final complete model-ready dataset passes
acceptance; this script never cleans them up.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import queue
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any


OUTPUT_FIELDS = [
    "game_id", "move_index", "ply", "side_to_move", "board_setboard",
    "hint1_request_board_setboard", "hint1_board_setboard", "legal_moves",
    "server_hint1_move", "server_hint1_score", "server_hint1_nodes",
    "server_hint1_depth", "server_hint1_is_book",
    "local_hint1_move", "local_hint1_score", "local_hint1_nodes",
    "local_hint1_depth", "local_hint1_is_book",
    "move_match", "score_match", "depth_match", "nodes_delta", "worker",
]


def load_analyzer(path: Path) -> ModuleType:
    name = "oq_hint1_template_engine"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import analyzer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_completed(path: Path) -> set[tuple[str, int]]:
    completed: set[tuple[str, int]] = set()
    if not path.exists():
        return completed
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != OUTPUT_FIELDS:
            raise RuntimeError(
                "existing hint1 template CSV predates native Console-board provenance; "
                "use a new output directory instead of appending"
            )
        for row in reader:
            completed.add((row["game_id"], int(row["move_index"])))
    return completed


def write_progress(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-raw", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--analyzer-module", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--hash-level", type=int, default=25)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    workers = max(1, int(args.workers))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "hint1_templates.csv"
    progress_path = output_dir / "progress.json"
    completed = read_completed(output_path)
    analyzer = load_analyzer(args.analyzer_module.resolve())

    task_queues = [queue.Queue(maxsize=32) for _ in range(workers)]
    result_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    stop = threading.Event()
    producer_summary: dict[str, int] = {"queued": 0, "skippedCompleted": 0, "passRows": 0}

    def producer() -> None:
        queued = 0
        try:
            with args.source_raw.resolve().open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    if stop.is_set():
                        break
                    if row["actual_move"] == "-":
                        producer_summary["passRows"] += 1
                        continue
                    key = (row["game_id"], int(row["move_index"]))
                    if key in completed:
                        producer_summary["skippedCompleted"] += 1
                        continue
                    if args.limit > 0 and queued >= args.limit:
                        break
                    task = {
                        "game_id": row["game_id"],
                        "move_index": int(row["move_index"]),
                        "ply": int(row["ply"]),
                        "side_to_move": row["side_to_move"],
                        "board_setboard": row["board_setboard"],
                        "legal_moves": row["legal_moves"],
                        "server_hint1_move": row["hint1_move"],
                        "server_hint1_score": row["hint1_score"],
                        "server_hint1_nodes": row["hint1_nodes"],
                        "server_hint1_depth": row["hint1_depth"],
                        "server_hint1_is_book": row["hint1_is_book"],
                    }
                    task_queues[queued % workers].put(task)
                    queued += 1
                    producer_summary["queued"] = queued
        except Exception as exc:
            stop.set()
            result_queue.put({"kind": "error", "where": "producer", "error": repr(exc)})
        finally:
            for task_queue in task_queues:
                task_queue.put(None)

    def worker(worker_index: int) -> None:
        engine = None
        try:
            engine = analyzer.PersistentEngine(
                args.engine.resolve(),
                2,
                1,
                int(args.hash_level),
                output_dir / f"hint1_worker_{worker_index + 1:02d}.log",
                False,
            )
            while not stop.is_set():
                task = task_queues[worker_index].get()
                if task is None:
                    break
                hints, console_board = engine.hint_for_board(
                    task["board_setboard"], 1, timeout=float(args.timeout)
                )
                if len(hints) != 1:
                    raise RuntimeError(f"expected one hint for {(task['game_id'], task['move_index'])}")
                if console_board != task["board_setboard"]:
                    raise RuntimeError(
                        f"hint1 Console board mismatch for {(task['game_id'], task['move_index'])}: "
                        f"request={task['board_setboard']} console={console_board}"
                    )
                hint = hints[0]
                legal_moves = set(task["legal_moves"].split())
                if str(hint.get("move", "")) not in legal_moves:
                    raise RuntimeError(
                        f"local hint1 move is illegal for {(task['game_id'], task['move_index'])}: {hint}"
                    )
                local_nodes = int(hint["nodes"])
                server_nodes = int(task["server_hint1_nodes"])
                result_queue.put({
                    "kind": "result",
                    **task,
                    "hint1_request_board_setboard": task["board_setboard"],
                    "hint1_board_setboard": console_board,
                    "local_hint1_move": hint["move"],
                    "local_hint1_score": hint["score"],
                    "local_hint1_nodes": local_nodes,
                    "local_hint1_depth": hint["depth"],
                    "local_hint1_is_book": hint["is_book"],
                    "move_match": int(str(hint["move"]) == task["server_hint1_move"]),
                    "score_match": int(str(hint["score"]) == task["server_hint1_score"]),
                    "depth_match": int(str(hint["depth"]) == task["server_hint1_depth"]),
                    "nodes_delta": local_nodes - server_nodes,
                    "worker": worker_index + 1,
                })
        except Exception as exc:
            stop.set()
            result_queue.put({
                "kind": "error", "where": f"worker-{worker_index + 1}", "error": repr(exc)
            })
        finally:
            if engine is not None:
                engine.close()
            result_queue.put({"kind": "done", "worker": worker_index + 1})

    started = time.time()
    producer_thread = threading.Thread(target=producer, name="hint1-producer", daemon=True)
    worker_threads = [
        threading.Thread(target=worker, args=(index,), name=f"hint1-worker-{index + 1}", daemon=True)
        for index in range(workers)
    ]
    for thread in worker_threads:
        thread.start()
    producer_thread.start()

    new_file = not output_path.exists()
    written = move_matches = score_matches = depth_matches = 0
    errors: list[dict[str, Any]] = []
    done_workers = 0
    with output_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        while done_workers < workers:
            item = result_queue.get()
            if item["kind"] == "done":
                done_workers += 1
                continue
            if item["kind"] == "error":
                errors.append(item)
                continue
            writer.writerow(item)
            written += 1
            move_matches += int(item["move_match"])
            score_matches += int(item["score_match"])
            depth_matches += int(item["depth_match"])
            if written % 100 == 0:
                handle.flush()
            if written % 1000 == 0:
                elapsed = time.time() - started
                write_progress(progress_path, {
                    "schema": "oq-local-hint1-template-progress-v1",
                    "status": "running",
                    "writtenThisRun": written,
                    "previouslyCompleted": len(completed),
                    "queued": producer_summary["queued"],
                    "elapsedSeconds": round(elapsed, 3),
                    "positionsPerSecond": written / elapsed if elapsed else None,
                    "moveMatches": move_matches,
                    "scoreMatches": score_matches,
                    "depthMatches": depth_matches,
                    "errors": errors,
                })

    producer_thread.join()
    for thread in worker_threads:
        thread.join()
    elapsed = time.time() - started
    report = {
        "schema": "oq-local-hint1-template-progress-v1",
        "status": "failed" if errors else "complete",
        "sourceRaw": str(args.source_raw.resolve()),
        "engine": str(args.engine.resolve()),
        "workers": workers,
        "level": 2,
        "threadsPerEngine": 1,
        "hashLevel": int(args.hash_level),
        "book": False,
        "noAutoCacheClear": True,
        "writtenThisRun": written,
        "previouslyCompleted": len(completed),
        "queued": producer_summary["queued"],
        "passRowsSkipped": producer_summary["passRows"],
        "elapsedSeconds": round(elapsed, 3),
        "positionsPerSecond": written / elapsed if elapsed else None,
        "moveMatches": move_matches,
        "scoreMatches": score_matches,
        "depthMatches": depth_matches,
        "errors": errors,
    }
    write_progress(progress_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
