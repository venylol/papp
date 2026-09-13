#!/usr/bin/env python3
"""Extract authoritative game/player metadata from an OQ account bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--model-ready-npz", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite: {args.output}")
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    rows = []
    for item in bundle["index"]:
        players = item.get("players") or []
        if len(players) != 2:
            raise ValueError(f"game {item.get('id')} does not have exactly two players")
        rows.append({
            "game_id": str(item["id"]), "created": str(item["created"]),
            "black_id": str(players[0]["id"]), "white_id": str(players[1]["id"]),
        })
    frame = pd.DataFrame(rows)
    if frame["game_id"].duplicated().any():
        raise ValueError("account bundle contains duplicate game IDs")
    with np.load(args.model_ready_npz, allow_pickle=False) as data:
        game_ids = data["game_id"].astype(str)
        if set(game_ids) != set(frame["game_id"]):
            raise ValueError("account bundle and model-ready NPZ game membership differ")
        metadata = frame.set_index("game_id")
        valid = data["global_placement_ply"] > 0
        for game_index, game_id in enumerate(game_ids):
            expected = {
                "black": str(metadata.loc[game_id, "black_id"]).casefold(),
                "white": str(metadata.loc[game_id, "white_id"]).casefold(),
            }
            for side, player, keep in zip(
                data["side_to_move"][game_index].astype(str),
                data["player_id"][game_index].astype(str), valid[game_index], strict=True,
            ):
                if keep and player.casefold() != expected[side.casefold()]:
                    raise ValueError(f"player/color mismatch for {game_id}: {side}={player}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False, encoding="utf-8")
    print(json.dumps({"ok": True, "games": len(frame), "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
