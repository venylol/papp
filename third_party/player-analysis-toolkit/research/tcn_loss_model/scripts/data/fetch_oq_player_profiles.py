#!/usr/bin/env python3
"""Fetch auditable, read-only Othello Quest Player-page profile snapshots."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.oq_player_profile import (
    DEFAULT_GTYPE,
    DEFAULT_SOCKET_IO_URL,
    INTERFACE_VERSION,
    PLAYER_EVENT,
    RAW_SCHEMA,
    SCRIPT_VERSION,
    SocketIO09XHRClient,
    accounts_from_file,
    canonical_json_sha256,
    deduplicate_accounts,
    normalize_account,
    normalize_profile_response,
    safe_account_stem,
    utc_now,
)


def write_new_json(path: Path, payload: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing snapshot: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def successful_accounts(index_path: Path) -> set[str]:
    if not index_path.exists():
        return set()
    completed: set[str] = set()
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") == "ok":
            completed.add(str(row.get("normalized_account") or ""))
    return completed


def timestamp_stem(value: str) -> str:
    return value.replace("-", "").replace(":", "").replace(".", "").replace("Z", "Z")


def collect_accounts(args: argparse.Namespace) -> list[str]:
    values = list(args.account or [])
    if args.input:
        values.extend(accounts_from_file(args.input))
    accounts = deduplicate_accounts(values)
    if not accounts:
        raise ValueError("provide at least one --account or --input")
    return accounts


def fetch(args: argparse.Namespace) -> dict[str, Any]:
    accounts = collect_accounts(args)
    output_dir = args.output_dir.resolve()
    index_path = output_dir / "snapshot_index.jsonl"
    completed = successful_accounts(index_path) if args.resume else set()
    if output_dir.exists() and any(output_dir.iterdir()) and not args.resume:
        raise FileExistsError(f"output directory is not empty; use --resume: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"ok": 0, "failed": 0, "skipped": 0, "requested": len(accounts), "failures": []}
    write_lock = threading.Lock()

    def fetch_one(account: str) -> dict[str, Any]:
        normalized = normalize_account(account)
        last_error: BaseException | None = None
        last_raw_path: Path | None = None
        for attempt in range(1, args.retries + 2):
            fetched_at = utc_now()
            try:
                client = SocketIO09XHRClient(args.endpoint, args.timeout)
                response = client.query_player(account, args.gtype)
                response_hash = canonical_json_sha256(response)
                stamp = timestamp_stem(fetched_at)
                stem = safe_account_stem(account)
                raw_path = output_dir / "raw" / f"{stem}__{stamp}.json"
                normalized_path = output_dir / "normalized" / f"{stem}__{stamp}.json"
                raw_envelope = {
                    "schema": RAW_SCHEMA,
                    "script_version": SCRIPT_VERSION,
                    "interface_version": INTERFACE_VERSION,
                    "query_event": PLAYER_EVENT,
                    "query_gtype": args.gtype,
                    "requested_account": account,
                    "normalized_account": normalized,
                    "profile_fetched_at_utc": fetched_at,
                    "raw_response_sha256": response_hash,
                    "response": response,
                }
                write_new_json(raw_path, raw_envelope)
                last_raw_path = raw_path
                profile = normalize_profile_response(
                    response,
                    requested_account=account,
                    fetched_at_utc=fetched_at,
                    gtype=args.gtype,
                    raw_response_sha256=response_hash,
                )
                write_new_json(normalized_path, profile)
                row = {
                    "status": "ok", "requested_account": account, "normalized_account": normalized,
                    "profile_fetched_at_utc": fetched_at, "raw_response_sha256": response_hash,
                    "raw_path": str(raw_path), "normalized_path": str(normalized_path),
                    "attempt": attempt,
                }
                with write_lock:
                    append_jsonl(index_path, row)
                last_error = None
                return row
            except BaseException as exc:
                last_error = exc
                if attempt <= args.retries:
                    delay = args.retry_delay * (2 ** (attempt - 1))
                    time.sleep(delay)
        if last_error is not None:
            failed_at = utc_now()
            failure = {
                "status": "failed", "requested_account": account, "normalized_account": normalized,
                "failed_at_utc": failed_at, "query_gtype": args.gtype,
                "interface_version": INTERFACE_VERSION, "script_version": SCRIPT_VERSION,
                "error_type": type(last_error).__name__, "error": str(last_error),
                "last_raw_path": str(last_raw_path) if last_raw_path else None,
            }
            failure_path = output_dir / "failures" / f"{safe_account_stem(account)}__{timestamp_stem(failed_at)}.json"
            write_new_json(failure_path, failure)
            row = {**failure, "failure_path": str(failure_path)}
            with write_lock:
                append_jsonl(index_path, row)
            return row
        raise AssertionError("unreachable fetch state")

    pending = [account for account in accounts if normalize_account(account) not in completed]
    summary["skipped"] = len(accounts) - len(pending)
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = {}
        for position, account in enumerate(pending):
            futures[executor.submit(fetch_one, account)] = account
            if position + 1 < len(pending):
                time.sleep(max(0.0, args.min_interval) + random.uniform(0.0, max(0.0, args.jitter)))
        for future in as_completed(futures):
            row = future.result()
            if row["status"] == "ok":
                summary["ok"] += 1
            else:
                summary["failed"] += 1
                summary["failures"].append({"account": row["requested_account"], "error": row["error"]})
    summary.update({
        "output_dir": str(output_dir), "index": str(index_path),
        "gtype": args.gtype, "interface_version": INTERFACE_VERSION,
        "script_version": SCRIPT_VERSION,
    })
    return summary


def live_smoke(args: argparse.Namespace) -> dict[str, Any]:
    accounts = ("hero9", "xiaojianbao")
    results = []
    for index, account in enumerate(accounts):
        fetched_at = utc_now()
        response = SocketIO09XHRClient(args.endpoint, args.timeout).query_player(account, DEFAULT_GTYPE)
        profile = normalize_profile_response(response, requested_account=account, fetched_at_utc=fetched_at)
        if profile["gtype"] != DEFAULT_GTYPE or profile["rating"] < 0:
            raise AssertionError(f"basic live invariant failed for {account}")
        if profile["played"] != profile["win"] + profile["loss"] + profile["draw"]:
            raise AssertionError(f"overall record invariant failed for {account}")
        missing = [name for name in ("sente", "gote", "strong", "weak") if profile[name] is None]
        if missing:
            raise AssertionError(f"required live categories missing for {account}: {missing}")
        for name in ("sente", "gote", "strong", "weak"):
            record = profile[name]
            if record["played"] != record["win"] + record["loss"] + record["draw"]:
                raise AssertionError(f"category invariant failed for {account}/{name}")
        results.append({
            "account": account, "returned_id": profile["id"], "returned_name": profile["name"],
            "gtype": profile["gtype"], "rating_nonnegative": True,
            "played_identity": True, "categories": ["sente", "gote", "strong", "weak"],
            "raw_response_sha256": profile["raw_response_sha256"],
        })
        if index == 0:
            time.sleep(max(0.0, args.min_interval))
    return {
        "ok": True, "schema": "oq-player-profile-live-smoke-v1", "checked_at_utc": utc_now(),
        "numeric_values_are_not_fixed_assertions": True, "results": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_parser = commands.add_parser("fetch", help="fetch one or more Player snapshots")
    fetch_parser.add_argument("--account", action="append", default=[])
    fetch_parser.add_argument("--input", type=Path, help="UTF-8 CSV/JSON/JSONL/text account list")
    fetch_parser.add_argument("--output-dir", type=Path, required=True)
    fetch_parser.add_argument("--gtype", default=DEFAULT_GTYPE)
    fetch_parser.add_argument("--endpoint", default=DEFAULT_SOCKET_IO_URL)
    fetch_parser.add_argument("--timeout", type=float, default=20.0)
    fetch_parser.add_argument("--retries", type=int, default=2)
    fetch_parser.add_argument("--retry-delay", type=float, default=1.0)
    fetch_parser.add_argument("--min-interval", type=float, default=1.0)
    fetch_parser.add_argument("--jitter", type=float, default=0.25)
    fetch_parser.add_argument("--concurrency", type=int, default=1)
    fetch_parser.add_argument("--resume", action="store_true")
    fetch_parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="record unavailable profiles but return success after all requested accounts were attempted",
    )
    smoke = commands.add_parser("live-smoke", help="explicit network smoke for hero9 and xiaojianbao")
    smoke.add_argument("--endpoint", default=DEFAULT_SOCKET_IO_URL)
    smoke.add_argument("--timeout", type=float, default=20.0)
    smoke.add_argument("--min-interval", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = fetch(args) if args.command == "fetch" else live_smoke(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("failed", 0) == 0 or getattr(args, "allow_failures", False) else 2
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
