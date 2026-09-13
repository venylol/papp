"""Read-only Othello Quest Player-page query and normalization helpers.

The public web client uses Socket.IO 0.9 event ``d4b6e7ef``.  This module uses
the protocol's HTTP long-polling transport so it does not need a login, browser,
or third-party Socket.IO package.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCRIPT_VERSION = "1.0.0"
INTERFACE_VERSION = "oq-player-page-d4b6e7ef-socket.io-0.9"
DEFAULT_SOCKET_IO_URL = "http://questgames.net:3002/socket.io/1/"
PLAYER_EVENT = "d4b6e7ef"
DEFAULT_GTYPE = "reversi"
PROFILE_SCHEMA = "oq-player-profile-snapshot-v1"
RAW_SCHEMA = "oq-player-profile-raw-response-v1"
REQUIRED_CATEGORIES = (("teban", "sente"), ("teban", "gote"), ("opp", "strong"), ("opp", "weak"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_account(value: Any) -> str:
    """Normalize an account for case-insensitive matching while retaining raw IDs elsewhere."""
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, not bool")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is missing or not an integer: {value!r}") from exc
    if number < 0:
        raise ValueError(f"{field} must be nonnegative: {number}")
    return number


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is missing or not numeric: {value!r}") from exc
    if not (number >= 0 and number < float("inf")):
        raise ValueError(f"{field} must be finite and nonnegative: {number}")
    return number


def _record(record: dict[str, Any], location: str) -> dict[str, Any]:
    win = _integer(record.get("win"), f"{location}.win")
    loss = _integer(record.get("loss"), f"{location}.loss")
    draw = _integer(record.get("draw"), f"{location}.draw")
    output = {
        "win": win,
        "loss": loss,
        "draw": draw,
        "played": win + loss + draw,
        "rating": _number(record.get("rating"), f"{location}.rating"),
    }
    return output


def normalize_profile_response(
    response: dict[str, Any],
    *,
    requested_account: str,
    fetched_at_utc: str,
    gtype: str = DEFAULT_GTYPE,
    raw_response_sha256: str | None = None,
) -> dict[str, Any]:
    """Turn one raw Player-page response into the stable auditable snapshot schema."""
    if not isinstance(response, dict):
        raise ValueError("Player response must be a JSON object")
    if response.get("error"):
        raise ValueError(f"OQ Player query failed: {response['error']}")
    returned_id = str(response.get("id") or "").strip()
    returned_name = str(response.get("name") or "").strip()
    if not returned_id:
        raise ValueError("Player response has no id")
    if normalize_account(returned_id) != normalize_account(requested_account):
        raise ValueError(
            f"Player response account mismatch: requested={requested_account!r}, returned={returned_id!r}"
        )
    returned_gtype = str(response.get("gtype") or gtype).strip()
    if returned_gtype != gtype:
        raise ValueError(f"Player response gtype mismatch: expected={gtype!r}, returned={returned_gtype!r}")
    win = _integer(response.get("win"), "win")
    loss = _integer(response.get("loss"), "loss")
    draw = _integer(response.get("draw"), "draw")
    computed_played = win + loss + draw
    raw_played = response.get("played")
    played = computed_played if raw_played is None else _integer(raw_played, "played")
    if played != computed_played:
        raise ValueError(f"played != win + loss + draw: {played} != {computed_played}")

    raw_srecords = response.get("srecords")
    if raw_srecords is None:
        raw_srecords = []
    if not isinstance(raw_srecords, list) or not all(isinstance(item, dict) for item in raw_srecords):
        raise ValueError("srecords must be a list of objects")
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates: list[str] = []
    for index, item in enumerate(raw_srecords):
        category = normalize_account(item.get("category"))
        name = normalize_account(item.get("name"))
        key = (category, name)
        if key in by_key:
            duplicates.append(f"{category}/{name}")
            continue
        by_key[key] = _record(item, f"srecords[{index}]")
    if duplicates:
        raise ValueError(f"duplicate Player category records: {duplicates}")
    categories: dict[str, dict[str, Any] | None] = {}
    missing_categories: list[str] = []
    for category, name in REQUIRED_CATEGORIES:
        label = name
        categories[label] = by_key.get((category, name))
        if categories[label] is None:
            missing_categories.append(f"{category}/{name}")

    return {
        "schema": PROFILE_SCHEMA,
        "script_version": SCRIPT_VERSION,
        "interface_version": INTERFACE_VERSION,
        "query_event": PLAYER_EVENT,
        "query_gtype": gtype,
        "requested_account": requested_account,
        "normalized_account": normalize_account(requested_account),
        "id": returned_id,
        "normalized_id": normalize_account(returned_id),
        "name": returned_name,
        "gtype": returned_gtype,
        "rating": _number(response.get("rating"), "rating"),
        "high": _number(response.get("high"), "high"),
        "win": win,
        "loss": loss,
        "draw": draw,
        "played": played,
        "played_source": "response" if raw_played is not None else "computed_win_loss_draw",
        "sente": categories["sente"],
        "gote": categories["gote"],
        "strong": categories["strong"],
        "weak": categories["weak"],
        "missing_categories": missing_categories,
        "last": response.get("last"),
        "profile_fetched_at_utc": fetched_at_utc,
        "raw_response_sha256": raw_response_sha256 or canonical_json_sha256(response),
    }


def _decode_socket_payload(body: str) -> list[str]:
    """Decode Socket.IO 0.9 single packets or U+FFFD length-framed payloads."""
    if not body.startswith("\ufffd"):
        return [body]
    packets: list[str] = []
    offset = 0
    while offset < len(body):
        if body[offset] != "\ufffd":
            raise RuntimeError("malformed Socket.IO 0.9 payload framing")
        end = body.find("\ufffd", offset + 1)
        if end < 0:
            raise RuntimeError("unterminated Socket.IO 0.9 payload length")
        size_text = body[offset + 1:end]
        if not size_text.isdigit():
            raise RuntimeError(f"invalid Socket.IO 0.9 payload length: {size_text!r}")
        size = int(size_text)
        start = end + 1
        packets.append(body[start:start + size])
        offset = start + size
    return packets


@dataclass
class SocketIO09XHRClient:
    endpoint: str = DEFAULT_SOCKET_IO_URL
    timeout: float = 20.0
    user_agent: str = f"tcn-loss-model-oq-player-profile/{SCRIPT_VERSION}"

    def __post_init__(self) -> None:
        self.endpoint = self.endpoint.rstrip("/") + "/"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        parsed = urllib.parse.urlsplit(self.endpoint)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"

    def _request(self, url: str, data: bytes | None = None) -> str:
        headers = {
            "Accept": "*/*",
            "Origin": self.origin,
            "User-Agent": self.user_agent,
        }
        if data is not None:
            headers["Content-Type"] = "text/plain;charset=UTF-8"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        with self.opener.open(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8")

    def query_player(self, account: str, gtype: str = DEFAULT_GTYPE) -> dict[str, Any]:
        query_account = normalize_account(account)
        if not query_account:
            raise ValueError("Player account is empty")
        stamp = int(time.time() * 1000)
        handshake = self._request(f"{self.endpoint}?t={stamp}")
        parts = handshake.split(":", 3)
        if len(parts) != 4 or not parts[0]:
            raise RuntimeError(f"invalid Socket.IO 0.9 handshake: {handshake!r}")
        sid, _heartbeat, _close, transports = parts
        if "xhr-polling" not in transports.split(","):
            raise RuntimeError(f"server did not offer xhr-polling: {transports}")
        polling = f"{self.endpoint}xhr-polling/{urllib.parse.quote(sid, safe='')}"
        initial = self._request(f"{polling}?t={int(time.time() * 1000)}")
        if not any(packet.startswith("1::") for packet in _decode_socket_payload(initial)):
            raise RuntimeError(f"Socket.IO transport did not open: {initial!r}")
        event = {
            "name": PLAYER_EVENT,
            "args": [{"id": query_account, "gtype": gtype}],
        }
        packet = "5:::" + json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        acknowledgement = self._request(
            f"{polling}?t={int(time.time() * 1000)}",
            packet.encode("utf-8"),
        )
        if acknowledgement.strip() not in {"1", "ok"}:
            raise RuntimeError(f"Socket.IO event POST was not acknowledged: {acknowledgement!r}")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            body = self._request(f"{polling}?t={int(time.time() * 1000)}")
            for item in _decode_socket_payload(body):
                if item.startswith("2::"):
                    self._request(
                        f"{polling}?t={int(time.time() * 1000)}",
                        "2::".encode("utf-8"),
                    )
                    continue
                if not item.startswith("5:::"):
                    continue
                envelope = json.loads(item[4:])
                if envelope.get("name") != PLAYER_EVENT:
                    continue
                args = envelope.get("args")
                if not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], dict):
                    raise RuntimeError(f"unexpected Player event response: {envelope!r}")
                return args[0]
        raise TimeoutError(f"timed out waiting for {PLAYER_EVENT} response for {account!r}")


def _account_from_mapping(value: dict[str, Any]) -> str | None:
    for key in ("account", "oq_account", "player_id", "id", "name"):
        candidate = str(value.get(key) or "").strip()
        if candidate:
            return candidate
    return None


def accounts_from_file(path: Path) -> list[str]:
    """Read UTF-8 CSV, JSON, JSONL, or one-account-per-line text."""
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            return []
        header = [normalize_account(value) for value in rows[0]]
        paired_columns = [header.index(key) for key in ("black_id", "white_id") if key in header]
        if paired_columns:
            values = [
                row[column].strip()
                for row in rows[1:]
                for column in paired_columns
                if len(row) > column and row[column].strip()
            ]
            return deduplicate_accounts(values)
        preferred = next((header.index(key) for key in ("account", "oq_account", "player_id", "id", "name") if key in header), None)
        start = 1 if preferred is not None else 0
        column = preferred if preferred is not None else 0
        values = [row[column].strip() for row in rows[start:] if len(row) > column and row[column].strip()]
    elif suffix == ".json":
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(document, dict):
            for key in ("accounts", "players", "items"):
                if isinstance(document.get(key), list):
                    document = document[key]
                    break
            else:
                document = list(document.values())
        if not isinstance(document, list):
            raise ValueError("JSON account input must be a list or contain accounts/players/items")
        values = []
        for item in document:
            if isinstance(item, str):
                values.append(item.strip())
            elif isinstance(item, dict):
                account = _account_from_mapping(item)
                if account:
                    values.append(account)
    elif suffix in {".jsonl", ".ndjson"}:
        values = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            account = item.strip() if isinstance(item, str) else _account_from_mapping(item) if isinstance(item, dict) else None
            if account:
                values.append(account)
    else:
        values = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return deduplicate_accounts(values)


def deduplicate_accounts(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        key = normalize_account(clean)
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def safe_account_stem(account: str) -> str:
    normalized = normalize_account(account)
    readable = re.sub(r"[^0-9a-z_-]+", "_", normalized).strip("_")[:40] or "account"
    return f"{readable}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:10]}"
