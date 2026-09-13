#!/usr/bin/env python3
"""Verify every immutable input file listed by a server handoff manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    root = args.bundle_root.resolve()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    checked = 0
    checked_bytes = 0
    for item in manifest["files"]:
        path = root / Path(item["path"])
        if not path.is_file():
            raise FileNotFoundError(f"package file is missing: {item['path']}")
        size = path.stat().st_size
        if size != int(item["bytes"]):
            raise ValueError(f"package file size differs: {item['path']}")
        if sha256_file(path) != item["sha256"]:
            raise ValueError(f"package file hash differs: {item['path']}")
        checked += 1
        checked_bytes += size
    print(json.dumps({"ok": True, "files": checked, "bytes": checked_bytes}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
