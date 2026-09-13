#!/usr/bin/env python3
"""Create a path-relocated hint6 work copy while preserving original evidence.

Only absolute-path identity and the resulting contract hash change. Every historical
request, board, hint, raw Console response, key, and batch boundary is retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from portable_safe_recompute import load_runner


SEMANTIC_FIELDS = (
    "schema", "stage", "level", "threads", "use_book", "count", "hashLevel",
    "workerCount", "batchSize", "timeoutSeconds", "maxAttempts",
    "noAutoCacheClear", "quietFlag", "noBoardFlag", "engineSha256",
    "engineResources", "commandMatrixSha256", "scriptSha256",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--source-csv", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evidence = args.evidence_dir.resolve()
    output = args.output_dir.resolve()
    engine = args.engine.resolve()
    source_csv = args.source_csv.resolve()
    if output.exists():
        raise FileExistsError(f"relocated work directory already exists: {output}")
    old_manifest_path = evidence / "run_manifest.json"
    old_audit_path = evidence / "audit.json"
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    old_audit = json.loads(old_audit_path.read_text(encoding="utf-8"))
    if not old_audit.get("ok") or int(old_audit.get("rows", -1)) <= 0:
        raise ValueError("the supplied partial hint6 evidence lacks a passing partial audit")
    if old_manifest.get("stage") != "hint6":
        raise ValueError("the supplied evidence is not a hint6 run")

    runner = load_runner()
    source_identity = {
        "kind": "frozen-csv",
        "path": str(source_csv),
        "sha256": sha256_file(source_csv),
    }
    new_contract = runner.stage_contract(
        "hint6", engine, 25, source_identity,
        workers=12, batch_size=128, timeout=900.0, max_attempts=2,
    )
    for field in SEMANTIC_FIELDS:
        if old_manifest.get(field) != new_contract.get(field):
            raise ValueError(
                f"relocation refused: semantic contract field {field!r} differs; "
                f"old={old_manifest.get(field)!r} new={new_contract.get(field)!r}"
            )
    if old_manifest.get("source", {}).get("kind") != "frozen-csv":
        raise ValueError("relocation refused: original source kind is not frozen-csv")
    if old_manifest.get("source", {}).get("sha256") != source_identity["sha256"]:
        raise ValueError("relocation refused: frozen source hash differs")

    new_hash = runner.canonical_hash(new_contract)
    new_contract["contractHash"] = new_hash
    output_batches = output / "batches"
    output_batches.mkdir(parents=True)
    old_hash = str(old_manifest["contractHash"])
    rows = 0
    batch_files: list[dict[str, Any]] = []
    for source_batch in sorted((evidence / "batches").glob("batch_*.jsonl")):
        lines: list[str] = []
        batch_rows = 0
        with source_batch.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                record = json.loads(line)
                if record.get("stage") != "hint6" or record.get("contractHash") != old_hash:
                    raise ValueError(f"evidence contract mismatch at {source_batch}:{line_number}")
                record["contractHash"] = new_hash
                lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                rows += 1
                batch_rows += 1
        target_batch = output_batches / source_batch.name
        atomic_text(target_batch, "".join(lines))
        batch_files.append({
            "name": source_batch.name,
            "rows": batch_rows,
            "sourceSha256": sha256_file(source_batch),
            "relocatedSha256": sha256_file(target_batch),
        })
    if rows != int(old_audit["rows"]):
        raise ValueError(f"relocated row count {rows} differs from audited evidence {old_audit['rows']}")

    created_at = datetime.now(UTC).isoformat()
    write_json(output / "run_manifest.json", {
        **new_contract,
        "createdAt": created_at,
        "python": sys.version,
        "platform": platform.platform(),
        "relocatedFromContractHash": old_hash,
        "relocationManifest": str((output / "relocation_manifest.json").resolve()),
    })
    relocation = {
        "schema": "egaroucid-safe-path-relocation-v1",
        "ok": True,
        "createdAt": created_at,
        "evidenceDir": str(evidence),
        "evidenceManifestSha256": sha256_file(old_manifest_path),
        "evidenceAuditSha256": sha256_file(old_audit_path),
        "oldContractHash": old_hash,
        "newContractHash": new_hash,
        "rows": rows,
        "batches": len(batch_files),
        "changedFields": ["contractHash", "engine", "source.path", "commandMatrix"],
        "preservedFields": "all request/board/hint/raw-response/key/batch content",
        "batchFiles": batch_files,
    }
    write_json(output / "relocation_manifest.json", relocation)
    print(json.dumps(relocation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
