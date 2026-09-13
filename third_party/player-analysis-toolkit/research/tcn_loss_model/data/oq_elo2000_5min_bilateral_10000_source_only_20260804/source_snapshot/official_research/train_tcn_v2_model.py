from __future__ import annotations

import argparse
import json
import math
import random
import re
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import GroupShuffleSplit
from torch.utils.data import DataLoader

import train_tcn_model as base
from train_unified_model import (
    RANDOM_STATE,
    add_engine_features,
    add_history_features,
    clean_feature_frame,
    select_features,
)


MODEL_NAME = "tcn_v2_side_phase_board_time_model"
DATA_PATH = Path("oq_reversi_5min_elo2000_hints/position_hints.csv")
CONTEXT_METADATA_PATH = Path("oq_reversi_5min_elo2000_hints/position_context_metadata.csv")
OUT_DIR = Path("tcn_v2_relative_time_model_outputs")
ZIP_PATH = Path("tcn_v2_relative_time_model_outputs.zip")
FEATURE_REVISION = "side_history_phase_board_hint6_close_best_pattern_hint6_dispersion_compact_dispersion_human_opening_frequency_context_metadata_direct_seconds"
TARGET_ACTUAL_SECONDS_FLOOR = 0.05
TARGET_INFO = {
    "target_log": "log1p(actual_thinking_time_ms / 1000.0)",
    "pred_seconds_transform": "expm1(pred_log), then clamp to [0.05, remaining_before_s * 0.95]",
    "minimum_pred_seconds": TARGET_ACTUAL_SECONDS_FLOOR,
    "training_time_limit_ms": 300000,
}
TARGET_METADATA_COLUMNS = []
CONTEXT_METADATA_KEYS = ["game_id", "ply", "move_index"]
CONTEXT_METADATA_RAW_COLUMNS = {
    "game_id",
    "move_index",
    "ply",
    "side_to_move",
    "board",
    "normalized_side_to_move",
}
CORNER_SQUARES = {0, 7, 56, 63}
EDGE_SQUARES = {idx for idx in range(64) if idx // 8 in {0, 7} or idx % 8 in {0, 7}}
NON_CORNER_EDGE_SQUARES = EDGE_SQUARES - CORNER_SQUARES
X_SQUARES = {9, 14, 49, 54}
C_SQUARES = {1, 6, 8, 15, 48, 55, 57, 62}
MOVE_COORD_RE = re.compile(r"\b([a-h][1-8])\b", re.IGNORECASE)


@dataclass
class TCNV2Config(base.Config):
    epochs: int = 600
    patience: int = 105
    test_size: float = 0.1
    warmup_epochs: int = 12
    min_lr_ratio: float = 0.08


def side_to_disc(side) -> str | None:
    text = str(side).strip().lower()
    if text in {"black", "x", "b"}:
        return "X"
    if text in {"white", "o", "w"}:
        return "O"
    return None


def opponent_disc(disc: str | None) -> str | None:
    if disc == "X":
        return "O"
    if disc == "O":
        return "X"
    return None


def normalize_board(board) -> str:
    text = str(board)
    return text[:64].ljust(64, "-")


def normalize_board64_for_compare(board) -> str:
    text = str(board).upper().replace(".", "-")
    chars = [(ch if ch in {"X", "O"} else "-") for ch in text[:64].ljust(64, "-")]
    return "".join(chars)


def learning_rate_for_epoch(epoch: int, cfg: TCNV2Config) -> float:
    if cfg.epochs <= 1:
        return cfg.lr
    warmup_epochs = max(0, int(cfg.warmup_epochs))
    min_lr_ratio = min(max(float(cfg.min_lr_ratio), 0.0), 1.0)
    if warmup_epochs > 0 and epoch <= warmup_epochs:
        return cfg.lr * epoch / warmup_epochs
    decay_start = warmup_epochs + 1
    decay_steps = max(cfg.epochs - warmup_epochs, 1)
    progress = min(max((epoch - decay_start) / decay_steps, 0.0), 1.0)
    cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
    return cfg.lr * (min_lr_ratio + (1.0 - min_lr_ratio) * cosine)


def set_optimizer_lr(optimizer: torch.optim.Optimizer, lr: float) -> None:
    for group in optimizer.param_groups:
        group["lr"] = lr


def board_legal_moves(board: str, side_disc: str | None) -> set[int]:
    if side_disc not in {"X", "O"}:
        return set()
    opp = opponent_disc(side_disc)
    cells = list(normalize_board(board))
    moves: set[int] = set()
    directions = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for idx, cell in enumerate(cells):
        if cell in {"X", "O"}:
            continue
        row, col = divmod(idx, 8)
        legal = False
        for dr, dc in directions:
            r, c = row + dr, col + dc
            seen_opp = False
            while 0 <= r < 8 and 0 <= c < 8:
                v = cells[r * 8 + c]
                if v == opp:
                    seen_opp = True
                elif v == side_disc:
                    if seen_opp:
                        legal = True
                    break
                else:
                    break
                r += dr
                c += dc
            if legal:
                moves.add(idx)
                break
    return moves


def empty_components_4(board: str) -> list[set[int]]:
    cells = list(normalize_board(board))
    empties = {i for i, v in enumerate(cells) if v not in {"X", "O"}}
    components: list[set[int]] = []
    while empties:
        start = empties.pop()
        comp = {start}
        stack = [start]
        while stack:
            idx = stack.pop()
            row, col = divmod(idx, 8)
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                r, c = row + dr, col + dc
                n = r * 8 + c
                if 0 <= r < 8 and 0 <= c < 8 and n in empties:
                    empties.remove(n)
                    comp.add(n)
                    stack.append(n)
        components.append(comp)
    return components


def has_opponent_independent_odd_empty(board, side) -> int:
    b = normalize_board(board)
    own = side_to_disc(side)
    opp = opponent_disc(own)
    own_moves = board_legal_moves(b, own)
    opp_moves = board_legal_moves(b, opp)
    for comp in empty_components_4(b):
        if len(comp) % 2 == 1 and comp.isdisjoint(own_moves) and not comp.isdisjoint(opp_moves):
            return 1
    return 0


def move_to_index(move) -> int | None:
    match = MOVE_COORD_RE.search(str(move))
    if not match:
        return None
    coord = match.group(1).lower()
    col = ord(coord[0]) - ord("a")
    row = int(coord[1]) - 1
    return row * 8 + col


def parse_move_indexes(text) -> set[int]:
    indexes = set()
    for match in MOVE_COORD_RE.finditer(str(text)):
        idx = move_to_index(match.group(1))
        if idx is not None:
            indexes.add(idx)
    return indexes


def legal_move_indexes(legal_moves, board, side) -> set[int]:
    indexes = parse_move_indexes(legal_moves)
    if indexes:
        return indexes
    return board_legal_moves(normalize_board(board), side_to_disc(side))


def add_hint6_pattern_features(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in df.itertuples(index=False):
        legal_indexes = legal_move_indexes(
            getattr(row, "legal_moves", ""),
            getattr(row, "board", ""),
            getattr(row, "side_to_move", ""),
        )
        close_best_moves = []
        best_score = pd.to_numeric(getattr(row, "hint6_1_score", np.nan), errors="coerce")
        if pd.notna(best_score):
            for rank in range(1, 7):
                move_idx = move_to_index(getattr(row, f"hint6_{rank}_move", ""))
                score = pd.to_numeric(getattr(row, f"hint6_{rank}_score", np.nan), errors="coerce")
                if move_idx is not None and pd.notna(score) and abs(float(score) - float(best_score)) <= 3.0:
                    close_best_moves.append(move_idx)

        corner_count = sum(idx in CORNER_SQUARES for idx in close_best_moves)
        edge_count = sum(idx in NON_CORNER_EDGE_SQUARES for idx in close_best_moves)
        x_square_count = sum(idx in X_SQUARES for idx in close_best_moves)
        c_square_count = sum(idx in C_SQUARES for idx in close_best_moves)
        rows.append(
            {
                "n_legal_corner_count": len(legal_indexes & CORNER_SQUARES),
                "pattern_hint6_close_best_count": len(close_best_moves),
                "pattern_hint6_close_best_corner_count": corner_count,
                "pattern_hint6_close_best_edge_count": edge_count,
                "pattern_hint6_close_best_x_square_count": x_square_count,
                "pattern_hint6_close_best_c_square_count": c_square_count,
                "pattern_hint6_close_best_has_corner": int(corner_count > 0),
                "pattern_hint6_close_best_has_edge": int(edge_count > 0),
                "pattern_hint6_close_best_has_x_square": int(x_square_count > 0),
                "pattern_hint6_close_best_has_c_square": int(c_square_count > 0),
            }
        )

    return pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def count_zone_discs(board, side, indexes: list[int], prefix: str) -> dict[str, int]:
    b = normalize_board(board)
    own = side_to_disc(side)
    opp = opponent_disc(own)
    values = [b[i] for i in indexes]
    return {
        f"{prefix}_disc_count": sum(v in {"X", "O"} for v in values),
        f"{prefix}_empty_count": sum(v not in {"X", "O"} for v in values),
        f"side_{prefix}_disc_count": sum(v == own for v in values) if own else 0,
        f"opponent_{prefix}_disc_count": sum(v == opp for v in values) if opp else 0,
        f"black_{prefix}_disc_count": sum(v == "X" for v in values),
        f"white_{prefix}_disc_count": sum(v == "O" for v in values),
    }


def add_phase_features(df: pd.DataFrame) -> pd.DataFrame:
    board = df["board"].map(normalize_board)
    df["empty_count"] = board.map(lambda b: sum(v not in {"X", "O"} for v in b))
    df["disc_count"] = 64 - df["empty_count"]
    df["phase_opening"] = df["empty_count"].ge(44).astype(int)
    df["phase_midgame"] = df["empty_count"].between(20, 43, inclusive="both").astype(int)
    df["phase_lategame"] = df["empty_count"].between(8, 19, inclusive="both").astype(int)
    df["phase_endgame"] = df["empty_count"].le(7).astype(int)
    return df


def add_board_zone_features(df: pd.DataFrame) -> pd.DataFrame:
    zones = {
        "corner": [0, 7, 56, 63],
        "x_square": [9, 14, 49, 54],
        "c_square": [1, 6, 8, 15, 48, 55, 57, 62],
    }
    rows = []
    for board, side in zip(df["board"], df["side_to_move"]):
        row = {}
        for prefix, indexes in zones.items():
            row.update(count_zone_discs(board, side, indexes, prefix))
        row["opponent_independent_odd_empty"] = has_opponent_independent_odd_empty(board, side)
        rows.append(row)
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def add_side_history_features(df: pd.DataFrame) -> pd.DataFrame:
    sort_cols = [c for c in ["game_id", "ply", "move_index"] if c in df.columns]
    df = df.sort_values(sort_cols).reset_index(drop=True)
    time_ms = pd.to_numeric(df["actual_thinking_time_ms"], errors="coerce")

    side_windows = [2, 4, 8]
    columns = {
        "cum_time_before_ms": [],
        "same_side_prev_time_ms": [],
        "opponent_side_prev_time_ms": [],
    }
    for prefix in ["same_side", "opponent_side"]:
        for window in side_windows:
            for stat in ["avg", "median", "max"]:
                columns[f"{prefix}_last_{window}_time_{stat}_ms"] = []
            columns[f"{prefix}_last_{window}_slow_count_5s"] = []
            columns[f"{prefix}_last_{window}_slow_count_10s"] = []

    for _game_id, game in df.groupby("game_id", sort=False):
        histories: dict[str, list[float]] = {}
        for idx in game.index:
            side = str(df.at[idx, "side_to_move"])
            opp = "white" if side.lower() == "black" else "black" if side.lower() == "white" else f"__opp_{side}"
            same_hist = histories.get(side, [])
            opp_hist = histories.get(opp, [])

            columns["cum_time_before_ms"].append(float(np.nansum(same_hist)) if same_hist else 0.0)
            columns["same_side_prev_time_ms"].append(same_hist[-1] if same_hist else np.nan)
            columns["opponent_side_prev_time_ms"].append(opp_hist[-1] if opp_hist else np.nan)

            for prefix, hist in [("same_side", same_hist), ("opponent_side", opp_hist)]:
                for window in side_windows:
                    vals = np.asarray(hist[-window:], dtype=np.float64)
                    if len(vals) == 0:
                        avg = median = max_value = np.nan
                        slow_5 = slow_10 = 0
                    else:
                        avg = float(np.nanmean(vals))
                        median = float(np.nanmedian(vals))
                        max_value = float(np.nanmax(vals))
                        slow_5 = int(np.sum(vals > 5000))
                        slow_10 = int(np.sum(vals > 10000))
                    columns[f"{prefix}_last_{window}_time_avg_ms"].append(avg)
                    columns[f"{prefix}_last_{window}_time_median_ms"].append(median)
                    columns[f"{prefix}_last_{window}_time_max_ms"].append(max_value)
                    columns[f"{prefix}_last_{window}_slow_count_5s"].append(slow_5)
                    columns[f"{prefix}_last_{window}_slow_count_10s"].append(slow_10)

            histories.setdefault(side, []).append(float(time_ms.iloc[idx]))

    for col, values in columns.items():
        df[col] = values
    if "time_limit_ms" in df.columns:
        df["remaining_before_ms"] = (
            pd.to_numeric(df["time_limit_ms"], errors="coerce")
            - pd.to_numeric(df["cum_time_before_ms"], errors="coerce")
        ).clip(lower=0)
    return df


def add_direct_time_targets(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    actual_ms = pd.to_numeric(df["actual_thinking_time_ms"], errors="coerce").clip(lower=0.0)
    df["target_log"] = np.log1p(actual_ms / 1000.0)
    return df


def make_sequences_v2(
    df: pd.DataFrame,
    feature_matrix: np.ndarray,
    game_ids: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    use = df["game_id"].isin(game_ids).to_numpy()
    selected = df.loc[use]
    game_order = list(pd.unique(selected["game_id"]))
    max_len = int(selected.groupby("game_id", sort=False).size().max())
    n_games = len(game_order)
    n_features = feature_matrix.shape[1]

    X_seq = np.zeros((n_games, max_len, n_features), dtype=np.float32)
    y_seq = np.zeros((n_games, max_len), dtype=np.float32)
    mask_seq = np.zeros((n_games, max_len), dtype=bool)

    meta_rows = []
    row_positions = np.flatnonzero(use)
    row_by_game = selected.groupby("game_id", sort=False).indices
    for game_idx, game_id in enumerate(game_order):
        row_idx = row_positions[np.asarray(row_by_game[game_id], dtype=np.int64)]
        length = len(row_idx)
        X_seq[game_idx, :length] = feature_matrix[row_idx]
        y_seq[game_idx, :length] = df.iloc[row_idx]["target_log"].to_numpy(dtype=np.float32)
        mask_seq[game_idx, :length] = True
        meta_cols = [
            "game_id",
            "ply",
            "move_index",
            "player_id",
            "side_to_move",
            "remaining_before_s",
            "hint1_nodes",
            "hint6_1_nodes",
            "last_2_time_avg_ms",
            "actual_thinking_time_ms",
            "target_log",
            *TARGET_METADATA_COLUMNS,
        ]
        meta = df.iloc[row_idx][[c for c in meta_cols if c in df.columns]].copy()
        meta["seq_game_index"] = game_idx
        meta["seq_pos"] = np.arange(length)
        meta_rows.append(meta)

    meta_df = pd.concat(meta_rows, ignore_index=True) if meta_rows else pd.DataFrame()
    return X_seq, y_seq, mask_seq, meta_df


def r2_score_manual(actual: np.ndarray, pred: np.ndarray) -> float:
    denom = np.sum((actual - actual.mean()) ** 2)
    if denom <= 0.0:
        return float("nan")
    return float(1.0 - np.sum((actual - pred) ** 2) / denom)


def compute_metrics_v2(meta_df: pd.DataFrame, pred_log: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    remaining = meta_df["remaining_before_s"] if "remaining_before_s" in meta_df.columns else None
    pred_s = base.capped_seconds(pred_log, remaining)
    actual_s = pd.to_numeric(meta_df["actual_thinking_time_ms"], errors="coerce").to_numpy(dtype=np.float64) / 1000.0
    actual_log = meta_df["target_log"].to_numpy(dtype=np.float64)

    spear_seconds = pd.Series(pred_s).corr(pd.Series(actual_s), method="spearman")
    metrics = pd.DataFrame(
        [
            {"metric": "MAE_seconds", "value": float(np.mean(np.abs(pred_s - actual_s)))},
            {"metric": "Median_AE_seconds", "value": float(np.median(np.abs(pred_s - actual_s)))},
            {"metric": "RMSE_log", "value": float(math.sqrt(np.mean((pred_log - actual_log) ** 2)))},
            {"metric": "R2_seconds", "value": r2_score_manual(actual_s, pred_s)},
            {"metric": "R2_log", "value": r2_score_manual(actual_log, pred_log)},
            {"metric": "Spearman_pred_seconds_vs_actual_seconds", "value": float(spear_seconds) if pd.notna(spear_seconds) else np.nan},
            {"metric": "Actual_mean_seconds", "value": float(np.mean(actual_s))},
            {"metric": "Predicted_mean_seconds", "value": float(np.mean(pred_s))},
            {"metric": "Actual_median_seconds", "value": float(np.median(actual_s))},
            {"metric": "Predicted_median_seconds", "value": float(np.median(pred_s))},
            {"metric": "n_test", "value": int(len(actual_s))},
        ]
    )

    pred_df = meta_df.reset_index(drop=True).copy()
    pred_df["pred_log"] = pred_log
    pred_df["actual_log"] = actual_log
    pred_df["pred_s"] = pred_s
    pred_df["actual_s"] = actual_s
    pred_df["residual_s"] = pred_df["actual_s"] - pred_df["pred_s"]
    return metrics, pred_df


def prepare_frame_v2(data_path: Path):
    df = pd.read_csv(data_path, low_memory=False)
    df = df[pd.to_numeric(df["actual_thinking_time_ms"], errors="coerce").notna()].copy()
    df["actual_thinking_time_ms"] = pd.to_numeric(df["actual_thinking_time_ms"], errors="coerce").clip(lower=0)

    df = add_engine_features(df)
    df = add_phase_features(df)
    df = add_board_zone_features(df)
    df = add_hint6_pattern_features(df)
    df = add_side_history_features(df)
    df = add_direct_time_targets(df)

    # Reuse existing non-player global-history and rolling-complexity features.
    df = add_history_features(df.drop(columns=["player_id"], errors="ignore"))
    df = add_engine_features(df)

    leak_or_unavailable = [
        c
        for c in df.columns
        if c == "player_id" or c.startswith("same_player_") or c.startswith("prev_same_player")
    ]
    if leak_or_unavailable:
        df = df.drop(columns=leak_or_unavailable, errors="ignore")

    features, categorical = select_features(df)
    feature_exclude = set(TARGET_METADATA_COLUMNS)
    features = [c for c in features if c not in feature_exclude]
    categorical = [c for c in categorical if c not in feature_exclude]
    numeric_features = [c for c in features if c not in set(categorical)]
    return df, features, categorical, numeric_features


def load_context_metadata(path: Path) -> tuple[pd.DataFrame, list[str]]:
    metadata = pd.read_csv(path, low_memory=False)
    missing_keys = [c for c in CONTEXT_METADATA_KEYS if c not in metadata.columns]
    if missing_keys:
        raise ValueError(f"context metadata missing merge keys: {missing_keys}")
    duplicate_count = int(metadata.duplicated(CONTEXT_METADATA_KEYS).sum())
    if duplicate_count:
        raise ValueError(f"context metadata has duplicate merge keys: {duplicate_count}")

    numeric_cols = [c for c in metadata.columns if c not in CONTEXT_METADATA_RAW_COLUMNS]
    for col in numeric_cols:
        metadata[col] = pd.to_numeric(metadata[col], errors="coerce")

    keep_cols = CONTEXT_METADATA_KEYS + [c for c in ["board"] if c in metadata.columns] + numeric_cols
    return metadata[keep_cols], numeric_cols


def prepare_frame_v2_with_context_metadata(data_path: Path):
    df, all_features, categorical, numeric_features = prepare_frame_v2(data_path)
    metadata, metadata_numeric_features = load_context_metadata(CONTEXT_METADATA_PATH)

    conflict_set = set(c for c in metadata_numeric_features if c in df.columns)
    if conflict_set:
        df = df.drop(columns=list(conflict_set))
        all_features = [c for c in all_features if c not in conflict_set]
        numeric_features = [c for c in numeric_features if c not in conflict_set]
        categorical = [c for c in categorical if c not in conflict_set]

    before_rows = len(df)
    df = df.merge(
        metadata,
        on=CONTEXT_METADATA_KEYS,
        how="left",
        validate="one_to_one",
        suffixes=("", "_context_metadata"),
    )
    if len(df) != before_rows:
        raise ValueError(f"context metadata merge changed row count: {before_rows} -> {len(df)}")

    if "board_context_metadata" in df.columns:
        board_mismatch = (
            df["board"].notna()
            & df["board_context_metadata"].notna()
            & df["board"].map(normalize_board64_for_compare).ne(
                df["board_context_metadata"].map(normalize_board64_for_compare)
            )
        )
        if bool(board_mismatch.any()):
            sample = df.loc[board_mismatch, CONTEXT_METADATA_KEYS].head(5).to_dict("records")
            raise ValueError(f"context metadata board mismatch on merge keys, sample={sample}")
        df = df.drop(columns=["board_context_metadata"])

    missing_metadata = int(df[metadata_numeric_features].isna().all(axis=1).sum())
    if missing_metadata:
        raise ValueError(f"context metadata missing for rows: {missing_metadata}")

    metadata_numeric_features = [c for c in metadata_numeric_features if c in df.columns]
    all_features = all_features + [c for c in metadata_numeric_features if c not in set(all_features)]
    numeric_features = numeric_features + [c for c in metadata_numeric_features if c not in set(numeric_features)]
    return df, all_features, categorical, numeric_features, metadata_numeric_features


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=TCNV2Config.epochs)
    parser.add_argument("--batch-size", type=int, default=TCNV2Config.batch_size)
    parser.add_argument("--lr", type=float, default=TCNV2Config.lr)
    parser.add_argument("--patience", type=int, default=TCNV2Config.patience)
    parser.add_argument("--test-size", type=float, default=TCNV2Config.test_size)
    parser.add_argument("--warmup-epochs", type=int, default=TCNV2Config.warmup_epochs)
    parser.add_argument("--min-lr-ratio", type=float, default=TCNV2Config.min_lr_ratio)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base.MODEL_NAME = MODEL_NAME
    base.OUT_DIR = OUT_DIR
    base.ZIP_PATH = ZIP_PATH

    cfg = TCNV2Config(
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        patience=args.patience,
        test_size=args.test_size,
        warmup_epochs=args.warmup_epochs,
        min_lr_ratio=args.min_lr_ratio,
    )
    set_seed(cfg.seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUT_DIR.iterdir():
        if path.is_file():
            path.unlink()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    t0 = time.time()
    base.write_progress(
        {
            "status": "starting",
            "model": MODEL_NAME,
            "config": asdict(cfg),
            "device": str(device),
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "feature_revision": FEATURE_REVISION,
            "context_metadata_path": str(CONTEXT_METADATA_PATH),
            "target_info": TARGET_INFO,
        }
    )

    df, all_features, categorical, numeric_features, metadata_numeric_features = prepare_frame_v2_with_context_metadata(
        DATA_PATH
    )
    groups = df["game_id"].to_numpy()
    splitter = GroupShuffleSplit(n_splits=1, test_size=cfg.test_size, random_state=cfg.seed)
    train_rows, _test_rows = next(splitter.split(df, df["target_log"].to_numpy(), groups=groups))
    train_games = pd.unique(df.iloc[train_rows]["game_id"])
    test_games = pd.unique(df.loc[~df["game_id"].isin(train_games), "game_id"])

    feature_matrix, input_features, preprocessing = base.standardize_features(df, numeric_features, train_rows)
    preprocessing["target_info"] = TARGET_INFO
    preprocessing["target_metadata_columns"] = TARGET_METADATA_COLUMNS
    X_train, y_train, mask_train, _train_meta = make_sequences_v2(df, feature_matrix, train_games)
    X_test, y_test, mask_test, test_meta = make_sequences_v2(df, feature_matrix, test_games)

    train_loader = DataLoader(
        base.SequenceDataset(X_train, y_train, mask_train),
        batch_size=cfg.batch_size,
        shuffle=True,
        num_workers=cfg.num_workers,
        pin_memory=device.type == "cuda",
    )
    test_loader = DataLoader(
        base.SequenceDataset(X_test, y_test, mask_test),
        batch_size=cfg.batch_size,
        shuffle=False,
        num_workers=cfg.num_workers,
        pin_memory=device.type == "cuda",
    )

    base.write_progress(
        {
            "status": "prepared_data",
            "model": MODEL_NAME,
            "device": str(device),
            "rows": int(len(df)),
            "train_games": int(len(train_games)),
            "test_games": int(len(test_games)),
            "train_rows": int(mask_train.sum()),
            "test_rows": int(mask_test.sum()),
            "max_seq_len": int(X_train.shape[1]),
            "all_selected_features": int(len(all_features)),
            "categorical_features_excluded": int(len(categorical)),
            "numeric_features": int(len(numeric_features)),
            "metadata_numeric_features": int(len(metadata_numeric_features)),
            "input_features_with_missing_indicators": int(len(input_features)),
            "added_feature_groups": [
                "same_side_opponent_side_history",
                "phase_empty_disc_counts",
                "corner_c_square_x_square_disc_counts",
                "n_legal_corner_count",
                "hint6_close_best_pattern_counts",
                "hint6_dispersion_flip_count_metadata",
                "compact_move_dispersion_metadata",
                "human_opening_frequency_metadata",
                "position_context_metadata",
            ],
            "feature_revision": FEATURE_REVISION,
            "context_metadata_path": str(CONTEXT_METADATA_PATH),
            "target_info": TARGET_INFO,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )

    model = base.TCNRegressor(
        input_dim=X_train.shape[2],
        channels=cfg.channels,
        levels=cfg.levels,
        kernel_size=cfg.kernel_size,
        dropout=cfg.dropout,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)

    best_rmse = float("inf")
    best_epoch = 0
    no_improve = 0
    history = []
    best_path = OUT_DIR / f"{MODEL_NAME}.pt"

    for epoch in range(1, cfg.epochs + 1):
        epoch_lr = learning_rate_for_epoch(epoch, cfg)
        set_optimizer_lr(optimizer, epoch_lr)
        model.train()
        epoch_loss_sum = 0.0
        epoch_count = 0
        for xb, yb, mb in train_loader:
            xb = xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            mb = mb.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = base.masked_mse(pred, yb, mb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
            optimizer.step()
            valid_count = int(mb.sum().item())
            epoch_loss_sum += float(loss.item()) * valid_count
            epoch_count += valid_count

        train_rmse = math.sqrt(epoch_loss_sum / max(epoch_count, 1))
        test_rmse = base.evaluate_loss(model, test_loader, device)
        if test_rmse < best_rmse - cfg.min_delta:
            best_rmse = test_rmse
            best_epoch = epoch
            no_improve = 0
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "config": asdict(cfg),
                    "input_features": input_features,
                    "preprocessing": preprocessing,
                    "numeric_features": numeric_features,
                    "metadata_numeric_features": metadata_numeric_features,
                    "categorical_features_excluded": categorical,
                    "model_name": MODEL_NAME,
                    "feature_revision": FEATURE_REVISION,
                    "context_metadata_path": str(CONTEXT_METADATA_PATH),
                    "target_info": TARGET_INFO,
                    "target_metadata_columns": TARGET_METADATA_COLUMNS,
                },
                best_path,
            )
        else:
            no_improve += 1

        row = {
            "epoch": epoch,
            "train_rmse_log": train_rmse,
            "test_rmse_log": test_rmse,
            "best_rmse_log": best_rmse,
            "best_epoch": best_epoch,
            "no_improve_epochs": no_improve,
            "lr": epoch_lr,
            "elapsed_seconds": time.time() - t0,
        }
        history.append(row)
        pd.DataFrame(history).to_csv(OUT_DIR / "training_history.csv", index=False)
        base.write_progress(
            {
                "status": "training",
                "model": MODEL_NAME,
                "device": str(device),
                "epoch": epoch,
                "epochs": cfg.epochs,
                "train_rmse_log": train_rmse,
                "test_rmse_log": test_rmse,
                "best_rmse_log": best_rmse,
                "best_epoch": best_epoch,
                "no_improve_epochs": no_improve,
                "lr": epoch_lr,
                "feature_revision": FEATURE_REVISION,
                "target_info": TARGET_INFO,
                "elapsed_seconds": round(time.time() - t0, 2),
            }
        )
        print(
            f"epoch={epoch:03d} train_rmse_log={train_rmse:.5f} "
            f"test_rmse_log={test_rmse:.5f} best={best_rmse:.5f}@{best_epoch} lr={epoch_lr:.3g}",
            flush=True,
        )
        if no_improve >= cfg.patience:
            break

    checkpoint = torch.load(best_path, map_location=device)
    model.load_state_dict(checkpoint["model_state_dict"])
    pred_log = base.predict_flat(model, test_loader, device)
    metrics, pred_df = compute_metrics_v2(test_meta, pred_log)
    metrics = pd.concat(
        [
            metrics,
            pd.DataFrame(
                [
                    {"metric": "n_train", "value": int(mask_train.sum())},
                    {"metric": "train_games", "value": int(len(train_games))},
                    {"metric": "test_games", "value": int(len(test_games))},
                    {"metric": "max_seq_len", "value": int(X_train.shape[1])},
                    {"metric": "input_features", "value": int(X_train.shape[2])},
                    {"metric": "numeric_base_features", "value": int(len(numeric_features))},
                    {"metric": "metadata_numeric_features", "value": int(len(metadata_numeric_features))},
                    {"metric": "excluded_categorical_features", "value": int(len(categorical))},
                    {"metric": "best_epoch", "value": int(best_epoch)},
                    {"metric": "device_cuda", "value": 1 if device.type == "cuda" else 0},
                ]
            ),
        ],
        ignore_index=True,
    )

    metrics.to_csv(OUT_DIR / "metrics.csv", index=False)
    pred_df.to_csv(OUT_DIR / "test_predictions_full.csv", index=False)
    pred_df.head(1000).to_csv(OUT_DIR / "sample_predictions.csv", index=False)
    (OUT_DIR / "preprocessing.json").write_text(json.dumps(preprocessing, indent=2), encoding="utf-8")
    (OUT_DIR / "config.json").write_text(
        json.dumps(
            {
                "training_config": asdict(cfg),
                "model_name": MODEL_NAME,
                "feature_revision": FEATURE_REVISION,
                "target_info": TARGET_INFO,
                "target_metadata_columns": TARGET_METADATA_COLUMNS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    base.write_plots(pred_df)

    summary = [
        f"# {MODEL_NAME}",
        "",
        f"- data: `{DATA_PATH}`",
        f"- context metadata: `{CONTEXT_METADATA_PATH}`",
        f"- feature revision: `{FEATURE_REVISION}`",
        "- target: `log1p(actual_thinking_time_ms / 1000.0)`",
        "- predicted seconds: `expm1(pred_log)`, clamped to `[0.05, remaining_before_s * 0.95]`",
        "- model: `causal TCN`",
        f"- device: `{device}`",
        f"- rows used: `{len(df)}`",
        f"- train rows: `{int(mask_train.sum())}`",
        f"- test rows: `{int(mask_test.sum())}`",
        f"- train games: `{len(train_games)}`",
        f"- test games: `{len(test_games)}`",
        f"- max seq len: `{X_train.shape[1]}`",
        f"- input features: `{X_train.shape[2]}`",
        f"- best epoch: `{best_epoch}`",
        "",
        "## Added Features",
        "- same_side/opponent_side history",
        "- empty_count/disc_count and phase flags",
        "- corner/C-square/X-square disc counts",
        "- n_legal_corner_count",
        "- hint6 close-best corner/edge/X-square/C-square pattern counts",
        "- hint6 flip-count and dispersion-difference metadata",
        "- compact move dispersion metadata",
        "- human opening frequency metadata",
        "- position_context_metadata",
        "",
        "## Metrics",
        base.frame_to_markdown(metrics),
    ]
    (OUT_DIR / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    shutil.copy2(Path(__file__), OUT_DIR / "train_tcn_v2_model.py")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in OUT_DIR.rglob("*"):
            zf.write(path, path.relative_to(OUT_DIR.parent))

    final_metrics = {row["metric"]: row["value"] for row in metrics.to_dict("records")}
    base.write_progress(
        {
            "status": "complete",
            "model": MODEL_NAME,
            "device": str(device),
            "best_epoch": best_epoch,
            "metrics": final_metrics,
            "outputs_dir": str(OUT_DIR),
            "zip_path": str(ZIP_PATH),
            "feature_revision": FEATURE_REVISION,
            "context_metadata_path": str(CONTEXT_METADATA_PATH),
            "target_info": TARGET_INFO,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )
    print(metrics.to_string(index=False))
    print(f"\nOUT_DIR\n{OUT_DIR}")
    print(f"\nZIP_PATH\n{ZIP_PATH}")


if __name__ == "__main__":
    main()
