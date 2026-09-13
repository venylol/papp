#!/usr/bin/env python3
"""PAPP mapping helper.

This helper contains only the local WeChat and account-validation operations
used by the PAPP competition-management frontend:

* read a decrypted WeChat contact database and refresh a group-nickname cache;
* read recent messages from one selected, decrypted WeChat group;
* validate OQ accounts against the public Othello Quest game-history endpoint.

It intentionally does not import or start an Agent/FTD workflow.  The WeChat
database resolver honors an explicit contact database override, otherwise
refreshes the configured live database (including WAL) through the existing
decrypt cache. Static decrypted databases are used only in offline mode.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
# Bundled Python's isolated ._pth omits the script directory.
sys.path.insert(0, str(ROOT))
CACHE_DIR = ROOT / "agent_cache"
MAP_SCHEMA_VERSION = 1
DEFAULT_GROUP_TEMPLATE = "【{month}月无差别组】栢龙杯棋王赛"
CHINA_TZ = timezone(timedelta(hours=8))
OQ_MODE_ENDPOINTS = {
    "1min": "reversi1",
    "5min": "reversi",
    "xot": "reversix",
}
OQ_VALIDATION_MODE_ORDER = ["5min", "1min", "xot"]
OQ_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_]{1,14}$")


class MappingError(RuntimeError):
    """Expected, user-actionable mapping failure."""


def default_group_name(now: datetime | None = None) -> str:
    current = now or datetime.now()
    return DEFAULT_GROUP_TEMPLATE.format(month=current.month)


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", "")).strip()


def safe_filename(value: str) -> str:
    text = normalize_text(value) or "wechat-group"
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:120] or "wechat-group"


def cache_path_for_group(group_name: str) -> Path:
    return CACHE_DIR / f"{safe_filename(group_name)}.member-map.json"


def _config_path() -> Path:
    configured_root = normalize_text(os.environ.get("WECHAT_DECRYPT_APP_DIR"))
    base = Path(configured_root) if configured_root else ROOT
    return base / "config.json"


def _read_wechat_config() -> dict[str, Any]:
    raw: dict[str, Any] = {}
    config_path = _config_path()
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError) as exc:
            raise MappingError(f"无法读取微信解密配置：{config_path}：{exc}") from exc

    return raw


def _configured_decrypted_dir() -> Path | None:
    raw = _read_wechat_config()
    config_path = _config_path()
    env_dir = normalize_text(os.environ.get("PAPP_WECHAT_DECRYPTED_DIR"))
    configured = env_dir or normalize_text(raw.get("decrypted_dir"))
    if not configured:
        configured = "decrypted"
    path = Path(configured)
    if not path.is_absolute():
        path = config_path.parent / path
    return path


def contact_db_path() -> Path:
    """Resolve a local decrypted contact.db without requiring FTD/Agent code."""

    explicit = normalize_text(
        os.environ.get("PAPP_WECHAT_CONTACT_DB")
        or os.environ.get("WECHAT_CONTACT_DB")
    )
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise MappingError(f"指定的微信 contact.db 不存在：{path}")
        return path

    # A static export is a snapshot, even when it is still readable. When a
    # live source is configured, only its DB/WAL-aware cache can refresh it.
    if normalize_text(_read_wechat_config().get("db_dir")):
        try:
            import mcp_server  # type: ignore

            cached = mcp_server._cache.get(os.path.join("contact", "contact.db"))
        except Exception as exc:
            raise MappingError(f"刷新微信联系人数据库失败：{exc}") from exc
        if cached and Path(cached).is_file():
            return Path(cached)
        raise MappingError("无法刷新微信联系人数据库；请检查微信数据库路径和解密密钥。")

    candidates: list[Path] = []

    decrypted_dir = _configured_decrypted_dir()
    if decrypted_dir:
        candidates.append(decrypted_dir / "contact" / "contact.db")
    candidates.append(ROOT / "decrypted" / "contact" / "contact.db")

    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.normpath(str(candidate)))
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.is_file():
            return candidate

    attempted = "\n".join(f"- {item}" for item in candidates)
    raise MappingError(
        "无法找到已解密 contact.db；请先完成微信数据库解密/刷新。\n"
        f"已检查：\n{attempted}\n"
        "也可以通过 PAPP_WECHAT_CONTACT_DB 明确指定 contact.db。"
    )


def group_display_name(row: sqlite3.Row) -> str:
    return normalize_text(row["remark"] or row["nick_name"] or row["username"])


def list_groups() -> list[dict[str, Any]]:
    db_path = contact_db_path()
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT c.id, c.username, c.local_type, c.remark, c.nick_name
            FROM contact c
            WHERE c.username LIKE '%@chatroom%' OR c.local_type = 2
            ORDER BY c.id
            """
        ).fetchall()

    groups: list[dict[str, Any]] = []
    for row in rows:
        username = normalize_text(row["username"])
        if "@chatroom" not in username:
            continue
        groups.append(
            {
                "room_id": int(row["id"]),
                "username": username,
                "display_name": group_display_name(row),
                "remark": normalize_text(row["remark"]),
                "nick_name": normalize_text(row["nick_name"]),
            }
        )
    return groups


