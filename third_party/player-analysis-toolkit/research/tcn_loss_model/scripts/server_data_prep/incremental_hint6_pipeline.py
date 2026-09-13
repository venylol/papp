#!/usr/bin/env python3
"""Prepare missing hint6 work and assemble a server-priority incremental result."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

PLACEMENTS = 599_112
ROWS = 609_124
PASSES = 10_012
GAMES = 10_000
PROVENANCE_FIELDS = [
    "hint1_request_board_setboard", "hint1_board_setboard",
    "hint1_setboard_response_board_setboard", "hint6_request_board_setboard",
    "hint6_board_setboard", "hint6_setboard_response_board_setboard",
    "hint1_request_id", "hint6_request_id", "hint1_worker_id", "hint6_worker_id",
    "hint1_batch_id", "hint6_batch_id", "hint1_contract_hash", "hint6_contract_hash",
    "hint1_engine_sha256", "hint6_engine_sha256", "hint1_engine_threads",
    "hint6_engine_threads", "hint1_engine_hash_level", "hint6_engine_hash_level",
    "hint1_use_book", "hint6_use_book", "hint6_provenance_tier", "hint6_origin",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def iter_records(stage_dir: Path) -> Iterator[dict[str, Any]]:
    batches = sorted((stage_dir / "batches").glob("batch_*.jsonl"))
    if not batches:
        raise FileNotFoundError(f"no committed batches: {stage_dir}")
    for path in batches:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL: {path}:{line_number}") from exc


def source_nodes(path: Path) -> tuple[dict[tuple[str, int], dict[str, Any]], list[str]]:
    result = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        for row in reader:
            if row["actual_move"] == "-" or row.get("is_pass_record") == "1":
                continue
            key = (row["game_id"], int(row["move_index"]))
            if key in result:
                raise ValueError(f"duplicate source placement: {key}")
            result[key] = {
                "board": row["board_setboard"],
                "legal": row["legal_moves"].lower().split(),
                "actual_move": row["actual_move"].lower(),
                "side_to_move": row["side_to_move"].lower(),
                "source_ply_including_pass": int(row["source_ply_including_pass"]),
                "global_placement_ply": int(row["global_placement_ply"]),
            }
    if len(result) != PLACEMENTS:
        raise ValueError(f"source placements {len(result)} != {PLACEMENTS}")
    return result, fields


def validate_hints(hints: list[dict[str, Any]], legal: list[str], count: int) -> None:
    expected = min(count, len(legal))
    moves = [str(item["move"]).lower() for item in hints]
    if len(hints) != expected or len(moves) != len(set(moves)) or not set(moves).issubset(set(legal)):
        raise ValueError(f"invalid candidates: expected={expected} legal={legal} hints={moves}")
    for item in hints:
        for field in ("score", "nodes", "depth", "is_book"):
            if item.get(field) is None or str(item[field]).strip() == "":
                raise ValueError(f"incomplete candidate field {field}: {item}")


def validate_native(record: dict[str, Any], manifest: dict[str, Any], source: dict[tuple[str, int], dict[str, Any]]) -> tuple[str, int]:
    stage = manifest["stage"]
    if record.get("stage") != stage or record.get("contractHash") != manifest["contractHash"]:
        raise ValueError("native record contract mismatch")
    key = (str(record["game_id"]), int(record["move_index"]))
    node = source.get(key)
    if node is None:
        raise ValueError(f"native record outside frozen placements: {key}")
    board = node["board"]
    boards = [
        record["board_setboard"], record[f"{stage}_request_board_setboard"],
        record[f"{stage}_board_setboard"], record["setboard_response_board_setboard"],
    ]
    if any(value != board for value in boards):
        raise ValueError(f"native board mismatch: {key}")
    if (
        int(record["source_ply_including_pass"]) != node["source_ply_including_pass"]
        or int(record["global_placement_ply"]) != node["global_placement_ply"]
        or str(record["side_to_move"]).lower() != node["side_to_move"]
        or str(record["actual_move"]).lower() != node["actual_move"]
    ):
        raise ValueError(f"native pass-aware index identity mismatch: {key}")
    if int(record["setboardResponseBoardCount"]) != 1 or int(record["hintResponseBoardCount"]) != 1:
        raise ValueError(f"native board response count mismatch: {key}")
    if not record.get("setboardRawResponse") or not record.get("hintRawResponse"):
        raise ValueError(f"native raw response missing: {key}")
    validate_hints(record["hints"], node["legal"], int(manifest["count"]))
    return key


def native_keys(stage_dir: Path, source: dict[tuple[str, int], dict[str, Any]]) -> tuple[set[tuple[str, int]], dict[str, Any]]:
    manifest = json.loads((stage_dir / "run_manifest.json").read_text(encoding="utf-8"))
    keys = set()
    for record in iter_records(stage_dir):
        key = validate_native(record, manifest, source)
        if key in keys:
            raise ValueError(f"duplicate native key in {stage_dir}: {key}")
        keys.add(key)
    return keys, manifest


def legacy_keys(path: Path, source: dict[tuple[str, int], dict[str, Any]]) -> set[tuple[str, int]]:
    keys = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            key = (str(record["game_id"]), int(record["move_index"]))
            node = source.get(key)
            identity_matches = node is not None and (
                record["board_setboard"] == node["board"]
                and str(record["side_to_move"]).lower() == node["side_to_move"]
                and str(record["actual_move"]).lower() == node["actual_move"]
                and int(record["source_ply_including_pass"]) == node["source_ply_including_pass"]
                and int(record["global_placement_ply"]) == node["global_placement_ply"]
            )
            if not identity_matches:
                raise ValueError(f"legacy seed identity mismatch: {key}")
            if record.get("provenanceTier") != "legacy-exact-key-board-and-legality-screened":
                raise ValueError(f"unexpected legacy tier: {key}")
            validate_hints(record["hints"], node["legal"], 6)
            if key in keys:
                raise ValueError(f"duplicate legacy seed key: {key}")
            keys.add(key)
    return keys


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    source_path = args.source_csv.resolve()
    attempt = args.attempt_dir.resolve()
    if attempt.exists():
        raise FileExistsError(f"attempt already exists: {attempt}")
    attempt.mkdir(parents=True)
    source, fields = source_nodes(source_path)
    selected = set()
    origin_counts = Counter()
    overlap_counts = Counter()
    manifests = {}
    for label, stage_dir in (
        ("server-current", args.server_hint6.resolve()),
        ("local-w12", args.local_w12.resolve()),
        ("local-w1", args.local_w1.resolve()),
    ):
        keys, manifest = native_keys(stage_dir, source)
        manifests[label] = {
            "dir": str(stage_dir), "rows": len(keys), "contractHash": manifest["contractHash"],
        }
        for key in keys:
            if key in selected:
                overlap_counts[label] += 1
            else:
                selected.add(key)
                origin_counts[label] += 1
    legacy = legacy_keys(args.legacy_seed.resolve(), source)
    for key in legacy:
        if key in selected:
            overlap_counts["legacy"] += 1
        else:
            selected.add(key)
            origin_counts["legacy"] += 1

    missing_path = attempt / "missing_hint6_source.csv"
    rows = missing = 0
    with source_path.open("r", encoding="utf-8", newline="") as source_handle, missing_path.open(
        "w", encoding="utf-8", newline=""
    ) as output_handle:
        reader = csv.DictReader(source_handle)
        writer = csv.DictWriter(output_handle, fieldnames=fields)
        writer.writeheader()
        for row in reader:
            if row["actual_move"] == "-" or row.get("is_pass_record") == "1":
                continue
            rows += 1
            key = (row["game_id"], int(row["move_index"]))
            if key not in selected:
                writer.writerow(row)
                missing += 1
        output_handle.flush()
        os.fsync(output_handle.fileno())
    if rows != PLACEMENTS or len(selected) + missing != PLACEMENTS:
        raise ValueError("incremental selection does not partition frozen placements")
    report = {
        "schema": "oq-hint6-incremental-prepare-v1", "ok": True,
        "sourceCsv": str(source_path), "sourceSha256": sha256_file(source_path),
        "priority": ["server-current", "local-w12", "local-w1", "legacy", "new-compute"],
        "indexContract": {
            "gamePairing": "exact game_id only",
            "nodePairing": "exact (game_id, move_index) only",
            "moveIndexSemantics": "original OQ index; explicit '-' pass consumes an index",
            "placementPlySemantics": "global_placement_ply excludes pass and is never used as merge key",
            "boardOnlyRemappingUsed": False,
        },
        "selectedRows": len(selected), "missingRows": missing,
        "selectedByOrigin": dict(origin_counts), "shadowedByHigherPriority": dict(overlap_counts),
        "nativeInputs": manifests,
        "legacySeed": {"path": str(args.legacy_seed.resolve()), "rows": len(legacy), "sha256": sha256_file(args.legacy_seed.resolve())},
        "missingSource": str(missing_path), "missingSourceSha256": sha256_file(missing_path),
    }
    atomic_json(attempt / "prepare_manifest.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def create_db(path: Path) -> sqlite3.Connection:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite merge index: {path}")
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("CREATE TABLE results(stage TEXT, game_id TEXT, move_index INTEGER, payload TEXT, PRIMARY KEY(stage,game_id,move_index)) WITHOUT ROWID")
    return connection


def safe_payload(record: dict[str, Any], manifest: dict[str, Any], origin: str) -> dict[str, Any]:
    stage = manifest["stage"]
    return {
        "hints": record["hints"], "request_board": record[f"{stage}_request_board_setboard"],
        "console_board": record[f"{stage}_board_setboard"],
        "setboard_console_board": record["setboard_response_board_setboard"],
        "request_id": record["requestId"], "worker_id": record["workerId"],
        "batch_id": record["batchId"], "contract_hash": record["contractHash"],
        "engine_sha256": manifest["engineSha256"], "threads": manifest["threads"],
        "hash_level": manifest["hashLevel"], "use_book": manifest["use_book"],
        "tier": "native-console-response", "origin": origin,
    }


def index_native(connection: sqlite3.Connection, stage_dir: Path, stage: str, origin: str, source: dict[tuple[str, int], dict[str, Any]], replace: bool = False) -> tuple[int, int]:
    manifest = json.loads((stage_dir / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest["stage"] != stage:
        raise ValueError(f"stage mismatch: {stage_dir}")
    inserted = shadowed = 0
    for record in iter_records(stage_dir):
        key = validate_native(record, manifest, source)
        payload = json.dumps(safe_payload(record, manifest, origin), ensure_ascii=False, separators=(",", ":"))
        command = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        cursor = connection.execute(f"{command} INTO results VALUES (?,?,?,?)", (stage, key[0], key[1], payload))
        if cursor.rowcount:
            inserted += 1
        else:
            shadowed += 1
        if (inserted + shadowed) % 2000 == 0:
            connection.commit()
    connection.commit()
    return inserted, shadowed


def index_legacy(connection: sqlite3.Connection, path: Path, source: dict[tuple[str, int], dict[str, Any]]) -> tuple[int, int]:
    inserted = shadowed = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            key = (str(record["game_id"]), int(record["move_index"]))
            node = source[key]
            if (
                record["board_setboard"] != node["board"]
                or str(record["side_to_move"]).lower() != node["side_to_move"]
                or str(record["actual_move"]).lower() != node["actual_move"]
                or int(record["source_ply_including_pass"]) != node["source_ply_including_pass"]
                or int(record["global_placement_ply"]) != node["global_placement_ply"]
            ):
                raise ValueError(f"legacy pass-aware index identity mismatch: {key}")
            validate_hints(record["hints"], node["legal"], 6)
            payload = {
                "hints": record["hints"], "request_board": node["board"],
                "console_board": "", "setboard_console_board": "",
                "request_id": f"legacy:{record['legacySourceSha256']}:{record['legacySourceLine']}",
                "worker_id": "", "batch_id": "", "contract_hash": "legacy-exact-key-screened-v1",
                "engine_sha256": "", "threads": 16, "hash_level": 25, "use_book": True,
                "tier": record["provenanceTier"], "origin": f"legacy:{record['legacySourceLabel']}",
            }
            cursor = connection.execute(
                "INSERT OR IGNORE INTO results VALUES (?,?,?,?)",
                ("hint6", key[0], key[1], json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            )
            if cursor.rowcount:
                inserted += 1
            else:
                shadowed += 1
            if (inserted + shadowed) % 2000 == 0:
                connection.commit()
    connection.commit()
    return inserted, shadowed


def fetch(connection: sqlite3.Connection, stage: str, key: tuple[str, int]) -> dict[str, Any]:
    row = connection.execute("SELECT payload FROM results WHERE stage=? AND game_id=? AND move_index=?", (stage, key[0], key[1])).fetchone()
    if row is None:
        raise KeyError(f"missing {stage}: {key}")
    return json.loads(row[0])


def formatted(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return value


def clear_pass(row: dict[str, Any]) -> None:
    row["hint1_level"] = ""
    for name in ("move", "score", "nodes", "depth", "is_book"):
        row[f"hint1_{name}"] = ""
    for rank in range(1, 7):
        for name in ("move", "score", "nodes", "depth", "is_book"):
            row[f"hint6_{rank}_{name}"] = ""
    for field in PROVENANCE_FIELDS:
        row[field] = ""


def apply(row: dict[str, Any], hint1: dict[str, Any], hint6: dict[str, Any]) -> None:
    row["hint1_level"] = 2
    for name in ("move", "score", "nodes", "depth", "is_book"):
        row[f"hint1_{name}"] = formatted(hint1["hints"][0][name])
    for rank in range(1, 7):
        candidate = hint6["hints"][rank - 1] if rank <= len(hint6["hints"]) else None
        for name in ("move", "score", "nodes", "depth", "is_book"):
            row[f"hint6_{rank}_{name}"] = formatted(candidate[name]) if candidate else ""
    for stage, payload in (("hint1", hint1), ("hint6", hint6)):
        row[f"{stage}_request_board_setboard"] = payload["request_board"]
        row[f"{stage}_board_setboard"] = payload["console_board"]
        row[f"{stage}_setboard_response_board_setboard"] = payload["setboard_console_board"]
        row[f"{stage}_request_id"] = payload["request_id"]
        row[f"{stage}_worker_id"] = payload["worker_id"]
        row[f"{stage}_batch_id"] = payload["batch_id"]
        row[f"{stage}_contract_hash"] = payload["contract_hash"]
        row[f"{stage}_engine_sha256"] = payload["engine_sha256"]
        row[f"{stage}_engine_threads"] = payload["threads"]
        row[f"{stage}_engine_hash_level"] = payload["hash_level"]
        row[f"{stage}_use_book"] = formatted(payload["use_book"])
    row["hint6_provenance_tier"] = hint6["tier"]
    row["hint6_origin"] = hint6["origin"]


def assemble(args: argparse.Namespace) -> dict[str, Any]:
    source_path = args.source_csv.resolve()
    source, _ = source_nodes(source_path)
    output = args.output_csv.resolve()
    manifest_path = args.output_manifest.resolve()
    for path in (output, manifest_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    connection = create_db(args.merge_index.resolve())
    counts = {}
    try:
        counts["hint1"] = index_native(connection, args.hint1_dir.resolve(), "hint1", "server-hint1", source)
        for label, directory in (
            ("server-current", args.server_hint6.resolve()),
            ("local-w12", args.local_w12.resolve()),
            ("local-w1", args.local_w1.resolve()),
            ("new-compute", args.new_hint6.resolve()),
        ):
            counts[label] = index_native(connection, directory, "hint6", label, source)
        counts["legacy"] = index_legacy(connection, args.legacy_seed.resolve(), source)
        hint1_total = connection.execute("SELECT COUNT(*) FROM results WHERE stage='hint1'").fetchone()[0]
        hint6_total = connection.execute("SELECT COUNT(*) FROM results WHERE stage='hint6'").fetchone()[0]
        if hint1_total != PLACEMENTS or hint6_total != PLACEMENTS:
            raise ValueError(f"incomplete merge index: hint1={hint1_total} hint6={hint6_total}")

        partial = output.with_suffix(output.suffix + ".partial")
        rows = placements = passes = 0
        games = set()
        tiers = Counter()
        origins = Counter()
        with source_path.open("r", encoding="utf-8", newline="") as source_handle, partial.open(
            "w", encoding="utf-8", newline=""
        ) as output_handle:
            reader = csv.DictReader(source_handle)
            fields = list(reader.fieldnames or []) + [field for field in PROVENANCE_FIELDS if field not in (reader.fieldnames or [])]
            writer = csv.DictWriter(output_handle, fieldnames=fields)
            writer.writeheader()
            for row in reader:
                rows += 1
                games.add(row["game_id"])
                key = (row["game_id"], int(row["move_index"]))
                if row["actual_move"] == "-" or row.get("is_pass_record") == "1":
                    passes += 1
                    clear_pass(row)
                else:
                    placements += 1
                    hint1 = fetch(connection, "hint1", key)
                    hint6 = fetch(connection, "hint6", key)
                    apply(row, hint1, hint6)
                    tiers[hint6["tier"]] += 1
                    origins[hint6["origin"]] += 1
                writer.writerow(row)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        actual = (rows, placements, passes, len(games))
        if actual != (ROWS, PLACEMENTS, PASSES, GAMES):
            raise ValueError(f"assembled shape mismatch: {actual}")
        os.replace(partial, output)
        report = {
            "schema": "oq-hint6-incremental-assembly-v1", "status": "complete",
            "sourceCsv": str(source_path), "sourceSha256": sha256_file(source_path),
            "rows": rows, "placements": placements, "passes": passes, "games": len(games),
            "indexCounts": counts, "hint6ProvenanceTiers": dict(tiers), "hint6Origins": dict(origins),
            "outputCsv": str(output), "outputSha256": sha256_file(output),
        }
        atomic_json(manifest_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source-csv", required=True, type=Path)
    common.add_argument("--server-hint6", required=True, type=Path)
    common.add_argument("--local-w12", required=True, type=Path)
    common.add_argument("--local-w1", required=True, type=Path)
    common.add_argument("--legacy-seed", required=True, type=Path)
    prepare_parser = commands.add_parser("prepare", parents=[common])
    prepare_parser.add_argument("--attempt-dir", required=True, type=Path)
    assemble_parser = commands.add_parser("assemble", parents=[common])
    assemble_parser.add_argument("--hint1-dir", required=True, type=Path)
    assemble_parser.add_argument("--new-hint6", required=True, type=Path)
    assemble_parser.add_argument("--output-csv", required=True, type=Path)
    assemble_parser.add_argument("--output-manifest", required=True, type=Path)
    assemble_parser.add_argument("--merge-index", required=True, type=Path)
    args = parser.parse_args()
    prepare(args) if args.command == "prepare" else assemble(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
