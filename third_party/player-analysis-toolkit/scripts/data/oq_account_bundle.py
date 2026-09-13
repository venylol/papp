from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODE_ENDPOINTS = {
    "1min": "reversi1",
    "5min": "reversi",
    "xot": "reversix",
}

DEFAULT_TIMEOUT = 20
DEFAULT_CONCURRENCY = 20
DEFAULT_MAX_ATTEMPTS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_new_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Any, lock: threading.Lock | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if lock is None:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return
    with lock:
        append_jsonl(path, value)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row is not an object: {path}:{line_number}")
            rows.append(value)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_json(base_url: str, path: str, timeout: int) -> Any:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Accept": "application/json", "User-Agent": "player-analysis-toolkit/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def account_key(value: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value or "").casefold())


def name_key(value: Any) -> str:
    return " ".join(re.findall(r"[0-9a-z]+", str(value or "").casefold()))


def public_games(base_url: str, account: str, mode: str, timeout: int) -> list[dict[str, Any]]:
    endpoint = MODE_ENDPOINTS[mode]
    account_path = urllib.parse.quote(account.strip().lower())
    payload = get_json(base_url, f"/games/{endpoint}/{account_path}.json", timeout)
    games = payload.get("games") if isinstance(payload, dict) else payload
    if not isinstance(games, list) or not all(isinstance(item, dict) for item in games):
        raise RuntimeError("OQ public account endpoint did not return a game list")
    ids = [str(item.get("id") or "").strip() for item in games]
    if not all(ids) or len(ids) != len(set(ids)):
        raise RuntimeError("OQ public account endpoint returned missing or duplicate game IDs")
    return games


def fetch_detail(base_url: str, game_id: str, timeout: int) -> dict[str, Any]:
    payload = get_json(base_url, f"/game/{urllib.parse.quote(game_id)}.json", timeout)
    if not isinstance(payload, dict) or payload.get("error"):
        raise RuntimeError(f"OQ game detail unavailable for {game_id}")
    if str(payload.get("id") or "").strip() != game_id:
        raise RuntimeError(f"OQ game detail ID mismatch for {game_id}")
    return payload


