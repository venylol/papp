#!/usr/bin/env python3
"""Build a self-contained Windows hint6-resume-to-model-ready server handoff ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "scripts" / "server_data_prep"
FROZEN = ROOT / "data" / "oq_elo2000_5min_bilateral_10000_model_ready_20260803_final"
HINT1 = ROOT / "outputs" / "oq_safe_full_recompute_10000_20260804" / "hint1"
HINT6_PARTIAL = ROOT / "outputs" / "oq_safe_full_recompute_10000_hint6_w12_20260804" / "hint6"
ENGINE = ROOT.parent / "server_handoffs" / "oq_egaroucid_windows_9950x_20260803_final" / "engine"
COMMAND_MATRIX = (
    ROOT.parents[2]
    / "Egaroucid_for_Console_7_8_1_Windows_AVX512_AMD"
    / "console_command_matrix.md"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        copy_function=shutil.copy2,
    )


def require(path: Path, kind: str = "file") -> None:
    ok = path.is_file() if kind == "file" else path.is_dir()
    if not ok:
        raise FileNotFoundError(f"required {kind} is missing: {path}")


def validate_inputs() -> dict[str, Any]:
    required_files = [
        TEMPLATES / "portable_safe_recompute.py",
        TEMPLATES / "relocate_hint6_resume.py",
        TEMPLATES / "validate_server_model_ready.py",
        TEMPLATES / "verify_package.py",
        TEMPLATES / "run_windows_data_prep.ps1",
        TEMPLATES / "run_windows_data_prep.cmd",
        TEMPLATES / "requirements-data-prep.txt",
        TEMPLATES / "AGENTS.md",
        TEMPLATES / "WINDOWS_9950X_DATA_PREP_AGENT_HANDOFF.md",
        ROOT / "scripts" / "pipeline" / "safe_recompute_egaroucid_hints.py",
        ROOT / "scripts" / "pipeline" / "assemble_safe_hint_recompute.py",
        ROOT / "scripts" / "data" / "materialize_oq_tcn_model_ready.py",
        COMMAND_MATRIX,
    ]
    for path in required_files:
        require(path)
    for path in (ROOT / "src", FROZEN / "handoff", FROZEN / "source_snapshot", HINT1, HINT6_PARTIAL, ENGINE):
        require(path, "directory")
    hint1_audit = json.loads((HINT1 / "audit.json").read_text(encoding="utf-8"))
    hint6_audit = json.loads((HINT6_PARTIAL / "audit.json").read_text(encoding="utf-8"))
    if not hint1_audit.get("ok") or int(hint1_audit.get("rows", -1)) != 599_112:
        raise ValueError("hint1 is not a passing 599112-row audited stage")
    if not hint6_audit.get("ok") or int(hint6_audit.get("rows", -1)) <= 0:
        raise ValueError("hint6 partial snapshot lacks a passing partial audit")
    manifest = json.loads((HINT6_PARTIAL / "run_manifest.json").read_text(encoding="utf-8"))
    expected = {
        "stage": "hint6", "level": 18, "threads": 16, "use_book": True,
        "count": 6, "hashLevel": 25, "workerCount": 12, "batchSize": 128,
        "timeoutSeconds": 900.0, "maxAttempts": 2,
        "quietFlag": False, "noBoardFlag": False,
    }
    differences = {key: (manifest.get(key), value) for key, value in expected.items() if manifest.get(key) != value}
    if differences:
        raise ValueError(f"hint6 partial snapshot violates the locked contract: {differences}")
    return {"hint1Audit": hint1_audit, "hint6Audit": hint6_audit, "hint6Manifest": manifest}


def build(args: argparse.Namespace) -> dict[str, Any]:
    validated = validate_inputs()
    output_parent = args.output_dir.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    bundle = output_parent / args.name
    zip_path = output_parent / f"{args.name}.zip"
    sha_path = output_parent / f"{args.name}.zip.sha256"
    for path in (bundle, zip_path, sha_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing package artifact: {path}")
    started = time.monotonic()
    (bundle / "app" / "scripts").mkdir(parents=True)
    (bundle / "assets" / "protocol").mkdir(parents=True)
    (bundle / "data").mkdir(parents=True)
    (bundle / "evidence").mkdir(parents=True)
    (bundle / "work" / "hint1" / "batches").mkdir(parents=True)
    (bundle / "scripts").mkdir(parents=True)

    app_scripts = {
        ROOT / "scripts" / "pipeline" / "safe_recompute_egaroucid_hints.py": "safe_recompute_egaroucid_hints.py",
        ROOT / "scripts" / "pipeline" / "assemble_safe_hint_recompute.py": "assemble_safe_hint_recompute.py",
        ROOT / "scripts" / "data" / "materialize_oq_tcn_model_ready.py": "materialize_oq_tcn_model_ready.py",
        TEMPLATES / "portable_safe_recompute.py": "portable_safe_recompute.py",
        TEMPLATES / "relocate_hint6_resume.py": "relocate_hint6_resume.py",
        TEMPLATES / "validate_server_model_ready.py": "validate_server_model_ready.py",
        TEMPLATES / "verify_package.py": "verify_package.py",
    }
    for source, name in app_scripts.items():
        shutil.copy2(source, bundle / "app" / "scripts" / name)
    copy_tree(ROOT / "src", bundle / "app" / "src")
    copy_tree(ENGINE, bundle / "assets" / "engine")
    shutil.copy2(COMMAND_MATRIX, bundle / "assets" / "protocol" / "console_command_matrix.md")
    copy_tree(FROZEN / "handoff", bundle / "data" / "handoff")
    copy_tree(FROZEN / "source_snapshot", bundle / "data" / "source_snapshot")
    copy_tree(HINT6_PARTIAL, bundle / "evidence" / "hint6_partial_original")
    shutil.copy2(HINT1 / "run_manifest.json", bundle / "work" / "hint1" / "run_manifest.json")
    for batch in sorted((HINT1 / "batches").glob("batch_*.jsonl")):
        shutil.copy2(batch, bundle / "work" / "hint1" / "batches" / batch.name)
    shutil.copy2(HINT1 / "audit.json", bundle / "evidence" / "hint1_full_audit_original.json")
    shutil.copy2(TEMPLATES / "run_windows_data_prep.ps1", bundle / "scripts" / "run_windows_data_prep.ps1")
    shutil.copy2(TEMPLATES / "run_windows_data_prep.cmd", bundle / "run_windows_data_prep.cmd")
    shutil.copy2(TEMPLATES / "requirements-data-prep.txt", bundle / "requirements-data-prep.txt")
    shutil.copy2(TEMPLATES / "AGENTS.md", bundle / "AGENTS.md")
    shutil.copy2(
        TEMPLATES / "WINDOWS_9950X_DATA_PREP_AGENT_HANDOFF.md",
        bundle / "WINDOWS_9950X_DATA_PREP_AGENT_HANDOFF.md",
    )

    package_info = {
        "schema": "oq-tcn-windows-data-prep-handoff-v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "purpose": "resume audited hint6, assemble frozen 10000 games, materialize validated TCN data; no training",
        "platform": "Windows 9950X server, Python, no CUDA required",
        "frozenShape": {"games": 10_000, "rows": 609_124, "placements": 599_112, "passes": 10_012},
        "hint1Rows": int(validated["hint1Audit"]["rows"]),
        "hint6PartialRows": int(validated["hint6Audit"]["rows"]),
        "lockedHint6": {"workers": 12, "threadsPerConsole": 16, "level": 18, "book": True, "hashLevel": 25},
        "trainingAuthorized": False,
    }
    write_json(bundle / "PACKAGE_INFO.json", package_info)

    files = []
    for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
        relative = path.relative_to(bundle).as_posix()
        # hint1 audit is regenerated on every entrypoint run and is deliberately not
        # a packaged work file; immutable original evidence is stored under evidence/.
        files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    package_manifest = {
        "schema": "oq-tcn-windows-data-prep-package-manifest-v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "files": files,
        "fileCount": len(files),
        "totalBytes": sum(item["bytes"] for item in files),
    }
    write_json(bundle / "package_manifest.json", package_manifest)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
            archive.write(path, (Path(args.name) / path.relative_to(bundle)).as_posix())
    zip_hash = sha256_file(zip_path)
    sha_path.write_text(f"{zip_hash}  {zip_path.name}\n", encoding="utf-8")
    report = {
        **package_info,
        "bundleDirectory": str(bundle),
        "zipPath": str(zip_path),
        "zipBytes": zip_path.stat().st_size,
        "zipSha256": zip_hash,
        "manifestSha256": sha256_file(bundle / "package_manifest.json"),
        "elapsedSeconds": time.monotonic() - started,
    }
    write_json(output_parent / f"{args.name}.build_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "server_packages")
    parser.add_argument("--name", default="oq_tcn_windows_9950x_data_prep_20260804")
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