def resolve_group_strict(group_name: str) -> dict[str, Any]:
    query = normalize_text(group_name)
    if not query:
        raise MappingError("群名不能为空。")

    groups = list_groups()
    if "@chatroom" in query:
        exact_user = [item for item in groups if item["username"] == query]
        if len(exact_user) == 1:
            return exact_user[0]
        raise MappingError(f"找不到群 username：{query}")

    exact = [item for item in groups if item["display_name"] == query]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise MappingError(
            "群名精确匹配到多个会话，请改用 @chatroom username：\n"
            + "\n".join(f"- {item['display_name']} ({item['username']})" for item in exact)
        )

    fuzzy = [item for item in groups if query in item["display_name"]]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        raise MappingError(
            "群名模糊匹配到多个会话，不能静默选择。请指定更完整群名或 @chatroom username：\n"
            + "\n".join(f"- {item['display_name']} ({item['username']})" for item in fuzzy)
        )
    raise MappingError(f"找不到群聊：{query}")


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    start = pos
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, pos
        shift += 7
        if shift > 63:
            break
    raise MappingError(f"protobuf varint 解析失败，offset={start}")


def iter_protobuf_fields(data: bytes):
    pos = 0
    length = len(data)
    while pos < length:
        tag, pos = read_varint(data, pos)
        field_no = tag >> 3
        wire_type = tag & 0x07
        if field_no <= 0:
            raise MappingError(f"protobuf 字段号异常：{field_no}")

        if wire_type == 0:
            value, pos = read_varint(data, pos)
            yield field_no, wire_type, value
        elif wire_type == 1:
            if pos + 8 > length:
                raise MappingError("protobuf fixed64 越界")
            yield field_no, wire_type, data[pos : pos + 8]
            pos += 8
        elif wire_type == 2:
            size, pos = read_varint(data, pos)
            if size < 0 or pos + size > length:
                raise MappingError("protobuf length-delimited 越界")
            yield field_no, wire_type, data[pos : pos + size]
            pos += size
        elif wire_type == 5:
            if pos + 4 > length:
                raise MappingError("protobuf fixed32 越界")
            yield field_no, wire_type, data[pos : pos + 4]
            pos += 4
        else:
            raise MappingError(f"不支持的 protobuf wire type：{wire_type}")


def decode_utf8(value: Any) -> str:
    if not isinstance(value, (bytes, bytearray)):
        return ""
    try:
        return bytes(value).decode("utf-8").strip()
    except UnicodeDecodeError:
        return ""


def looks_like_wechat_username(value: str) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    if "@chatroom" in text or text.startswith(("wxid_", "gh_")):
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9_.\-]{5,64}", text))