def run_dynamic(
    items: list[str],
    worker: Any,
    workers: int,
) -> list[tuple[str, Any]]:
    """Keep a bounded worker pool full and return results in completion order."""
    iterator = iter(items)
    results: list[tuple[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        pending: dict[Any, str] = {}

        def submit_next() -> None:
            try:
                item = next(iterator)
            except StopIteration:
                return
            pending[executor.submit(worker, item)] = item

        for _ in range(max(1, workers)):
            submit_next()
        while pending:
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                item = pending.pop(future)
                results.append((item, future.result()))
                submit_next()
    return results


def acquisition_paths(output_path: Path) -> dict[str, Path]:
    directory = output_path.parent / "acquisition"
    return {
        "directory": directory,
        "manifest": directory / "batch_manifest.json",
        "gameList": directory / "game_list.json",
        "listAttempts": directory / "game_list_attempts.jsonl",
        "details": directory / "game_details.jsonl",
        "detailAttempts": directory / "game_detail_attempts.jsonl",
        "report": output_path.parent / "acquisition_report.json",
    }


def load_or_create_manifest(
    paths: dict[str, Path],
    account: str,
    mode: str,
    base_url: str,
    timeout: int,
    concurrency: int,
    max_attempts: int,
) -> dict[str, Any]:
    path = paths["manifest"]
    if path.is_file():
        manifest = read_json(path)
        if manifest.get("schema") != "oq-account-bundle-one-shot-batch-v1":
            raise ValueError(f"unsupported acquisition batch manifest schema: {path}")
        if manifest.get("account") != account or manifest.get("mode") != mode:
            raise ValueError("existing acquisition batch belongs to a different account or mode")
        policy = manifest.get("networkPolicy") or {}
        expected = {
            "baseUrl": base_url,
            "requestTimeoutSeconds": timeout,
            "workers": concurrency,
            "maximumAttemptsPerRequest": max_attempts,
        }
        actual = {key: policy.get(key) for key in expected}
        if actual != expected:
            raise ValueError(f"acquisition batch network policy changed: expected={expected} actual={actual}")
        return manifest

    manifest = {
        "schema": "oq-account-bundle-one-shot-batch-v1",
        "batchId": f"oq-account-{account_key(account) or 'unknown'}-{mode}",
        "account": account,
        "mode": mode,
        "createdAtUtc": utc_now(),
        "networkPolicy": {
            "baseUrl": base_url,
            "requestTimeoutSeconds": timeout,
            "workers": concurrency,
            "maximumAttemptsPerRequest": max_attempts,
            "resumePolicy": "resume only missing list/detail terminal records; never refetch terminal items",
        },
        "stages": {},
        "paths": {
            "gameList": str(paths["gameList"].resolve()),
            "listAttempts": str(paths["listAttempts"].resolve()),
            "details": str(paths["details"].resolve()),
            "detailAttempts": str(paths["detailAttempts"].resolve()),
        },
    }
    save_manifest(paths["manifest"], manifest)
    return manifest


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["updatedAtUtc"] = utc_now()
    atomic_write_json(path, manifest)


def acquire_game_list(
    base_url: str,
    account: str,
    mode: str,
    timeout: int,
    max_attempts: int,
    paths: dict[str, Path],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    stage = manifest["stages"].get("gameList") or {}
    list_path = paths["gameList"]
    if list_path.is_file():
        cached = read_json(list_path)
        games = cached.get("games")
        if not isinstance(games, list) or not all(isinstance(item, dict) for item in games):
            raise ValueError(f"cached account game list is invalid: {list_path}")
        stage.update({
            "complete": True,
            "resumedFromCheckpoint": True,
            "listedGameCount": len(games),
            "gameListSha256": sha256_file(list_path),
        })
        manifest["stages"]["gameList"] = stage
        save_manifest(paths["manifest"], manifest)
        return games

    stage.setdefault("startedAtUtc", utc_now())
    stage["maximumAttempts"] = max_attempts
    manifest["stages"]["gameList"] = stage
    save_manifest(paths["manifest"], manifest)
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        at = utc_now()
        try:
            games = public_games(base_url, account, mode, timeout)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            append_jsonl(paths["listAttempts"], {
                "atUtc": at, "attempt": attempt, "ok": False, "error": last_error,
            })
            continue
        append_jsonl(paths["listAttempts"], {
            "atUtc": at, "attempt": attempt, "ok": True, "gameCount": len(games),
        })
        atomic_write_json(list_path, {
            "schema": "oq-account-game-list-checkpoint-v1",
            "fetchedAtUtc": utc_now(),
            "account": account,
            "mode": mode,
            "sourceUrl": f"{base_url.rstrip('/')}/games/{MODE_ENDPOINTS[mode]}/{urllib.parse.quote(account.strip().lower())}.json",
            "games": games,
        })
        stage.update({
            "complete": True,
            "completedAtUtc": utc_now(),
            "attempts": attempt,
            "listedGameCount": len(games),
            "gameListSha256": sha256_file(list_path),
            "attemptLogSha256": sha256_file(paths["listAttempts"]),
        })
        manifest["stages"]["gameList"] = stage
        save_manifest(paths["manifest"], manifest)
        return games

    stage.update({
        "complete": False,
        "failedAtUtc": utc_now(),
        "attempts": max_attempts,
        "error": last_error,
        "attemptLogSha256": sha256_file(paths["listAttempts"]),
    })
    manifest["stages"]["gameList"] = stage
    save_manifest(paths["manifest"], manifest)
    raise RuntimeError(f"account game list failed after finite retries: {last_error}")


def detail_is_valid(account: str, detail: dict[str, Any]) -> tuple[bool, str]:
    try:
        validate_account_participation(account, [detail])
    except RuntimeError as exc:
        return False, str(exc)
    return True, "ok"


def fetch_detail_terminal(
    base_url: str,
    account: str,
    game_id: str,
    timeout: int,
    max_attempts: int,
    previous_attempts: int,
    detail_path: Path,
    attempt_path: Path,
    write_lock: threading.Lock,
) -> dict[str, Any]:
    attempts = previous_attempts
    last_error = ""
    while attempts < max_attempts:
        attempts += 1
        at = utc_now()
        try:
            detail = fetch_detail(base_url, game_id, timeout)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            append_jsonl(attempt_path, {
                "atUtc": at, "gameId": game_id, "attempt": attempts,
                "ok": False, "error": last_error,
            }, write_lock)
            continue

        valid, reason = detail_is_valid(account, detail)
        append_jsonl(attempt_path, {
            "atUtc": at, "gameId": game_id, "attempt": attempts,
            "ok": True, "valid": valid, "reason": reason,
        }, write_lock)
        terminal = {
            "gameId": game_id,
            "status": "success" if valid else "invalid",
            "ok": valid,
            "valid": valid,
            "attempts": attempts,
            "fetchedAtUtc": utc_now(),
            "reason": reason,
            "detail": detail,
        }
        append_jsonl(detail_path, terminal, write_lock)
        return terminal

    terminal = {
        "gameId": game_id,
        "status": "failed",
        "ok": False,
        "valid": False,
        "attempts": attempts,
        "fetchedAtUtc": utc_now(),
        "reason": "request_failed_after_finite_retries",
        "error": last_error,
    }
    append_jsonl(detail_path, terminal, write_lock)
    return terminal


def validate_account_participation(account: str, details: list[dict[str, Any]]) -> None:
    target = account_key(account)
    for detail in details:
        players = detail.get("players") if isinstance(detail.get("players"), list) else []
        keys = {
            account_key(player.get("id") or player.get("name"))
            for player in players
            if isinstance(player, dict)
        }
        if len(players) != 2 or target not in keys:
            raise RuntimeError(f"target account is not one of two players in game {detail.get('id')}")


def acquire_details(
    base_url: str,
    account: str,
    games: list[dict[str, Any]],
    timeout: int,
    concurrency: int,
    max_attempts: int,
    paths: dict[str, Path],
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    for path_key in ("details", "detailAttempts"):
        paths[path_key].parent.mkdir(parents=True, exist_ok=True)
        paths[path_key].touch(exist_ok=True)

    requested_ids = [str(item["id"]).strip() for item in games]
    terminal_by_id = {
        str(row.get("gameId") or ""): row
        for row in read_jsonl(paths["details"])
        if row.get("gameId")
    }
    attempts_by_id: dict[str, int] = {}
    for row in read_jsonl(paths["detailAttempts"]):
        game_id = str(row.get("gameId") or "")
        if game_id:
            attempts_by_id[game_id] = max(attempts_by_id.get(game_id, 0), int(row.get("attempt") or 0))

    stage = manifest["stages"].get("gameDetails") or {}
    stage.setdefault("startedAtUtc", utc_now())
    stage.update({
        "requestedGameCount": len(requested_ids),
        "maximumAttemptsPerGame": max_attempts,
        "workers": concurrency,
    })
    manifest["stages"]["gameDetails"] = stage
    save_manifest(paths["manifest"], manifest)

    pending = [game_id for game_id in requested_ids if game_id not in terminal_by_id]
    write_lock = threading.Lock()

    def acquire_one(game_id: str) -> dict[str, Any]:
        return fetch_detail_terminal(
            base_url, account, game_id, timeout, max_attempts,
            attempts_by_id.get(game_id, 0), paths["details"], paths["detailAttempts"], write_lock,
        )

    for game_id, terminal in run_dynamic(pending, acquire_one, concurrency):
        terminal_by_id[game_id] = terminal

    missing = [game_id for game_id in requested_ids if game_id not in terminal_by_id]
    if missing:
        raise RuntimeError(f"detail batch ended without terminal records: {missing[:10]}")

    failures = [
        game_id for game_id in requested_ids
        if not terminal_by_id[game_id].get("valid")
    ]
    details = [
        terminal_by_id[game_id]["detail"]
        for game_id in requested_ids
        if terminal_by_id[game_id].get("valid") and isinstance(terminal_by_id[game_id].get("detail"), dict)
    ]
    stage.update({
        "complete": True,
        "completedAtUtc": utc_now(),
        "terminalGameCount": len(requested_ids),
        "successCount": len(details),
        "failureCount": len(failures),
        "failureGameIds": failures,
        "detailSnapshotSha256": sha256_file(paths["details"]),
        "detailAttemptLogSha256": sha256_file(paths["detailAttempts"]),
    })
    manifest["stages"]["gameDetails"] = stage
    save_manifest(paths["manifest"], manifest)
    return details, terminal_by_id, failures


def mapping_by_name(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = state.get("ftdPlayerAccountMapping")
    if not isinstance(raw, dict):
        raise RuntimeError("shared state has no ftdPlayerAccountMapping object")
    account_index = raw.get("accountIndex")
    rows = account_index if isinstance(account_index, dict) else raw
    result: dict[str, dict[str, Any]] = {}
    for map_name, row in rows.items():
        if not isinstance(row, dict):
            continue
        for candidate in (map_name, row.get("ftdName"), row.get("displayName")):
            key = name_key(candidate)
            if key:
                result[key] = row
    return result


def pairing_game_id(row: dict[str, Any]) -> str:
    source_key = str(row.get("sourceMessageKey") or "")
    if source_key.startswith("oq-auto:id:"):
        return source_key.removeprefix("oq-auto:id:").strip()
    audit = row.get("oqAutoAudit") if isinstance(row.get("oqAutoAudit"), dict) else {}
    game = audit.get("game") if isinstance(audit.get("game"), dict) else {}
    return str(game.get("gameId") or row.get("oqGameId") or "").strip()


def tournament_games(
    state: dict[str, Any],
    account: str,
    available_ids: set[str],
    reported_opponents: set[str],
) -> list[dict[str, Any]]:
    score_helper = state.get("scoreHelper") if isinstance(state.get("scoreHelper"), dict) else {}
    rounds = score_helper.get("rounds") if isinstance(score_helper.get("rounds"), list) else []
    mappings = mapping_by_name(state)
    target = account_key(account)
    output: list[dict[str, Any]] = []
    for round_item in rounds:
        if not isinstance(round_item, dict):
            continue
        pairings = round_item.get("ftdPairings") if isinstance(round_item.get("ftdPairings"), list) else []
        for row in pairings:
            if not isinstance(row, dict):
                continue
            black_name = str(row.get("black") or "").strip()
            white_name = str(row.get("white") or "").strip()
            black_map = mappings.get(name_key(black_name), {})
            white_map = mappings.get(name_key(white_name), {})
            black_account = str(black_map.get("account") or "").strip()
            white_account = str(white_map.get("account") or "").strip()
            if target not in {account_key(black_account), account_key(white_account)}:
                continue
            if not black_account or not white_account:
                raise RuntimeError(
                    f"missing mapped OQ account for round {round_item.get('round')} table {row.get('table')}"
                )
            game_id = pairing_game_id(row)
            if not game_id:
                raise RuntimeError(
                    f"missing OQ game ID for round {round_item.get('round')} table {row.get('table')}"
                )
            if game_id not in available_ids:
                raise RuntimeError(f"tournament game {game_id} is absent from the fetched account bundle")
            opponent = white_account if account_key(black_account) == target else black_account
            output.append(
                {
                    "round": int(round_item.get("round") or 0),
                    "stage": str(round_item.get("stage") or ""),
                    "table": int(row.get("table") or 0),
                    "oqGameId": game_id,
                    "ftdBlack": black_name,
                    "ftdWhite": white_name,
                    "ftdBlackAccount": black_account,
                    "ftdWhiteAccount": white_account,
                    "ftdStage": str(row.get("ftdStage") or ""),
                    "ftdRound": row.get("ftdRound"),
                    "ftdTable": row.get("ftdTable"),
                    "reported": account_key(opponent) in reported_opponents,
                    "reportedOpponentAccount": opponent if account_key(opponent) in reported_opponents else "",
                }
            )
    ids = [item["oqGameId"] for item in output]
    if not output or len(ids) != len(set(ids)):
        raise RuntimeError("target tournament pairings are missing or contain duplicate OQ game IDs")
    output.sort(key=lambda item: (item["round"], item["table"]))
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a public OQ account bundle and optionally annotate local tournament pairings."
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--mode", choices=sorted(MODE_ENDPOINTS), default="5min")
    parser.add_argument("--output", required=True, help="New account bundle JSON path")
    parser.add_argument("--base-url", default="http://questgames.net")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--state", default="", help="Optional shared checkin-state.json")
    parser.add_argument("--tournament-output", default="", help="New tournament bundle JSON path")
    parser.add_argument("--reported-opponent", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_path = Path(args.output).resolve()
    tournament_path = Path(args.tournament_output).resolve() if args.tournament_output else None
    if output_path.exists() or (tournament_path and tournament_path.exists()):
        raise FileExistsError("refusing to overwrite an existing requested output")
    if args.timeout <= 0 or args.concurrency <= 0 or args.max_attempts <= 0:
        raise ValueError("timeout, concurrency, and max-attempts must be positive")

    paths = acquisition_paths(output_path)
    manifest = load_or_create_manifest(
        paths, args.account, args.mode, args.base_url,
        args.timeout, args.concurrency, args.max_attempts,
    )
    games = acquire_game_list(
        args.base_url, args.account, args.mode, args.timeout, args.max_attempts,
        paths, manifest,
    )
    details, terminal_by_id, failure_ids = acquire_details(
        args.base_url, args.account, games, args.timeout, args.concurrency,
        args.max_attempts, paths, manifest,
    )
    validate_account_participation(args.account, details)
    fetched_at = datetime.now(timezone.utc).isoformat()
    failure_records = [
        {
            "gameId": game_id,
            "status": terminal_by_id[game_id].get("status"),
            "attempts": terminal_by_id[game_id].get("attempts"),
            "reason": terminal_by_id[game_id].get("reason"),
            "error": terminal_by_id[game_id].get("error"),
        }
        for game_id in failure_ids
    ]
    invalid_ids = [
        game_id for game_id in failure_ids
        if terminal_by_id[game_id].get("status") == "invalid"
    ]
    transport_failure_ids = [
        game_id for game_id in failure_ids
        if terminal_by_id[game_id].get("status") == "failed"
    ]
    coverage_warning = None
    if len(details) < len(games):
        coverage_warning = (
            f"only {len(details)} of {len(games)} listed games have usable details; "
            f"terminal failures or invalid details: {len(failure_ids)}"
        )
    acquisition = {
        "schema": "oq-account-bundle-acquisition-v1",
        "batchId": manifest["batchId"],
        "batchManifest": str(paths["manifest"].resolve()),
        "report": str(paths["report"].resolve()),
        "listedGameCount": len(games),
        "detailTerminalGameCount": len(games),
        "detailFetchedGameCount": len(details),
        "detailFailureGameCount": len(failure_ids),
        "detailFailureGameIds": failure_ids,
        "invalidDetailGameIds": invalid_ids,
        "transportFailureGameIds": transport_failure_ids,
        "coverageStatus": "complete" if not coverage_warning else "partial",
        "coverageWarning": coverage_warning,
    }
    bundle = {
        "schema": "oq-public-account-bundle-v1",
        "scope": "public-account-endpoint-exposed-games",
        "account": args.account,
        "mode": args.mode,
        "fetchedAt": fetched_at,
        "sourceUrl": (
            f"{args.base_url.rstrip('/')}/games/{MODE_ENDPOINTS[args.mode]}/"
            f"{urllib.parse.quote(args.account.strip().lower())}.json"
        ),
        "index": games,
        "details": details,
        "acquisition": acquisition,
    }
    tournament_bundle = None
    if tournament_path:
        if not args.state:
            raise ValueError("--state is required with --tournament-output")
        state_path = Path(args.state).resolve()
        pairings = tournament_games(
            read_json(state_path),
            args.account,
            {str(item["id"]) for item in games},
            {account_key(value) for value in args.reported_opponent},
        )
        tournament_bundle = {
            "schema": "oq-local-tournament-account-games-v1",
            "account": args.account,
            "sourceState": str(state_path),
            "createdAt": fetched_at,
            "games": pairings,
        }
    report = {
        "schema": "oq-account-bundle-acquisition-report-v1",
        "createdAtUtc": utc_now(),
        "account": args.account,
        "mode": args.mode,
        "batchId": manifest["batchId"],
        "listedGameCount": len(games),
        "detailTerminalGameCount": len(games),
        "detailFetchedGameCount": len(details),
        "detailFailureGameCount": len(failure_ids),
        "detailFailureGameIds": failure_ids,
        "invalidDetailGameIds": invalid_ids,
        "transportFailureGameIds": transport_failure_ids,
        "coverageStatus": "complete" if not coverage_warning else "partial",
        "coverageWarning": coverage_warning,
        "failureRecords": failure_records,
        "batchManifest": str(paths["manifest"].resolve()),
        "gameListCheckpoint": str(paths["gameList"].resolve()),
        "detailCheckpoint": str(paths["details"].resolve()),
        "detailAttemptLog": str(paths["detailAttempts"].resolve()),
        "bundle": str(output_path.resolve()),
    }
    write_new_json(output_path, bundle)
    atomic_write_json(paths["report"], report)
    manifest["stages"]["bundle"] = {
        "complete": True,
        "completedAtUtc": utc_now(),
        "listedGameCount": len(games),
        "detailFetchedGameCount": len(details),
        "detailFailureGameCount": len(failure_ids),
        "bundleSha256": sha256_file(output_path),
        "reportSha256": sha256_file(paths["report"]),
    }
    save_manifest(paths["manifest"], manifest)
    if tournament_path and tournament_bundle is not None:
        write_new_json(tournament_path, tournament_bundle)
    print(
        json.dumps(
            {
                "ok": True,
                "account": args.account,
                "mode": args.mode,
                "gameCount": len(games),
                "detailCount": len(details),
                "detailFailureCount": len(failure_ids),
                "detailFailureGameIds": failure_ids,
                "coverageStatus": "complete" if not coverage_warning else "partial",
                "coverageWarning": coverage_warning,
                "bundle": str(output_path),
                "batchManifest": str(paths["manifest"]),
                "acquisitionReport": str(paths["report"]),
                "tournamentGameCount": len(tournament_bundle["games"]) if tournament_bundle else 0,
                "reportedGameCount": (
                    sum(bool(item["reported"]) for item in tournament_bundle["games"])
                    if tournament_bundle
                    else 0
                ),
                "tournamentBundle": str(tournament_path) if tournament_path else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
