#!/usr/bin/env python3
"""Query the small OQ Player profile confirmation payload used by PAPP."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.oq_player_profile import (  # noqa: E402
    DEFAULT_GTYPE,
    DEFAULT_SOCKET_IO_URL,
    SocketIO09XHRClient,
    normalize_account,
)


def number(value: Any, field: str) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric, not bool")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is missing or not numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
    return int(parsed) if parsed.is_integer() else parsed


def integer(value: Any, field: str) -> int:
    parsed = number(value, field)
    if isinstance(parsed, float) or int(parsed) != parsed:
        raise ValueError(f"{field} must be an integer")
    return int(parsed)


def query(account: str, endpoint: str, timeout: float) -> dict[str, Any]:
    requested = str(account or "").strip()
    normalized = normalize_account(requested)
    if not normalized:
        raise ValueError("OQ ID 不能为空")

    response = SocketIO09XHRClient(endpoint, timeout).query_player(
        requested, DEFAULT_GTYPE
    )
    if response.get("error"):
        raise ValueError(str(response["error"]))

    returned_id = str(response.get("id") or "").strip()
    if not returned_id:
        raise ValueError("查询结果没有 OQ ID")
    if normalize_account(returned_id) != normalized:
        raise ValueError("查询结果的 OQ ID 与输入不一致")

    win = integer(response.get("win"), "win")
    loss = integer(response.get("loss"), "loss")
    draw = integer(response.get("draw"), "draw")
    played = integer(response.get("played"), "played") if response.get("played") is not None else win + loss + draw
    if played != win + loss + draw:
        raise ValueError("总局数与胜负和不一致")

    return {
        "ok": True,
        "profile": {
            "id": returned_id,
            "name": str(response.get("name") or "").strip(),
            "rating": number(response.get("rating"), "rating"),
            "high": number(response.get("high"), "high"),
            "hiddenR": number(response.get("hiddenR"), "hiddenR"),
            "played": played,
            "win": win,
            "loss": loss,
            "draw": draw,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--endpoint", default=DEFAULT_SOCKET_IO_URL)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    try:
        print(json.dumps(query(args.account, args.endpoint, args.timeout), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
