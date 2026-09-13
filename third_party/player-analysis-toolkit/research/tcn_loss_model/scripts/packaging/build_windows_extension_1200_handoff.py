#!/usr/bin/env python3
"""Build the Windows 9950X independent 1200-game extension handoff ZIP."""

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
SOURCE = ROOT / "outputs" / "oq_bilateral_extension_1200_hint_source_20260804"
PULL = ROOT / "outputs" / "oq_bilateral_extension_1000_pull_20260804"
PROFILE_AUDIT = ROOT / "outputs" / "oq_player_profile_index_extension_1200_20260804" / "coverage_audit.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "server_packages")
    parser.add_argument("--name", default="oq_tcn_windows_9950x_extension_1200_20260804_v2")
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

    scripts = {
        ROOT / "scripts" / "audit" / "audit_oq_extension_source.py": "scripts/audit_oq_extension_source.py",
        ROOT / "scripts" / "packaging" / "package_extension_return.py": "scripts/package_extension_return.py",
        SERVER / "verify_package.py": "scripts/verify_package.py",
        SERVER / "run_extension_1200_data_prep.ps1": "scripts/run_extension_1200_data_prep.ps1",
        SERVER / "run_extension_1200_data_prep.cmd": "run_extension_1200_data_prep.cmd",
        SERVER / "WINDOWS_EXTENSION_1200_AGENT_HANDOFF.md": "WINDOWS_EXTENSION_1200_AGENT_HANDOFF.md",
    }
    pull_names = ["games.csv", "move_times.csv", "game_player_summaries.csv", "progress.json", "pull_manifest.json"]
    required = list(scripts) + [SOURCE / "source_manifest.json", PROFILE_AUDIT] + [PULL / name for name in pull_names]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
    source_manifest = json.loads((SOURCE / "source_manifest.json").read_text(encoding="utf-8"))
    pull_manifest = json.loads((PULL / "pull_manifest.json").read_text(encoding="utf-8"))
    expected_shape = {"games": 1200, "rows": 72940, "placements": 71954, "passes": 986}
    if not source_manifest.get("ok") or any(int(source_manifest["shape"][key]) != value for key, value in expected_shape.items()):
        raise ValueError("1200-game pass-aware source manifest differs from the package contract")
    if not pull_manifest.get("ok") or int(pull_manifest["games"]) != 1200 or int(pull_manifest["targetGames"]) != 1200:
        raise ValueError("completed pull manifest differs from the 1200-game package contract")

    (bundle / "scripts").mkdir(parents=True)
    shutil.copytree(SOURCE, bundle / "assets" / "source", copy_function=shutil.copy2)
    (bundle / "assets" / "pull").mkdir(parents=True)
    for name in pull_names:
        shutil.copy2(PULL / name, bundle / "assets" / "pull" / name)
    (bundle / "audit").mkdir(parents=True)
    shutil.copy2(PROFILE_AUDIT, bundle / "audit" / "oq_player_profile_index_coverage.json")
    for source, relative in scripts.items():
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    info = {
        "schema": "oq-tcn-windows-extension-1200-v1", "createdAt": datetime.now(UTC).isoformat(),
        "purpose": "prepare and return only the independent 1200-game extension after the 10000-game result has been returned",
        "requiresExistingPackage": "extracted oq_tcn_windows_9950x_data_prep_20260804_v3 server root",
        "shape": expected_shape,
        "indexContract": {
            "game": "exact game_id", "node": "exact (game_id, original move_index)",
            "moveIndexIncludesExplicitPass": True, "maxGlobalPlacementPly": 60,
        },
        "engine": {
            "hint1": "level2/no-book/1-thread/hash25/12-workers",
            "hint6": "level18/book/16-threads/hash25/12-workers/batch128/timeout900/max-attempts2",
        },
        "serverCombinesWith10000": False,
        "playerProfileMaterialization": "deferred until the base 10000 and extension 1200 NPZ files are merged locally",
        "profileNormalizationScope": "single combined 11200-game train split",
        "trainingAuthorized": False, "cudaRequired": False,
    }
    write_json(bundle / "PACKAGE_INFO.json", info)
    files = []
    for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
        files.append({"path": path.relative_to(bundle).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "schema": "oq-extension-package-manifest-v1", "files": files,
        "fileCount": len(files), "totalBytes": sum(int(item["bytes"]) for item in files),
    }
    write_json(bundle / "package_manifest.json", manifest)

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
            archive.write(path, (Path(args.name) / path.relative_to(bundle)).as_posix())
    with zipfile.ZipFile(archive_path, "r") as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise RuntimeError(f"ZIP CRC failed: {corrupt}")
    digest = sha256_file(archive_path)
    sha_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    report = {
        "schema": "oq-extension-package-build-v1", "ok": True,
        "bundle": str(bundle), "zip": str(archive_path), "zipBytes": archive_path.stat().st_size,
        "zipSha256": digest, "zipCrcOk": True, "shape": expected_shape,
    }
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
