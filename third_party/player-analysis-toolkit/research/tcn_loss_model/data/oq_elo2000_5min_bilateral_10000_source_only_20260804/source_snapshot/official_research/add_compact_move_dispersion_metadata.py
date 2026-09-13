from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

import build_position_context_metadata as context


METADATA_PATH = Path("oq_reversi_5min_elo2000_hints/position_context_metadata.csv")


FEATURE_COLUMNS = [
    "legal_move_min_flipped_opponent_dispersion_excl_xc",
    "has_best_compact_move",
    "has_danger_compact_move",
]

STALE_FEATURE_COLUMNS = [
    "has_best_compact_move_dispersion_1",
    "has_danger_compact_move_dispersion_0",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, default=METADATA_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    df = pd.read_csv(args.metadata, low_memory=False)
    required = {"board", "normalized_side_to_move"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"metadata missing required columns: {missing}")
    df = df.drop(columns=[c for c in STALE_FEATURE_COLUMNS if c in df.columns], errors="ignore")

    unique = df[["board", "normalized_side_to_move"]].drop_duplicates().copy()
    feature_by_position = {}
    for row in unique.itertuples(index=False):
        board = context.normalize_board(row.board)
        side = context.normalize_side(row.normalized_side_to_move)
        feature_by_position[(row.board, row.normalized_side_to_move)] = context.compact_move_dispersion_summary(
            board, side
        )

    pairs = list(zip(df["board"], df["normalized_side_to_move"]))
    for col in FEATURE_COLUMNS:
        df[col] = [feature_by_position[pair][col] for pair in pairs]

    tmp = args.metadata.with_suffix(args.metadata.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, args.metadata)

    pass_rows = int(pd.to_numeric(df[FEATURE_COLUMNS[0]], errors="coerce").isna().sum())
    best_rows = int(pd.to_numeric(df[FEATURE_COLUMNS[1]], errors="coerce").fillna(0).sum())
    danger_rows = int(pd.to_numeric(df[FEATURE_COLUMNS[2]], errors="coerce").fillna(0).sum())
    print(
        f"Wrote {args.metadata} rows={len(df)} unique_positions={len(unique)} "
        f"pass_or_no_legal_rows={pass_rows} best_compact_rows={best_rows} danger_compact_rows={danger_rows}"
    )


if __name__ == "__main__":
    main()
