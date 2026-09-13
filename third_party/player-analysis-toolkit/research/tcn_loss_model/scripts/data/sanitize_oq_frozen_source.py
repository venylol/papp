#!/usr/bin/env python3
"""Create a hint-free frozen OQ source copy before quarantining a dirty delivery."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import time
from pathlib import Path


SOURCE_COLUMNS = [
    "game_id", "mode", "gtype", "tcb", "created", "finalStatus",
    "move_index", "ply", "source_ply_including_pass", "global_placement_ply",
    "side_to_move", "player_id", "actual_move", "actual_thinking_time_ms",
    "board", "board_setboard", "n_legal_moves", "legal_moves", "is_pass_record",
    "split", "input_policy",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dirty-delivery", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    source = args.dirty_delivery.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite: {output}")
    (output / "handoff").mkdir(parents=True)

    raw_source = source / "handoff" / "raw_nodes_with_pass.csv"
    raw_output = output / "handoff" / "raw_nodes_with_pass.csv"
    rows = placements = passes = 0
    games = set()
    with raw_source.open("r", encoding="utf-8", newline="") as source_handle:
        reader = csv.DictReader(source_handle)
        missing = [name for name in SOURCE_COLUMNS if name not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"source columns missing: {missing}")
        with raw_output.open("w", encoding="utf-8", newline="") as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=SOURCE_COLUMNS)
            writer.writeheader()
            for row in reader:
                writer.writerow({name: row[name] for name in SOURCE_COLUMNS})
                rows += 1
                games.add(row["game_id"])
                if row["actual_move"] == "-" or row["is_pass_record"] == "1":
                    passes += 1
                else:
                    placements += 1
            output_handle.flush()
            os.fsync(output_handle.fileno())

    expected = {"rows": 609124, "placements": 599112, "passes": 10012, "games": 10000}
    actual = {"rows": rows, "placements": placements, "passes": passes, "games": len(games)}
    if actual != expected:
        raise ValueError(f"hint-free source shape mismatch: {actual} != {expected}")

    retained = [
        "handoff/games.csv", "handoff/context_metadata.csv", "handoff/split_manifest.csv",
    ]
    for relative in retained:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    shutil.copytree(source / "source_snapshot", output / "source_snapshot", copy_function=shutil.copy2)

    files = []
    for path in sorted(item for item in output.rglob("*") if item.is_file()):
        files.append({
            "path": path.relative_to(output).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    manifest = {
        "schema": "oq-frozen-10000-hint-free-source-v1",
        "createdAtUnix": time.time(),
        "sourceDirtyDelivery": str(source),
        "shape": actual,
        "rawColumns": SOURCE_COLUMNS,
        "hintColumnsPresent": False,
        "excluded": [
            "all hint1/hint6 columns", "decision_labels.csv", "model_ready",
            "dirty delivery manifests", "unsafe server analyzer scripts",
        ],
        "files": files,
    }
    (output / "SOURCE_ONLY_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