def parse_member_records_from_ext_buffer(ext_buffer: bytes) -> list[dict[str, str]]:
    """Parse chat_room.ext_buffer's repeated username/group-nick records."""

    records: list[dict[str, str]] = []
    for field_no, wire_type, value in iter_protobuf_fields(ext_buffer or b""):
        if field_no != 1 or wire_type != 2 or not isinstance(value, bytes):
            continue

        username = ""
        group_nick = ""
        try:
            for sub_no, sub_wire, sub_value in iter_protobuf_fields(value):
                if sub_wire != 2:
                    continue
                text = decode_utf8(sub_value)
                if sub_no == 1:
                    username = text
                elif sub_no == 2:
                    group_nick = text
        except MappingError:
            continue

        username = normalize_text(username)
        group_nick = normalize_text(group_nick)
        if looks_like_wechat_username(username) and group_nick:
            records.append({"username": username, "group_nick": group_nick})
    return records


def load_room_member_rows(room_id: int) -> dict[str, dict[str, Any]]:
    db_path = contact_db_path()
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT cm.room_id, cm.member_id, c.username, c.local_type, c.alias,
                   c.remark, c.nick_name, c.quan_pin, c.remark_quan_pin
            FROM chatroom_member cm
            JOIN contact c ON c.id = cm.member_id
            WHERE cm.room_id = ?
            """,
            (room_id,),
        ).fetchall()

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        username = normalize_text(row["username"])
        if not username:
            continue
        display = normalize_text(row["remark"] or row["nick_name"] or row["alias"] or username)
        out[username] = {
            "member_id": int(row["member_id"]),
            "username": username,
            "contact_display": display,
            "contact_remark": normalize_text(row["remark"]),
            "contact_nick_name": normalize_text(row["nick_name"]),
            "alias": normalize_text(row["alias"]),
            "local_type": int(row["local_type"] or 0),
        }
    return out


def load_room_ext_buffer(room_id: int) -> bytes:
    db_path = contact_db_path()
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT ext_buffer FROM chat_room WHERE id = ?",
            (room_id,),
        ).fetchone()
    if not row or not row["ext_buffer"]:
        raise MappingError(f"群 room_id={room_id} 没有 chat_room.ext_buffer，无法提取群昵称映射。")
    return bytes(row["ext_buffer"])


def load_message_sender_map(room_id: int) -> dict[str, dict[str, str]]:
    """Return the current group nickname for each WeChat username."""
    members = load_room_member_rows(room_id)
    by_username: dict[str, dict[str, str]] = {
        username: {
            "username": username,
            "group_nick": "",
            "contact_display": normalize_text(member.get("contact_display")),
        }
        for username, member in members.items()
    }
    try:
        ext_buffer = load_room_ext_buffer(room_id)
    except MappingError:
        # A missing nickname buffer should leave sender identity available so
        # the frontend can record an unmatched check-in as pending.
        return by_username

    conflicts: set[str] = set()
    for record in parse_member_records_from_ext_buffer(ext_buffer):
        username = record["username"]
        group_nick = record["group_nick"]
        current = by_username.setdefault(
            username,
            {"username": username, "group_nick": "", "contact_display": ""},
        )
        if current["group_nick"] and current["group_nick"] != group_nick:
            conflicts.add(username)
            continue
        current["group_nick"] = group_nick

    for username in conflicts:
        by_username[username]["group_nick"] = ""
    return by_username


def resolve_chat_message_sender(
    real_sender_id: Any,
    sender_from_content: Any,
    id_to_username: dict[int, str],
    members_by_username: dict[str, dict[str, str]],
) -> dict[str, str]:
    try:
        sender_id = int(real_sender_id)
    except (TypeError, ValueError):
        sender_id = 0
    username = normalize_text(id_to_username.get(sender_id, "")) if sender_id else ""
    if not username:
        username = normalize_text(sender_from_content)

    member = members_by_username.get(username, {})
    return {
        "username": username,
        "group_nick": normalize_text(member.get("group_nick")),
        "contact_display": normalize_text(member.get("contact_display")),
    }


def build_member_map(group_name: str) -> dict[str, Any]:
    group = resolve_group_strict(group_name)
    member_rows = load_room_member_rows(group["room_id"])
    records = parse_member_records_from_ext_buffer(load_room_ext_buffer(group["room_id"]))

    by_username: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for record in records:
        username = record["username"]
        group_nick = record["group_nick"]
        existing = by_username.get(username)
        if existing and existing["group_nick"] != group_nick:
            conflicts.append(
                {"username": username, "values": sorted({existing["group_nick"], group_nick})}
            )
            continue

        contact = member_rows.get(username, {})
        by_username[username] = {
            "username": username,
            "group_nick": group_nick,
            "contact_display": contact.get("contact_display", ""),
            "contact_remark": contact.get("contact_remark", ""),
            "contact_nick_name": contact.get("contact_nick_name", ""),
            "alias": contact.get("alias", ""),
            "member_id": contact.get("member_id"),
            "in_chatroom_member_table": username in member_rows,
        }

    if conflicts:
        raise MappingError(
            "群昵称映射存在冲突，拒绝生成缓存：\n"
            + json.dumps(conflicts[:20], ensure_ascii=False, indent=2)
        )

    member_count = len(member_rows)
    mapped_count = len(by_username)
    if mapped_count == 0:
        raise MappingError("未从 chat_room.ext_buffer 解析到任何群昵称映射。")
    if member_count and mapped_count < max(1, int(member_count * 0.5)):
        raise MappingError(f"群昵称映射数量异常：成员表 {member_count} 人，映射 {mapped_count} 人。")

    missing_from_ext = sorted(set(member_rows) - set(by_username))
    extra_in_ext = sorted(set(by_username) - set(member_rows))
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "group_query": group_name,
        "group_name": group["display_name"],
        "room_username": group["username"],
        "room_id": group["room_id"],
        "refreshed_at": datetime.now().isoformat(timespec="seconds"),
        "member_count": member_count,
        "mapped_count": mapped_count,
        "missing_from_ext_count": len(missing_from_ext),
        "extra_in_ext_count": len(extra_in_ext),
        "missing_from_ext": missing_from_ext[:100],
        "extra_in_ext": extra_in_ext[:100],
        "members": sorted(by_username.values(), key=lambda item: item["group_nick"].casefold()),
    }


def write_member_map(payload: dict[str, Any]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path_for_group(str(payload["group_name"]))
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def compact_member_map(payload: dict[str, Any]) -> dict[str, Any]:
    members = payload.get("members") if isinstance(payload.get("members"), list) else []
    seen: set[str] = set()
    group_nicks: list[str] = []
    raw_nicks = payload.get("groupNicks") if isinstance(payload.get("groupNicks"), list) else []
    for raw in [*raw_nicks, *members]:
        value = raw.get("group_nick") if isinstance(raw, dict) else raw
        nick = normalize_text(value)
        if nick and nick not in seen:
            seen.add(nick)
            group_nicks.append(nick)
    group_nicks.sort(key=lambda value: value.casefold())
    return {
        "ok": True,
        "groupName": normalize_text(payload.get("group_name") or payload.get("group_query")),
        "roomUsername": normalize_text(payload.get("room_username")),
        "refreshedAt": normalize_text(payload.get("refreshed_at")),
        "memberCount": int(payload.get("member_count") or len(members) or len(group_nicks)),
        "mappedCount": int(payload.get("mapped_count") or len(group_nicks)),
        "groupNicks": group_nicks,
    }


def parse_local_time(value: Any) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("T", " ").replace("Z", "+00:00"))
    except ValueError as exc:
        raise MappingError(f"时间格式无效：{value}") from exc


def oq_created_local(value: Any) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(CHINA_TZ).replace(tzinfo=None)
    except ValueError:
        return None


class DirectOQClient:
    def __init__(self, base_url: str = "http://questgames.net", timeout: int = 20) -> None:
        self.base_url = str(base_url or "http://questgames.net").rstrip("/")
        self.timeout = max(1, int(timeout or 20))

    def fetch_games(self, account: str, mode: str) -> list[dict[str, Any]]:
        endpoint = OQ_MODE_ENDPOINTS.get(mode, OQ_MODE_ENDPOINTS["5min"])
        account_text = str(account or "").strip().lower()
        url = f"{self.base_url}/games/{endpoint}/{urllib.parse.quote(account_text, safe='')}.json"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "papp-local-oq-validation/1.0", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MappingError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise MappingError(str(exc.reason or exc)) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise MappingError(str(exc)) from exc

        games = payload.get("games") if isinstance(payload, dict) else payload
        return [item for item in games if isinstance(item, dict)] if isinstance(games, list) else []


def validation_modes(primary_mode: str) -> list[str]:
    first = primary_mode if primary_mode in OQ_MODE_ENDPOINTS else "5min"
    return [first, *[mode for mode in OQ_VALIDATION_MODE_ORDER if mode != first]]


def validate_one_oq_account(
    account: str,
    primary_mode: str,
    base_url: str,
    timeout: int,
    start: datetime | None,
    end: datetime | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    clean = normalize_text(account)
    if not clean:
        return {
            "account": clean,
            "ok": False,
            "status": "invalid",
            "mode": "",
            "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
            "totalGames": 0,
            "windowGames": 0,
            "error": "OQ account is empty",
        }
    if not OQ_ACCOUNT_RE.fullmatch(clean):
        return {
            "account": clean,
            "ok": False,
            "status": "invalid",
            "mode": "",
            "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
            "totalGames": 0,
            "windowGames": 0,
            "error": "OQ account must be 1-14 ASCII letters, digits, or underscores",
        }

    errors: list[str] = []
    for mode in validation_modes(primary_mode):
        try:
            games = DirectOQClient(base_url, timeout).fetch_games(clean, mode)
        except MappingError as exc:
            errors.append(f"{mode}: {exc}")
            continue
        if not games:
            errors.append(f"{mode}: no game history")
            continue
        window_games = 0
        if start and end:
            for game in games:
                created = oq_created_local(game.get("created") or game.get("createdAt"))
                if created and start <= created <= end:
                    window_games += 1
        return {
            "account": clean,
            "ok": True,
            "status": "ok",
            "mode": mode,
            "primaryMode": primary_mode,
            "fallbackUsed": mode != primary_mode,
            "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
            "totalGames": len(games),
            "windowGames": window_games,
            "error": "",
        }

    return {
        "account": clean,
        "ok": False,
        "status": "invalid",
        "mode": "",
        "primaryMode": primary_mode,
        "fallbackUsed": False,
        "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
        "totalGames": 0,
        "windowGames": 0,
        "error": "; ".join(errors) or "OQ account has no game history",
    }


def validate_oq_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    raw_accounts = payload.get("accounts") if isinstance(payload, dict) else []
    if not isinstance(raw_accounts, list):
        raise MappingError("accounts 必须是数组。")

    accounts: list[str] = []
    seen: set[str] = set()
    for raw in raw_accounts:
        account = normalize_text(raw)
        key = account.casefold()
        if account and key not in seen:
            accounts.append(account)
            seen.add(key)

    mode = normalize_text(payload.get("mode")) or "5min"
    if mode not in OQ_MODE_ENDPOINTS:
        mode = "5min"
    base_url = normalize_text(payload.get("baseUrl")) or "http://questgames.net"
    timeout = max(1, min(120, int(payload.get("timeout") or 20)))
    concurrency = max(1, min(32, int(payload.get("concurrency") or 8)))
    start = parse_local_time(payload.get("fromTime") or payload.get("fromLocal"))
    end = parse_local_time(payload.get("toTime") or payload.get("toLocal"))
    if (start is None) != (end is None):
        raise MappingError("fromTime 和 toTime 必须同时提供。")
    if start and end and end < start:
        raise MappingError("OQ 校验时间范围的结束时间不能早于开始时间。")

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    if accounts:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(
                    validate_one_oq_account,
                    account,
                    mode,
                    base_url,
                    timeout,
                    start,
                    end,
                ): account
                for account in accounts
            }
            for future in as_completed(futures):
                results.append(future.result())
    results.sort(key=lambda item: str(item.get("account") or "").casefold())
    checked_at = datetime.now(CHINA_TZ).isoformat(timespec="seconds")
    return {
        "ok": True,
        "action": "validate-oq-accounts",
        "mode": mode,
        "baseUrl": base_url,
        "concurrency": concurrency,
        "checkedAt": checked_at,
        "wallMs": round((time.perf_counter() - started) * 1000, 1),
        "checkedCount": len(results),
        "okCount": sum(1 for item in results if item.get("ok") is True),
        "invalidCount": sum(1 for item in results if item.get("ok") is not True),
        "window": {
            "fromLocal": start.isoformat(sep=" ") if start else "",
            "toLocal": end.isoformat(sep=" ") if end else "",
        },
        "results": results,
        "byAccount": {
            str(item.get("account") or "").casefold(): item for item in results
        },
    }


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def read_payload(args: argparse.Namespace) -> dict[str, Any]:
    raw = str(args.accounts_json or "").strip()
    if not raw:
        raw = sys.stdin.read().strip()
    if not raw:
        return {"accounts": []}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MappingError(f"OQ 校验输入不是有效 JSON：{exc}") from exc
    if isinstance(parsed, list):
        return {"accounts": parsed}
    if isinstance(parsed, dict):
        return parsed
    raise MappingError("OQ 校验输入必须是 JSON 数组或对象。")


def cmd_refresh_map(args: argparse.Namespace) -> int:
    query = normalize_text(args.group) or default_group_name()
    payload = build_member_map(query)
    path = write_member_map(payload)
    compact = compact_member_map(payload)
    print_json(
        {
            **compact,
            "action": "refresh-map",
            "cachePath": str(path),
            "missingFromExtCount": payload["missing_from_ext_count"],
            "extraInExtCount": payload["extra_in_ext_count"],
        }
    )
    return 0


def cmd_list_groups(args: argparse.Namespace) -> int:
    groups = list_groups()
    query = normalize_text(args.query)
    if query:
        groups = [
            item
            for item in groups
            if query in item["display_name"] or query in item["username"]
        ]
    print_json({"ok": True, "groups": groups})
    return 0


def extract_wechat_solitaire_text(xml_content: str) -> str:
    """Return the visible relay body stored in a WeChat solitaire app message."""
    if not isinstance(xml_content, str) or not xml_content.strip():
        return ""

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return ""

    appmsg = root.find("./appmsg")
    if appmsg is None or normalize_text(appmsg.findtext("type")) != "53":
        return ""
    return str(appmsg.findtext("title") or "").strip()


def cmd_recent_chat_messages(args: argparse.Namespace) -> int:
    group = resolve_group_strict(args.group)
    try:
        import mcp_server  # type: ignore
    except Exception as exc:
        raise MappingError(f"微信聊天记录读取组件不可用：{exc}") from exc

    context = mcp_server._resolve_chat_context(group["username"])
    if not context or not context.get("is_group"):
        raise MappingError("所选会话不是微信群聊。")

    members_by_username = load_message_sender_map(group["room_id"])

    relay_only = bool(getattr(args, "relay_only", False))
    limit = max(1, min(2000, int(args.limit)))
    offset_limit = 1_000_000 if relay_only else 10_000
    offset = max(0, min(offset_limit, int(args.offset)))
    start_time = args.start_time
    end_time = args.end_time
    if start_time is not None and start_time < 0:
        raise MappingError("start-time 必须是非负整数秒时间戳")
    if end_time is not None and end_time < 0:
        raise MappingError("end-time 必须是非负整数秒时间戳")
    if start_time is not None and end_time is not None and start_time > end_time:
        raise MappingError("start-time 不能晚于 end-time")
    query_limit = limit + offset
    type_filter = [(53 << 32) | 49] if relay_only else None
    messages: list[dict[str, Any]] = []
    for table in context.get("message_tables", []):
        with closing(sqlite3.connect(str(table["db_path"]))) as conn:
            id_to_username = mcp_server._load_name2id_maps(conn)
            rows = mcp_server._query_messages(
                conn,
                table["table_name"],
                start_ts=start_time,
                end_ts=end_time,
                limit=query_limit,
                type_filter=type_filter,
            )

        for row in rows:
            local_id, local_type, create_time, real_sender_id, content, compressed = row
            try:
                base_type, subtype = mcp_server._split_msg_type(local_type)
                if relay_only and (base_type != 49 or subtype != 53):
                    continue
                timestamp = int(create_time or 0)
                text = ""
                sender_from_content = ""
                if base_type == 1 or (base_type == 49 and subtype == 53):
                    decoded = mcp_server._decompress_content(content, compressed)
                    sender_from_content, decoded = mcp_server._parse_message_content(
                        decoded,
                        local_type,
                        True,
                    )
                    if base_type == 1:
                        text = decoded
                    else:
                        text = extract_wechat_solitaire_text(decoded)
                if relay_only and not text:
                    continue
                shard_name = Path(str(table["db_path"])).name
                message_id = (
                    f"{shard_name}:{table['table_name']}:{timestamp}:{int(local_id or 0)}"
                )
                sender = resolve_chat_message_sender(
                    real_sender_id,
                    sender_from_content,
                    id_to_username,
                    members_by_username,
                )
                messages.append(
                    {
                        "messageId": message_id,
                        "createTime": timestamp,
                        "localId": int(local_id or 0),
                        "type": int(base_type),
                        "content": str(text or ""),
                        "senderUsername": sender["username"],
                        "senderGroupNick": sender["group_nick"],
                        "senderDisplayName": sender["contact_display"],
                        "sender": sender["group_nick"] or sender["contact_display"],
                    }
                )
            except Exception as exc:
                raise MappingError(
                    f"解析微信群消息失败（local_id={local_id}, create_time={create_time}）：{exc}"
                ) from exc

    messages.sort(
        key=lambda item: (item["createTime"], item["messageId"]),
        reverse=True,
    )
    print_json(
        {
            "ok": True,
            "groupName": group["display_name"],
            "roomUsername": group["username"],
            "messages": messages[offset : offset + limit],
        }
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    query = normalize_text(args.group) or default_group_name()
    group = resolve_group_strict(query)
    path = cache_path_for_group(group["display_name"])
    result: dict[str, Any] = {
        "ok": True,
        "groupName": group["display_name"],
        "roomUsername": group["username"],
        "cachePath": str(path),
        "cacheExists": path.exists(),
    }
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        result.update(compact_member_map(payload))
    print_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PAPP local mapping helper")
    parser.add_argument("--group", default="", help="群名或 @chatroom username")
    sub = parser.add_subparsers(dest="command", required=True)

    refresh = sub.add_parser("refresh-map", help="刷新并缓存微信群昵称")
    refresh.set_defaults(func=cmd_refresh_map)

    groups = sub.add_parser("list-groups", help="列出本机已解密的微信群")
    groups.add_argument("--query", default="", help="按群名或 username 过滤")
    groups.set_defaults(func=cmd_list_groups)

    history = sub.add_parser("recent-chat-messages", help="读取选定微信群的近期消息")
    history.add_argument("--limit", type=int, default=200, help="最多返回的消息数量（1-2000）")
    history.add_argument("--offset", type=int, default=0, help="从最新消息向前跳过的数量")
    history.add_argument("--start-time", type=int, default=None, help="只读取此 Unix 秒时间戳之后的消息（含）")
    history.add_argument("--end-time", type=int, default=None, help="只读取此 Unix 秒时间戳之前的消息（含）")
    history.add_argument("--relay-only", action="store_true", help="只读取微信群接龙应用消息，分页上限提高")
    history.set_defaults(func=cmd_recent_chat_messages)

    status = sub.add_parser("status", help="查看群昵称缓存状态")
    status.set_defaults(func=cmd_status)

    validate = sub.add_parser("validate-oq-accounts", help="校验 OQ 账号")
    validate.add_argument("--accounts-json", default="", help="账号 JSON；省略时从 stdin 读取")
    validate.set_defaults(func=lambda args: _cmd_validate(args))
    return parser


def _cmd_validate(args: argparse.Namespace) -> int:
    result = validate_oq_accounts(read_payload(args))
    print_json(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except MappingError as exc:
        print_json({"ok": False, "error": str(exc)})
        return 2
    except Exception as exc:  # noqa: BLE001
        print_json({"ok": False, "error": f"未预期错误：{exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
