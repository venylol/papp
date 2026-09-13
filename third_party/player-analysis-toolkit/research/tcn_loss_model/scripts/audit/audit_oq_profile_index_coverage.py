#!/usr/bin/env python3
"""Audit OQ Player snapshot-index coverage for a game cohort."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.oq_player_profile import normalize_account  # noqa: E402
from src.oq_profile_features import load_profile_snapshots  # noqa: E402


def read_games(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def players(rows: list[dict[str, str]]) -> set[str]:
    return {
        normalize_account(row[field])
        for row in rows
        for field in ("black_id", "white_id")
    }


def file_set_hash(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", required=True, type=Path)
    parser.add_argument("--baseline-games", type=Path)
    parser.add_argument("--snapshots-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    rows = read_games(args.games.resolve())
    if len({row["game_id"] for row in rows}) != len(rows):
        raise ValueError("game cohort contains duplicate game IDs")
    cohort_players = players(rows)
    baseline_players = players(read_games(args.baseline_games.resolve())) if args.baseline_games else set()
    snapshot_root = args.snapshots_dir.resolve()
    normalized_root = snapshot_root / "normalized" if (snapshot_root / "normalized").is_dir() else snapshot_root
    snapshot_files = sorted(normalized_root.rglob("*.json"), key=lambda path: path.relative_to(snapshot_root).as_posix())
    snapshots = load_profile_snapshots(snapshot_files)
    snapshot_accounts = set(snapshots)
    index_statuses: Counter[str] = Counter()
    index_path = snapshot_root / "snapshot_index.jsonl"
    if index_path.is_file():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                index_statuses[str(json.loads(line).get("status") or "unknown")] += 1
    games_with_both = sum(
        normalize_account(row["black_id"]) in snapshot_accounts
        and normalize_account(row["white_id"]) in snapshot_accounts
        for row in rows
    )
    missing = cohort_players - snapshot_accounts
    new_players = cohort_players - baseline_players if args.baseline_games else set()
    report = {
        "schema": "oq-player-profile-index-coverage-v1", "ok": True,
        "games": len(rows), "datasetAccounts": len(cohort_players),
        "snapshotAccounts": len(snapshot_accounts), "normalizedSnapshotFiles": len(snapshot_files),
        "coveredAccounts": len(cohort_players & snapshot_accounts),
        "accountCoverageRate": len(cohort_players & snapshot_accounts) / len(cohort_players),
        "missingAccounts": sorted(missing),
        "gamesWithBothProfiles": games_with_both,
        "gameCoverageRate": games_with_both / len(rows),
        "newToBaselineAccounts": len(new_players),
        "newToBaselineCovered": len(new_players & snapshot_accounts),
        "newToBaselineMissing": sorted(new_players - snapshot_accounts),
        "indexAttemptStatuses": dict(index_statuses),
        "snapshotSetSha256": file_set_hash(snapshot_root, snapshot_files),
        "missingValueContract": "unavailable profiles remain zero-valued with an explicit 31-field missing mask",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
