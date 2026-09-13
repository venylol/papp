#!/usr/bin/env python3
"""Build the Windows exact-index incremental hint6 handoff ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "scripts" / "server_data_prep"
SOURCE = ROOT / "data" / "oq_elo2000_5min_bilateral_10000_source_only_20260804"
W12 = ROOT / "outputs" / "oq_safe_full_recompute_10000_hint6_w12_20260804" / "hint6"
W1 = ROOT / "outputs" / "oq_safe_full_recompute_10000_20260804" / "hint6"
LEGACY_AUDIT = ROOT / "outputs" / "legacy_hint6_exact_index_audit_20260804"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"), copy_function=shutil.copy2)


def row_count(stage: Path) -> int:
    return sum(1 for batch in (stage / "batches").glob("batch_*.jsonl") for _ in batch.open("r", encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "server_packages")
    parser.add_argument("--name", default="oq_tcn_windows_9950x_incremental_exact_index_20260804_v4")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / args.name
    archive_path = output / f"{args.name}.zip"
    sha_path = output / f"{args.name}.zip.sha256"
    report_path = output / f"{args.name}.build_report.json"
    for path in (bundle, archive_path, sha_path, report_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")

    required = [
        SOURCE / "SOURCE_ONLY_MANIFEST.json", W12 / "run_manifest.json", W12 / "audit.json",
        W1 / "run_manifest.json", LEGACY_AUDIT / "audit.json",
        LEGACY_AUDIT / "legacy_hint6_exact_index_seed.jsonl",
        LEGACY_AUDIT / "reference_sample_audit_v2.json",
        SERVER / "incremental_hint6_pipeline.py", SERVER / "run_incremental_merge_and_data_prep.ps1",
        SERVER / "run_incremental_merge_and_data_prep.cmd", SERVER / "verify_package.py",
        SERVER / "WINDOWS_INCREMENTAL_EXACT_INDEX_AGENT_HANDOFF.md",
        ROOT / "scripts" / "data" / "materialize_oq_tcn_model_ready.py",
    ]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
    w12_rows = row_count(W12)
    w1_rows = row_count(W1)
    if w12_rows != 14080 or w1_rows != 53632:
        raise ValueError(f"unexpected native evidence counts: w12={w12_rows} w1={w1_rows}")
    legacy = json.loads((LEGACY_AUDIT / "audit.json").read_text(encoding="utf-8"))
    sample = json.loads((LEGACY_AUDIT / "reference_sample_audit_v2.json").read_text(encoding="utf-8"))
    if not legacy.get("ok") or legacy.get("selectedUniqueRows") != 381437 or not sample.get("ok"):
        raise ValueError("legacy exact-index audit or reference sample gate failed")

    (bundle / "scripts").mkdir(parents=True)
    copy_tree(ROOT / "src", bundle / "src")
    (bundle / "evidence").mkdir(parents=True)
    (bundle / "audit").mkdir(parents=True)
    copy_tree(SOURCE, bundle / "assets" / "source")
    copy_tree(W12, bundle / "evidence" / "hint6_local_w12")
    copy_tree(W1, bundle / "evidence" / "hint6_local_w1")
    shutil.copy2(LEGACY_AUDIT / "legacy_hint6_exact_index_seed.jsonl", bundle / "evidence" / "legacy_hint6_exact_index_seed.jsonl")
    shutil.copy2(LEGACY_AUDIT / "audit.json", bundle / "audit" / "legacy_exact_index_audit.json")
    shutil.copy2(LEGACY_AUDIT / "reference_sample_audit_v2.json", bundle / "audit" / "reference_sample_audit.json")
    for source, name in (
        (SERVER / "incremental_hint6_pipeline.py", "incremental_hint6_pipeline.py"),
        (SERVER / "run_incremental_merge_and_data_prep.ps1", "run_incremental_merge_and_data_prep.ps1"),
        (SERVER / "verify_package.py", "verify_package.py"),
        (ROOT / "scripts" / "data" / "materialize_oq_tcn_model_ready.py", "materialize_oq_tcn_model_ready.py"),
    ):
        shutil.copy2(source, bundle / "scripts" / name)
    shutil.copy2(SERVER / "run_incremental_merge_and_data_prep.cmd", bundle / "run_incremental_merge_and_data_prep.cmd")
    shutil.copy2(SERVER / "WINDOWS_INCREMENTAL_EXACT_INDEX_AGENT_HANDOFF.md", bundle / "WINDOWS_INCREMENTAL_EXACT_INDEX_AGENT_HANDOFF.md")

    info = {
        "schema": "oq-tcn-windows-incremental-exact-index-v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "requiresExistingPackage": "oq_tcn_windows_9950x_data_prep_20260804_v3 extracted directory",
        "priority": ["server-current", "local-w12", "local-w1", "legacy-exact-index", "new-compute"],
        "indexContract": {
            "game": "exact game_id", "node": "exact (game_id, move_index)",
            "moveIndexIncludesExplicitPass": True, "boardOnlyRemappingUsed": False,
        },
        "nativeW12Rows": w12_rows, "nativeW1Rows": w1_rows,
        "legacyExactIndexRows": legacy["selectedUniqueRows"],
        "referenceChecks": {
            "hint1Nodes": sample["hint1"]["checked"], "hint1ExactRate": sample["rates"]["hint1MoveAndScoreSame"],
            "hint6Nodes": sample["hint6"]["checked"], "hint6Top1Rate": sample["rates"]["hint6Top1MoveSame"],
            "hint6SetRate": sample["rates"]["hint6UnorderedMovesSame"],
        },
        "trainingAuthorized": False, "cudaRequired": False,
    }
    write_json(bundle / "PACKAGE_INFO.json", info)

    files = []
    for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
        files.append({"path": path.relative_to(bundle).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {"schema": "oq-incremental-package-manifest-v1", "files": files, "fileCount": len(files), "totalBytes": sum(item["bytes"] for item in files)}
    write_json(bundle / "package_manifest.json", manifest)

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
            archive.write(path, (Path(args.name) / path.relative_to(bundle)).as_posix())
    archive_hash = sha256_file(archive_path)
    sha_path.write_text(f"{archive_hash}  {archive_path.name}\n", encoding="utf-8")
    report = {**info, "zipPath": str(archive_path), "zipBytes": archive_path.stat().st_size, "zipSha256": archive_hash, "manifestSha256": sha256_file(bundle / "package_manifest.json")}
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
