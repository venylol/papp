#!/usr/bin/env python3
"""Create the audited Windows-server return ZIP for one prepared extension cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-dir", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-zip", required=True, type=Path)
    parser.add_argument("--expected-games", required=True, type=int)
    parser.add_argument("--expected-rows", required=True, type=int)
    parser.add_argument("--expected-placements", required=True, type=int)
    parser.add_argument("--expected-passes", required=True, type=int)
    args = parser.parse_args()

    attempt = args.attempt_dir.resolve()
    source = args.source_dir.resolve()
    archive_path = args.output_zip.resolve()
    sha_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    return_manifest_path = attempt / "RETURN_PACKAGE_MANIFEST.json"
    for path in (archive_path, sha_path, return_manifest_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
    hint1 = load_json(attempt / "work" / "hint1" / "audit.json")
    hint6 = load_json(attempt / "work" / "hint6" / "audit.json")
    assembly = load_json(attempt / "results" / "assembled" / "assembly_manifest.json")
    validation = load_json(attempt / "results" / "model_ready" / "server_final_validation.json")
    if not hint1.get("ok") or int(hint1["rows"]) != args.expected_placements:
        raise ValueError("hint1 audit gate failed")
    if not hint6.get("ok") or int(hint6["rows"]) != args.expected_placements:
        raise ValueError("hint6 audit gate failed")
    if (
        assembly.get("status") != "complete"
        or (int(assembly["games"]), int(assembly["rows"]), int(assembly["placements"]), int(assembly["passes"]))
        != (args.expected_games, args.expected_rows, args.expected_placements, args.expected_passes)
    ):
        raise ValueError("assembled extension shape gate failed")
    if not validation.get("ok") or int(validation["inputFeatures"]) != 362 or int(validation["boardChannels"]) != 23:
        raise ValueError("model-ready extension validation gate failed")

    roots = [(source, Path("source")), (attempt, Path("prepared_extension"))]
    files: list[dict[str, object]] = []
    for root, prefix in roots:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path == return_manifest_path:
                continue
            relative = (prefix / path.relative_to(root)).as_posix()
            files.append({"source": str(path), "path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "schema": "oq-extension-server-return-v1", "ok": True,
        "createdAt": datetime.now(UTC).isoformat(), "trainingStarted": False, "cudaUsed": False,
        "games": args.expected_games, "rows": args.expected_rows,
        "placements": args.expected_placements, "passes": args.expected_passes,
        "modelReady": "prepared_extension/results/model_ready/model_ready_1200.npz",
        "assembledRaw": "prepared_extension/results/assembled/raw_nodes_with_pass_safe_hints.csv",
        "files": [{key: value for key, value in item.items() if key != "source"} for item in files],
    }
    return_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files.append({
        "source": str(return_manifest_path), "path": "prepared_extension/RETURN_PACKAGE_MANIFEST.json",
        "bytes": return_manifest_path.stat().st_size, "sha256": sha256_file(return_manifest_path),
    })
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for item in files:
            archive.write(str(item["source"]), str(item["path"]))
    with zipfile.ZipFile(archive_path, "r") as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise RuntimeError(f"ZIP CRC failed: {corrupt}")
    digest = sha256_file(archive_path)
    sha_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    print(json.dumps({"ok": True, "zip": str(archive_path), "bytes": archive_path.stat().st_size, "sha256": digest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
