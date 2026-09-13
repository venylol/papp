#!/usr/bin/env python3
"""Export frozen source rows for a fixed safe-hint smoke manifest.

Selected placement keys are retained together with pass rows whose immediately
preceding and following placement keys are both in the manifest.  Outputs are UTF-8,
new-only fixtures for real assembly smoke tests.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", required=True, type=Path)
    parser.add_argument("--sample-manifest", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--output-manifest", required=True, type=Path)
    args = parser.parse_args()
    source = args.source_csv.resolve()
    sample_path = args.sample_manifest.resolve()
    output = args.output_csv.resolve()
    output_manifest = args.output_manifest.resolve()
    if output.exists() or output_manifest.exists():
        raise FileExistsError("refusing to overwrite smoke subset artifacts")
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    selected = {
        (str(item["task"]["game_id"]), int(item["task"]["move_index"]))
        for item in sample["tasks"]
    }
    selected_games = {key[0] for key in selected}
    rows_by_game: dict[str, list[dict[str, str]]] = {}
    fieldnames: list[str] | None = None
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        for row in reader:
            game_id = str(row["game_id"])
            if game_id in selected_games:
                rows_by_game.setdefault(game_id, []).append(row)
    if fieldnames is None:
        raise ValueError("source has no header")
    found: set[tuple[str, int]] = set()
    retained: list[dict[str, str]] = []
    pass_rows = 0
    for game_id, rows in rows_by_game.items():
        for index, row in enumerate(rows):
            key = (game_id, int(row["move_index"]))
            if key in selected:
                retained.append(row)
                found.add(key)
                continue
            if row["actual_move"] != "-":
                continue
            previous = next(
                ((game_id, int(rows[cursor]["move_index"])) for cursor in range(index - 1, -1, -1) if rows[cursor]["actual_move"] != "-"),
                None,
            )
            following = next(
                ((game_id, int(rows[cursor]["move_index"])) for cursor in range(index + 1, len(rows)) if rows[cursor]["actual_move"] != "-"),
                None,
            )
            if previous in selected and following in selected:
                retained.append(row)
                pass_rows += 1
    missing = selected - found
    if missing:
        raise ValueError(f"sample keys missing from frozen source: {sorted(missing)[:10]}")
    retained.sort(key=lambda row: (str(row["game_id"]), int(row["move_index"])))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(retained)
    report: dict[str, Any] = {
        "schema": "egaroucid-safe-smoke-source-subset-v1",
        "sourceCsv": str(source),
        "sourceSha256": sha256_file(source),
        "sampleManifest": str(sample_path),
        "sampleSha256": sha256_file(sample_path),
        "rows": len(retained),
        "placements": len(selected),
        "passes": pass_rows,
        "games": len(selected_games),
        "outputCsv": str(output),
        "outputSha256": sha256_file(output),
    }
    output_manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
