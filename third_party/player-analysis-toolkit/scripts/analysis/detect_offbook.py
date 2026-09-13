#!/usr/bin/env python3
"""Assign deterministic off-book labels from audited Level22 game outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOLKIT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from player_analysis_toolkit.analysis_core import (  # noqa: E402
    account_key,
    load_engine_games,
    read_json,
    write_json,
)
from player_analysis_toolkit.offbook_core import (  # noqa: E402
    ALGORITHM_LABEL,
    algorithm_contract,
    detect_target_offbook,
    fast_threshold_ms,
    finite_number,
    time_threshold_ms,
)


SCHEMA = "player-offbook-algorithm-records-v1"
def target_color_from_game(game: dict[str, Any], account: str) -> str:
    target = account_key(account)
    matches = [
        color for color in ("black", "white")
        if account_key((game.get(color) or {}).get("account")) == target
    ]
    if len(matches) == 1:
        return matches[0]
    node_colors = {
        str(node.get("playerColor") or "").lower()
        for node in game.get("nodes", [])
        if account_key(node.get("playerAccount")) == target
    }
    if len(node_colors) == 1 and next(iter(node_colors)) in {"black", "white"}:
        return next(iter(node_colors))
    raise ValueError(f"game {game.get('gameId')!r} does not map account {account!r} to exactly one color")


def detect_game(game: dict[str, Any], account: str, time_limit_ms: Any) -> dict[str, Any]:
    game_id = str(game.get("gameId") or "")
    nodes = game.get("nodes") if isinstance(game.get("nodes"), list) else []
    target_nodes: list[dict[str, Any]] = []
    target_color = target_color_from_game(game, account)

    for node in nodes:
        if account_key(node.get("playerAccount")) != account_key(account):
            continue
        target_nodes.append({
            "ply": node.get("ply"),
            "move": node.get("move"),
            "thinkingTimeMs": node.get("thinkingTimeMs"),
            "bestEval": node.get("bestEval"),
            "playerColor": node.get("playerColor"),
        })
    return detect_target_offbook(game_id, target_color, target_nodes, time_limit_ms)


def detect_all(engine_directory: Path, bundle_path: Path, account: str) -> dict[str, Any]:
    audit_path = engine_directory / "audit.json"
    if not audit_path.is_file() or read_json(audit_path).get("ok") is not True:
        raise ValueError(f"Level22 audit is missing or unsuccessful: {audit_path}")
    games = load_engine_games(engine_directory)
    game_ids = [game["gameId"] for game in games]
    if any(not game_id for game_id in game_ids) or len(set(game_ids)) != len(game_ids):
        raise ValueError("Level22 games require unique non-empty game IDs")
    bundle = read_json(bundle_path)
    details = bundle.get("details") if isinstance(bundle.get("details"), list) else []
    time_limits: dict[str, Any] = {}
    for detail in details:
        if not isinstance(detail, dict):
            continue
        game_id = str(detail.get("id") or "")
        if not game_id or game_id in time_limits:
            raise ValueError("source bundle requires unique non-empty game IDs")
        time_limits[game_id] = detail.get("tcb")
    missing_time_limits = sorted(set(game_ids) - set(time_limits))
    if missing_time_limits:
        raise ValueError(f"source bundle is missing Level22 game IDs: {missing_time_limits}")
    records = [detect_game(game, account, time_limits[game["gameId"]]) for game in games]
    records.sort(key=lambda row: row["gameId"])
    return {
        "schema": SCHEMA,
        "account": account,
        "mode": "target",
        "labeledBy": "algorithm",
        "algorithm": algorithm_contract(),
        "recordCount": len(records),
        "offBookRecordCount": sum(row["algorithmLabel"] == "offbook" for row in records),
        "noOffBookRecordCount": sum(row["algorithmLabel"] == "no_offbook" for row in records),
        "records": records,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-directory", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    value = detect_all(args.engine_directory.resolve(), args.bundle.resolve(), args.account)
    write_json(output, value)
    print(json.dumps({
        "schema": value["schema"],
        "recordCount": value["recordCount"],
        "offBookRecordCount": value["offBookRecordCount"],
        "noOffBookRecordCount": value["noOffBookRecordCount"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
