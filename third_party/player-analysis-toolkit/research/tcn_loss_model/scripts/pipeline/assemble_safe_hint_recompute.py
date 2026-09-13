#!/usr/bin/env python3
"""Assemble audited safe hint1/hint6 batches onto the frozen raw node table.

The merge key is exactly ``(game_id, move_index)``.  Engine batch raw responses stay
in their immutable JSONL files; this output carries the five mandatory board fields,
new hint values, request/config identifiers, and all original pass rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.egaroucid_safe import HintTransaction, validate_hint_transaction  # noqa: E402


EXPECTED_ROWS = 609_124
EXPECTED_PLACEMENTS = 599_112
EXPECTED_PASSES = 10_012
EXPECTED_GAMES = 10_000
PROVENANCE_FIELDS = [
    "hint1_request_board_setboard",
    "hint1_board_setboard",
    "hint1_setboard_response_board_setboard",
    "hint6_request_board_setboard",
    "hint6_board_setboard",
    "hint6_setboard_response_board_setboard",
    "hint1_request_id",
    "hint6_request_id",
    "hint1_worker_id",
    "hint6_worker_id",
    "hint1_batch_id",
    "hint6_batch_id",
    "hint1_contract_hash",
    "hint6_contract_hash",
    "hint1_engine_sha256",
    "hint6_engine_sha256",
    "hint1_engine_threads",
    "hint6_engine_threads",
    "hint1_engine_hash_level",
    "hint6_engine_hash_level",
    "hint1_use_book",
    "hint6_use_book",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def iter_records(stage_dir: Path) -> Iterator[dict[str, Any]]:
    batches = sorted((stage_dir / "batches").glob("batch_*.jsonl"))
    if not batches:
        raise FileNotFoundError(f"no committed batches found: {stage_dir}")
    for path in batches:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc


def transaction_from_record(record: dict[str, Any]) -> HintTransaction:
    stage = str(record["stage"])
    return HintTransaction(
        request_board_setboard=record[f"{stage}_request_board_setboard"],
        setboard_response_board_setboard=record["setboard_response_board_setboard"],
        hint_response_board_setboard=record[f"{stage}_board_setboard"],
        setboard_response_board_count=int(record["setboardResponseBoardCount"]),
        hint_response_board_count=int(record["hintResponseBoardCount"]),
        hints=record["hints"],
        setboard_raw_response=record["setboardRawResponse"],
        hint_raw_response=record["hintRawResponse"],
        elapsed_seconds=float(record["elapsedSeconds"]),
    )


def create_index(database: Path) -> sqlite3.Connection:
    if database.exists():
        raise FileExistsError(f"refusing to overwrite merge index: {database}")
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=PERSIST")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        """
        CREATE TABLE results (
            stage TEXT NOT NULL,
            game_id TEXT NOT NULL,
            move_index INTEGER NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY(stage, game_id, move_index)
        ) WITHOUT ROWID
        """
    )
    connection.commit()
    return connection


def minimal_payload(record: dict[str, Any]) -> dict[str, Any]:
    stage = str(record["stage"])
    return {
        "stage": stage,
        "board_setboard": record["board_setboard"],
        "legal_moves": record["legal_moves"],
        "request_board": record[f"{stage}_request_board_setboard"],
        "console_board": record[f"{stage}_board_setboard"],
        "setboard_console_board": record["setboard_response_board_setboard"],
        "hints": record["hints"],
        "request_id": record["requestId"],
        "worker_id": record["workerId"],
        "batch_id": record["batchId"],
        "contract_hash": record["contractHash"],
    }


def index_stage(
    connection: sqlite3.Connection,
    stage_dir: Path,
    stage: str,
    expected: int,
) -> tuple[int, dict[str, Any]]:
    manifest = json.loads((stage_dir / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest["stage"] != stage:
        raise ValueError(f"stage manifest mismatch: {stage_dir}")
    inserted = 0
    for record in iter_records(stage_dir):
        if record["stage"] != stage or record["contractHash"] != manifest["contractHash"]:
            raise ValueError(f"record contract mismatch for {(record.get('game_id'), record.get('move_index'))}")
        transaction = transaction_from_record(record)
        validate_hint_transaction(transaction, list(record["legal_moves"]), int(manifest["count"]))
        if record["board_setboard"] != transaction.request_board_setboard:
            raise ValueError(f"source/request board mismatch for {(record['game_id'], record['move_index'])}")
        payload = json.dumps(minimal_payload(record), ensure_ascii=False, separators=(",", ":"))
        try:
            connection.execute(
                "INSERT INTO results(stage, game_id, move_index, payload) VALUES (?, ?, ?, ?)",
                (stage, str(record["game_id"]), int(record["move_index"]), payload),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"duplicate engine result key: {(stage, record['game_id'], record['move_index'])}") from exc
        inserted += 1
        if inserted % 1000 == 0:
            connection.commit()
    connection.commit()
    if inserted != expected:
        raise ValueError(f"{stage} result count is {inserted}, expected {expected}")
    return inserted, manifest


def fetch_payload(connection: sqlite3.Connection, stage: str, game_id: str, move_index: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT payload FROM results WHERE stage=? AND game_id=? AND move_index=?",
        (stage, game_id, move_index),
    ).fetchone()
    if row is None:
        raise KeyError(f"missing {stage} result for {(game_id, move_index)}")
    return json.loads(row[0])


def format_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return value


def apply_hint_fields(row: dict[str, Any], hint1: dict[str, Any], hint6: dict[str, Any]) -> None:
    item = hint1["hints"][0]
    row["hint1_level"] = 2
    for name in ("move", "score", "nodes", "depth", "is_book"):
        row[f"hint1_{name}"] = format_value(item[name])
    for rank in range(1, 7):
        candidate = hint6["hints"][rank - 1] if rank <= len(hint6["hints"]) else None
        for name in ("move", "score", "nodes", "depth", "is_book"):
            row[f"hint6_{rank}_{name}"] = format_value(candidate[name]) if candidate else ""
    row.update(
        {
            "hint1_request_board_setboard": hint1["request_board"],
            "hint1_board_setboard": hint1["console_board"],
            "hint1_setboard_response_board_setboard": hint1["setboard_console_board"],
            "hint6_request_board_setboard": hint6["request_board"],
            "hint6_board_setboard": hint6["console_board"],
            "hint6_setboard_response_board_setboard": hint6["setboard_console_board"],
            "hint1_request_id": hint1["request_id"],
            "hint6_request_id": hint6["request_id"],
            "hint1_worker_id": hint1["worker_id"],
            "hint6_worker_id": hint6["worker_id"],
            "hint1_batch_id": hint1["batch_id"],
            "hint6_batch_id": hint6["batch_id"],
            "hint1_contract_hash": hint1["contract_hash"],
            "hint6_contract_hash": hint6["contract_hash"],
        }
    )


def clear_pass_engine_fields(row: dict[str, Any]) -> None:
    row["hint1_level"] = ""
    for name in ("move", "score", "nodes", "depth", "is_book"):
        row[f"hint1_{name}"] = ""
    for rank in range(1, 7):
        for name in ("move", "score", "nodes", "depth", "is_book"):
            row[f"hint6_{rank}_{name}"] = ""
    for field in PROVENANCE_FIELDS:
        row[field] = ""


def assemble(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source_csv.resolve()
    hint1_dir = args.hint1_dir.resolve()
    hint6_dir = args.hint6_dir.resolve()
    output = args.output_csv.resolve()
    manifest_path = args.output_manifest.resolve()
    database = args.merge_index.resolve()
    for path in (output, manifest_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite output: {path}")
    attempt = uuid.uuid4().hex[:10]
    partial = output.with_name(f"{output.name}.partial.{attempt}")
    started = time.monotonic()
    connection = create_index(database)
    try:
        hint1_count, hint1_manifest = index_stage(connection, hint1_dir, "hint1", args.expected_placements)
        hint6_count, hint6_manifest = index_stage(connection, hint6_dir, "hint6", args.expected_placements)
        engine_fields = {
            "hint1_engine_sha256": hint1_manifest["engineSha256"],
            "hint6_engine_sha256": hint6_manifest["engineSha256"],
            "hint1_engine_threads": hint1_manifest["threads"],
            "hint6_engine_threads": hint6_manifest["threads"],
            "hint1_engine_hash_level": hint1_manifest["hashLevel"],
            "hint6_engine_hash_level": hint6_manifest["hashLevel"],
            "hint1_use_book": hint1_manifest["use_book"],
            "hint6_use_book": hint6_manifest["use_book"],
        }
        rows = placements = passes = 0
        games: set[str] = set()
        seen_keys: set[tuple[str, int]] = set()
        with source.open("r", encoding="utf-8", newline="") as source_handle:
            reader = csv.DictReader(source_handle)
            if reader.fieldnames is None:
                raise ValueError("source CSV has no header")
            fieldnames = list(reader.fieldnames) + [field for field in PROVENANCE_FIELDS if field not in reader.fieldnames]
            with partial.open("w", encoding="utf-8", newline="") as output_handle:
                writer = csv.DictWriter(output_handle, fieldnames=fieldnames, extrasaction="raise")
                writer.writeheader()
                for row in reader:
                    rows += 1
                    game_id = str(row["game_id"])
                    move_index = int(row["move_index"])
                    key = (game_id, move_index)
                    if key in seen_keys:
                        raise ValueError(f"duplicate source key: {key}")
                    seen_keys.add(key)
                    games.add(game_id)
                    if row["actual_move"] == "-":
                        passes += 1
                        clear_pass_engine_fields(row)
                    else:
                        placements += 1
                        hint1 = fetch_payload(connection, "hint1", game_id, move_index)
                        hint6 = fetch_payload(connection, "hint6", game_id, move_index)
                        apply_hint_fields(row, hint1, hint6)
                        row.update({name: format_value(value) for name, value in engine_fields.items()})
                        board = row["board_setboard"]
                        board_fields = [
                            row["hint1_request_board_setboard"], row["hint1_board_setboard"],
                            row["hint6_request_board_setboard"], row["hint6_board_setboard"],
                        ]
                        if any(value != board for value in board_fields):
                            raise ValueError(f"five-board provenance mismatch during merge: {key}")
                    writer.writerow(row)
                output_handle.flush()
                os.fsync(output_handle.fileno())
        expected = (args.expected_rows, args.expected_placements, args.expected_passes, args.expected_games)
        actual = (rows, placements, passes, len(games))
        if actual != expected:
            raise ValueError(f"assembled shape mismatch: actual={actual} expected={expected}")
        os.replace(partial, output)
        report = {
            "schema": "oq-safe-hint-assembly-v1",
            "status": "complete",
            "sourceCsv": str(source),
            "sourceSha256": sha256_file(source),
            "hint1Dir": str(hint1_dir),
            "hint6Dir": str(hint6_dir),
            "hint1ContractHash": hint1_manifest["contractHash"],
            "hint6ContractHash": hint6_manifest["contractHash"],
            "rows": rows,
            "placements": placements,
            "passes": passes,
            "games": len(games),
            "hint1Results": hint1_count,
            "hint6Results": hint6_count,
            "outputCsv": str(output),
            "outputSha256": sha256_file(output),
            "mergeIndex": str(database),
            "mergeIndexSha256": sha256_file(database),
            "elapsedSeconds": time.monotonic() - started,
        }
        atomic_write_json(manifest_path, report)
        return report
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", required=True, type=Path)
    parser.add_argument("--hint1-dir", required=True, type=Path)
    parser.add_argument("--hint6-dir", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--output-manifest", required=True, type=Path)
    parser.add_argument("--merge-index", required=True, type=Path)
    parser.add_argument("--expected-rows", type=int, default=EXPECTED_ROWS)
    parser.add_argument("--expected-placements", type=int, default=EXPECTED_PLACEMENTS)
    parser.add_argument("--expected-passes", type=int, default=EXPECTED_PASSES)
    parser.add_argument("--expected-games", type=int, default=EXPECTED_GAMES)
    args = parser.parse_args()
    report = assemble(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
