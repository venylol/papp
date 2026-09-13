from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

import build_position_context_metadata as context


DATA_PATH = Path("oq_reversi_5min_elo2000_hints/position_hints.csv")
METADATA_PATH = Path("oq_reversi_5min_elo2000_hints/position_context_metadata.csv")
KEY_COLUMNS = ["game_id", "move_index", "ply"]
HINT6_MOVE_COLUMNS = ["hint6_1_move", "hint6_2_move", "hint6_3_move"]
FEATURE_COLUMNS = [
    "hint6_1_flipped_disc_count",
    "hint6_1_flipped_opponent_dispersion_excl_xc",
    "hint6_1_2_flipped_disc_count_diff",
    "hint6_1_3_flipped_disc_count_diff",
    "hint6_1_2_flipped_disc_count_abs_diff",
    "hint6_1_3_flipped_disc_count_abs_diff",
    "hint6_1_2_flipped_opponent_dispersion_excl_xc_diff",
    "hint6_1_3_flipped_opponent_dispersion_excl_xc_diff",
    "hint6_1_2_flipped_opponent_dispersion_excl_xc_abs_diff",
    "hint6_1_3_flipped_opponent_dispersion_excl_xc_abs_diff",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument("--metadata", type=Path, default=METADATA_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    meta = pd.read_csv(args.metadata, low_memory=False)
    required_meta = set(KEY_COLUMNS + ["board", "normalized_side_to_move"])
    missing_meta = sorted(required_meta - set(meta.columns))
    if missing_meta:
        raise ValueError(f"metadata missing required columns: {missing_meta}")

    meta = meta.drop(columns=[c for c in HINT6_MOVE_COLUMNS if c in meta.columns], errors="ignore")
    hints = pd.read_csv(args.data, usecols=KEY_COLUMNS + HINT6_MOVE_COLUMNS, low_memory=False)
    if int(hints.duplicated(KEY_COLUMNS).sum()):
        raise ValueError("position hints has duplicate key rows")

    merged = meta.merge(hints, on=KEY_COLUMNS, how="left", validate="one_to_one")
    if len(merged) != len(meta):
        raise ValueError(f"hint merge changed row count: {len(meta)} -> {len(merged)}")

    unique_cols = ["board", "normalized_side_to_move", *HINT6_MOVE_COLUMNS]
    unique = merged[unique_cols].drop_duplicates().copy()
    feature_by_position = {}
    for row in unique.itertuples(index=False):
        board = context.normalize_board(row.board)
        side = context.normalize_side(row.normalized_side_to_move)
        feature_by_position[(row.board, row.normalized_side_to_move, row.hint6_1_move, row.hint6_2_move, row.hint6_3_move)] = (
            context.hint6_dispersion_summary(board, side, row.hint6_1_move, row.hint6_2_move, row.hint6_3_move)
        )

    keys = list(
        zip(
            merged["board"],
            merged["normalized_side_to_move"],
            merged["hint6_1_move"],
            merged["hint6_2_move"],
            merged["hint6_3_move"],
        )
    )
    for col in FEATURE_COLUMNS:
        meta[col] = [feature_by_position[key][col] for key in keys]

    tmp = args.metadata.with_suffix(args.metadata.suffix + ".tmp")
    meta.to_csv(tmp, index=False)
    os.replace(tmp, args.metadata)

    valid_h1 = int(pd.to_numeric(meta["hint6_1_flipped_disc_count"], errors="coerce").notna().sum())
    valid_h2_diff = int(pd.to_numeric(meta["hint6_1_2_flipped_disc_count_diff"], errors="coerce").notna().sum())
    valid_h3_diff = int(pd.to_numeric(meta["hint6_1_3_flipped_disc_count_diff"], errors="coerce").notna().sum())
    print(
        f"Wrote {args.metadata} rows={len(meta)} unique_hint6_positions={len(unique)} "
        f"valid_hint6_1={valid_h1} valid_1_2_diff={valid_h2_diff} valid_1_3_diff={valid_h3_diff}"
    )


if __name__ == "__main__":
    main()
