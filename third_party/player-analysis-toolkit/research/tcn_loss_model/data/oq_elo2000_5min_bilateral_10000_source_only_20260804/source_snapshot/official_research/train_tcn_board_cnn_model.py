from __future__ import annotations

import argparse
import json
import math
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import GroupShuffleSplit
from torch import nn
from torch.utils.data import DataLoader, Dataset

import train_tcn_model as base
import train_tcn_v2_model as v2
from train_causal_transformer_board_model import (
    BOARD_CONTEXT_NAMES,
    BOARD_ENCODING,
    CACHE_DIR as BOARD_CONTEXT_CACHE_DIR,
    load_or_build_board_contexts,
)


MODEL_NAME = "tcn_board_cnn_time_model"
DATA_PATH = v2.DATA_PATH
CONTEXT_METADATA_PATH = v2.CONTEXT_METADATA_PATH
OUT_DIR = Path("tcn_board_cnn_time_model_outputs")
ZIP_PATH = Path("tcn_board_cnn_time_model_outputs.zip")
FEATURE_REVISION = (
    f"{v2.FEATURE_REVISION}_board_cnn_conditioned_tcn_current_hint6_rank_planes_rank1_score_rank2_4_gap_planes_prev_own_hint1_value"
)
HINT6_MAX_PLANES = 6
HINT6_VALUE_GAP_MAX_RANK = 4
HINT6_VALUE_SCALE = 32.0
PREV_OWN_HINT_VALUE_PLANES = 2
BOARD_BASE_CNN_CHANNELS = [
    "current_empty",
    "current_X",
    "current_O",
    "prev_opponent_empty",
    "prev_opponent_X",
    "prev_opponent_O",
    "prev_own_empty",
    "prev_own_X",
    "prev_own_O",
    "prev_opponent_actual_move",
    "prev_own_actual_move",
]


def current_hint_value_plane_count(current_hint_planes: int) -> int:
    return min(max(int(current_hint_planes), 0), HINT6_VALUE_GAP_MAX_RANK)


def make_board_cnn_encoding(current_hint_planes: int) -> dict:
    hint_channels = [f"current_hint6_{rank}_move" for rank in range(1, current_hint_planes + 1)]
    value_plane_count = current_hint_value_plane_count(current_hint_planes)
    hint_value_channels = []
    if value_plane_count >= 1:
        hint_value_channels.append("current_hint6_1_score_value")
    hint_value_channels.extend(
        f"current_hint6_{rank}_gap_from_rank1" for rank in range(2, value_plane_count + 1)
    )
    prev_own_value_channels = ["prev_own_hint6_1_score_value", "prev_own_hint6_1_score_available"]
    return {
        **BOARD_ENCODING,
        "hint_move_shape": f"batch x time x {current_hint_planes}; current hint6 move ranks only, actual current move hidden",
        "hint_move_token_ids": {"no_hint": 0, "a1": 1, "h8": 64},
        "hint_value_shape": (
            f"batch x time x {value_plane_count}; rank1 score plus rank2-rank{value_plane_count} score gaps"
            if value_plane_count
            else "batch x time x 0"
        ),
        "hint_value_scale": HINT6_VALUE_SCALE,
        "hint_value_transform": "tanh(value / scale); rank1 uses raw score, ranks 2-4 use hint6_1_score - hint6_rank_score",
        "prev_own_hint_value_shape": (
            f"batch x time x {PREV_OWN_HINT_VALUE_PLANES}; previous same-side hint6_1 score value plus availability"
        ),
        "prev_own_hint_value_transform": (
            "value=tanh(prev_own_hint6_1_score / scale), availability=1 when previous same-side row and finite score exist; "
            "both channels broadcast over the 8x8 board"
        ),
        "cnn_channels": BOARD_BASE_CNN_CHANNELS + hint_channels + hint_value_channels + prev_own_value_channels,
        "board_cnn_input_channels": (
            len(BOARD_BASE_CNN_CHANNELS) + current_hint_planes + value_plane_count + PREV_OWN_HINT_VALUE_PLANES
        ),
        "current_hint_planes": current_hint_planes,
        "current_hint_value_planes": value_plane_count,
        "current_hint_value_gap_max_rank": HINT6_VALUE_GAP_MAX_RANK,
        "prev_own_hint_value_planes": PREV_OWN_HINT_VALUE_PLANES,
        "fusion": "board CNN embedding FiLM-conditions numeric step embedding before causal TCN",
    }


BOARD_CNN_ENCODING = make_board_cnn_encoding(HINT6_MAX_PLANES)


@dataclass
class BoardCNNConfig(v2.TCNV2Config):
    board_embedding_dim: int = 96
    board_channels: int = 64
    board_dropout: float = 0.10
    prep_workers: int = 16
    save_latest_every: int = 1
    current_hint_planes: int = HINT6_MAX_PLANES


class BoardSequenceDataset(Dataset):
    def __init__(
        self,
        X: np.ndarray,
        boards: np.ndarray,
        board_moves: np.ndarray,
        current_hint_moves: np.ndarray,
        current_hint_values: np.ndarray,
        prev_own_hint_values: np.ndarray,
        y: np.ndarray,
        mask: np.ndarray,
    ) -> None:
        self.X = torch.from_numpy(X)
        self.boards = torch.from_numpy(boards)
        self.board_moves = torch.from_numpy(board_moves)
        self.current_hint_moves = torch.from_numpy(current_hint_moves)
        self.current_hint_values = torch.from_numpy(current_hint_values)
        self.prev_own_hint_values = torch.from_numpy(prev_own_hint_values)
        self.y = torch.from_numpy(y)
        self.mask = torch.from_numpy(mask)

    def __len__(self) -> int:
        return self.X.shape[0]

    def __getitem__(self, idx: int):
        return (
            self.X[idx],
            self.boards[idx],
            self.board_moves[idx],
            self.current_hint_moves[idx],
            self.current_hint_values[idx],
            self.prev_own_hint_values[idx],
            self.y[idx],
            self.mask[idx],
        )


class BoardCNNEncoder(nn.Module):
    def __init__(self, cfg: BoardCNNConfig) -> None:
        super().__init__()
        hidden = cfg.board_channels
        self.current_hint_planes = int(cfg.current_hint_planes)
        self.current_hint_value_planes = current_hint_value_plane_count(self.current_hint_planes)
        self.prev_own_hint_value_planes = PREV_OWN_HINT_VALUE_PLANES
        input_channels = (
            len(BOARD_BASE_CNN_CHANNELS)
            + self.current_hint_planes
            + self.current_hint_value_planes
            + self.prev_own_hint_value_planes
        )
        self.cnn = nn.Sequential(
            nn.Conv2d(input_channels, hidden, kernel_size=3, padding=1),
            nn.GroupNorm(8, hidden),
            nn.GELU(),
            nn.Dropout2d(cfg.board_dropout),
            nn.Conv2d(hidden, hidden, kernel_size=3, padding=1),
            nn.GroupNorm(8, hidden),
            nn.GELU(),
            nn.Dropout2d(cfg.board_dropout),
            nn.Conv2d(hidden, cfg.board_embedding_dim, kernel_size=3, padding=1),
            nn.GELU(),
            nn.AdaptiveAvgPool2d(1),
        )
        self.norm = nn.LayerNorm(cfg.board_embedding_dim)

    def forward(
        self,
        board_tokens: torch.Tensor,
        board_move_tokens: torch.Tensor,
        current_hint_tokens: torch.Tensor,
        current_hint_values: torch.Tensor,
        prev_own_hint_values: torch.Tensor,
    ) -> torch.Tensor:
        batch, seq_len, contexts, squares = board_tokens.shape
        if contexts != len(BOARD_CONTEXT_NAMES) or squares != 64:
            raise ValueError(f"expected board shape BxTx{len(BOARD_CONTEXT_NAMES)}x64, got {tuple(board_tokens.shape)}")
        if board_move_tokens.shape != (batch, seq_len, contexts):
            raise ValueError(
                f"expected move token shape {(batch, seq_len, contexts)}, got {tuple(board_move_tokens.shape)}"
            )
        if current_hint_tokens.shape != (batch, seq_len, self.current_hint_planes):
            raise ValueError(
                f"expected current hint token shape {(batch, seq_len, self.current_hint_planes)}, "
                f"got {tuple(current_hint_tokens.shape)}"
            )
        if current_hint_values.shape != (batch, seq_len, self.current_hint_value_planes):
            raise ValueError(
                f"expected current hint value shape {(batch, seq_len, self.current_hint_value_planes)}, "
                f"got {tuple(current_hint_values.shape)}"
            )
        if prev_own_hint_values.shape != (batch, seq_len, self.prev_own_hint_value_planes):
            raise ValueError(
                f"expected prev own hint value shape {(batch, seq_len, self.prev_own_hint_value_planes)}, "
                f"got {tuple(prev_own_hint_values.shape)}"
            )

        tokens = board_tokens.reshape(batch * seq_len, contexts, 64).long()
        moves = board_move_tokens.reshape(batch * seq_len, contexts).long()
        hints = current_hint_tokens.reshape(batch * seq_len, self.current_hint_planes).long()
        hint_values = current_hint_values.reshape(batch * seq_len, self.current_hint_value_planes).float()
        prev_own_values = prev_own_hint_values.reshape(batch * seq_len, self.prev_own_hint_value_planes).float()
        planes: list[torch.Tensor] = []
        for context_idx in range(contexts):
            context = tokens[:, context_idx]
            planes.extend([(context == token_id).float() for token_id in (1, 2, 3)])

        for context_idx in (1, 2):
            move_plane = torch.zeros(tokens.shape[0], 64, device=tokens.device, dtype=torch.float32)
            move_idx = moves[:, context_idx] - 1
            valid = move_idx >= 0
            if bool(valid.any()):
                move_plane[valid, move_idx[valid]] = 1.0
            planes.append(move_plane)

        for hint_idx in range(self.current_hint_planes):
            hint_plane = torch.zeros(tokens.shape[0], 64, device=tokens.device, dtype=torch.float32)
            move_idx = hints[:, hint_idx] - 1
            valid = move_idx >= 0
            if bool(valid.any()):
                hint_plane[valid, move_idx[valid]] = 1.0
            planes.append(hint_plane)

        for value_idx in range(self.current_hint_value_planes):
            value_plane = torch.zeros(tokens.shape[0], 64, device=tokens.device, dtype=torch.float32)
            move_idx = hints[:, value_idx] - 1
            valid = move_idx >= 0
            if bool(valid.any()):
                value_plane[valid, move_idx[valid]] = hint_values[valid, value_idx]
            planes.append(value_plane)

        for value_idx in range(self.prev_own_hint_value_planes):
            planes.append(prev_own_values[:, value_idx].unsqueeze(1).expand(-1, 64))

        board_planes = torch.stack(planes, dim=1).reshape(
            tokens.shape[0],
            len(BOARD_BASE_CNN_CHANNELS)
            + self.current_hint_planes
            + self.current_hint_value_planes
            + self.prev_own_hint_value_planes,
            8,
            8,
        )
        embedding = self.cnn(board_planes).flatten(1)
        return self.norm(embedding).reshape(batch, seq_len, -1)


class BoardConditionedTCNRegressor(nn.Module):
    def __init__(
        self,
        input_dim: int,
        cfg: BoardCNNConfig,
    ) -> None:
        super().__init__()
        self.input_norm = nn.LayerNorm(input_dim)
        self.input_proj = nn.Linear(input_dim, cfg.channels)
        self.board_encoder = BoardCNNEncoder(cfg)
        self.board_proj = nn.Sequential(
            nn.Linear(cfg.board_embedding_dim, cfg.channels),
            nn.GELU(),
            nn.Dropout(cfg.dropout),
        )
        self.film = nn.Sequential(
            nn.Linear(cfg.board_embedding_dim, cfg.channels * 2),
        )
        blocks = []
        in_channels = cfg.channels
        for level in range(cfg.levels):
            dilation = 2**level
            blocks.append(base.TemporalBlock(in_channels, cfg.channels, cfg.kernel_size, dilation, cfg.dropout))
            in_channels = cfg.channels
        self.tcn = nn.Sequential(*blocks)
        self.head = nn.Sequential(
            nn.Conv1d(cfg.channels, cfg.channels // 2, 1),
            nn.GELU(),
            nn.Dropout(cfg.dropout),
            nn.Conv1d(cfg.channels // 2, 1, 1),
        )

    def forward(
        self,
        x: torch.Tensor,
        board_tokens: torch.Tensor,
        board_move_tokens: torch.Tensor,
        current_hint_tokens: torch.Tensor,
        current_hint_values: torch.Tensor,
        prev_own_hint_values: torch.Tensor,
    ) -> torch.Tensor:
        numeric = self.input_proj(self.input_norm(x))
        board = self.board_encoder(
            board_tokens,
            board_move_tokens,
            current_hint_tokens,
            current_hint_values,
            prev_own_hint_values,
        )
        gamma, beta = self.film(board).chunk(2, dim=-1)
        numeric = numeric * (1.0 + 0.1 * torch.tanh(gamma)) + 0.1 * beta
        fused = numeric + self.board_proj(board)
        y = self.tcn(fused.transpose(1, 2))
        return self.head(y).squeeze(1)


@torch.no_grad()
def evaluate_rmse(model: nn.Module, loader: DataLoader, device: torch.device) -> float:
    model.eval()
    total_loss = 0.0
    total_count = 0
    for xb, bb, bm, hm, hv, pv, yb, mb in loader:
        xb = xb.to(device, non_blocking=True)
        bb = bb.to(device, non_blocking=True)
        bm = bm.to(device, non_blocking=True)
        hm = hm.to(device, non_blocking=True)
        hv = hv.to(device, non_blocking=True)
        pv = pv.to(device, non_blocking=True)
        yb = yb.to(device, non_blocking=True)
        mb = mb.to(device, non_blocking=True)
        pred = model(xb, bb, bm, hm, hv, pv)
        valid = mb.bool()
        total_loss += torch.sum((pred[valid] - yb[valid]) ** 2).item()
        total_count += int(valid.sum().item())
    return math.sqrt(total_loss / max(total_count, 1))


@torch.no_grad()
def predict_flat(model: nn.Module, loader: DataLoader, device: torch.device) -> np.ndarray:
    model.eval()
    chunks = []
    for xb, bb, bm, hm, hv, pv, _yb, mb in loader:
        xb = xb.to(device, non_blocking=True)
        bb = bb.to(device, non_blocking=True)
        bm = bm.to(device, non_blocking=True)
        hm = hm.to(device, non_blocking=True)
        hv = hv.to(device, non_blocking=True)
        pv = pv.to(device, non_blocking=True)
        pred = model(xb, bb, bm, hm, hv, pv).detach().cpu().numpy()
        chunks.append(pred[mb.numpy().astype(bool)])
    return np.concatenate(chunks)


def make_current_hint_move_sequences(df: pd.DataFrame, game_ids: np.ndarray, hint_planes: int) -> np.ndarray:
    use = df["game_id"].isin(game_ids).to_numpy()
    game_order = list(pd.unique(df.loc[use, "game_id"]))
    max_len = int(df.loc[use].groupby("game_id", sort=False).size().max())
    hint_seq = np.zeros((len(game_order), max_len, hint_planes), dtype=np.uint8)
    row_by_game = df.loc[use].groupby("game_id", sort=False).indices
    for game_idx, game_id in enumerate(game_order):
        row_idx = np.asarray(row_by_game[game_id], dtype=np.int64)
        view = df.iloc[row_idx]
        for rank in range(1, hint_planes + 1):
            col = f"hint6_{rank}_move"
            if col not in view.columns:
                continue
            hint_seq[game_idx, : len(view), rank - 1] = [
                0 if (idx := v2.move_to_index(move)) is None else idx + 1 for move in view[col]
            ]
    return hint_seq


def make_current_hint_value_sequences(df: pd.DataFrame, game_ids: np.ndarray, hint_planes: int) -> np.ndarray:
    value_planes = current_hint_value_plane_count(hint_planes)
    use = df["game_id"].isin(game_ids).to_numpy()
    game_order = list(pd.unique(df.loc[use, "game_id"]))
    max_len = int(df.loc[use].groupby("game_id", sort=False).size().max())
    value_seq = np.zeros((len(game_order), max_len, value_planes), dtype=np.float32)
    if value_planes == 0:
        return value_seq

    row_by_game = df.loc[use].groupby("game_id", sort=False).indices
    for game_idx, game_id in enumerate(game_order):
        row_idx = np.asarray(row_by_game[game_id], dtype=np.int64)
        view = df.iloc[row_idx]
        if "hint6_1_score" in view.columns:
            best = pd.to_numeric(view["hint6_1_score"], errors="coerce").to_numpy(dtype=np.float32)
        else:
            best = np.full(len(view), np.nan, dtype=np.float32)
        values = np.zeros((len(view), value_planes), dtype=np.float32)
        valid_best = np.isfinite(best)
        values[valid_best, 0] = np.tanh(best[valid_best] / HINT6_VALUE_SCALE)
        for rank in range(2, value_planes + 1):
            score_col = f"hint6_{rank}_score"
            if score_col in view.columns:
                score = pd.to_numeric(view[score_col], errors="coerce").to_numpy(dtype=np.float32)
            else:
                score = np.full(len(view), np.nan, dtype=np.float32)
            valid = valid_best & np.isfinite(score)
            gap = np.zeros(len(view), dtype=np.float32)
            gap[valid] = best[valid] - score[valid]
            values[valid, rank - 1] = np.tanh(gap[valid] / HINT6_VALUE_SCALE)
        value_seq[game_idx, : len(view), :] = values
    return value_seq


def make_prev_own_hint_value_sequences(df: pd.DataFrame, game_ids: np.ndarray) -> np.ndarray:
    use = df["game_id"].isin(game_ids).to_numpy()
    game_order = list(pd.unique(df.loc[use, "game_id"]))
    max_len = int(df.loc[use].groupby("game_id", sort=False).size().max())
    value_seq = np.zeros((len(game_order), max_len, PREV_OWN_HINT_VALUE_PLANES), dtype=np.float32)
    if "hint6_1_score" not in df.columns:
        return value_seq

    row_by_game = df.loc[use].groupby("game_id", sort=False).indices
    for game_idx, game_id in enumerate(game_order):
        row_idx = np.asarray(row_by_game[game_id], dtype=np.int64)
        view = df.iloc[row_idx]
        sides = [str(side).strip().lower() for side in view["side_to_move"]]
        scores = pd.to_numeric(view["hint6_1_score"], errors="coerce").to_numpy(dtype=np.float32)
        values = np.zeros((len(view), PREV_OWN_HINT_VALUE_PLANES), dtype=np.float32)
        for pos, side in enumerate(sides):
            prev_own_pos = None
            for prev_pos in range(pos - 1, -1, -1):
                if sides[prev_pos] == side:
                    prev_own_pos = prev_pos
                    break
            if prev_own_pos is None:
                continue
            score = scores[prev_own_pos]
            if np.isfinite(score):
                values[pos, 0] = np.tanh(score / HINT6_VALUE_SCALE)
                values[pos, 1] = 1.0
        value_seq[game_idx, : len(view), :] = values
    return value_seq


def write_progress(payload: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "updated_at_unix": time.time()}
    tmp_path = OUT_DIR / "progress.json.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(OUT_DIR / "progress.json")


def atomic_torch_save(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    torch.save(payload, tmp_path)
    tmp_path.replace(path)


def build_checkpoint_payload(
    *,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    cfg: BoardCNNConfig,
    epoch: int,
    train_rmse: float | None,
    test_rmse: float | None,
    best_rmse: float,
    best_epoch: int,
    no_improve: int,
    epoch_lr: float,
    input_features: list[str],
    preprocessing: dict,
    numeric_features: list[str],
    metadata_numeric_features: list[str],
    categorical: list[str],
    extra: dict | None = None,
) -> dict:
    payload = {
        "checkpoint_version": 2,
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "optimizer_name": optimizer.__class__.__name__,
        "epoch": int(epoch),
        "train_rmse_log": train_rmse,
        "test_rmse_log": test_rmse,
        "best_rmse_log": best_rmse,
        "best_epoch": int(best_epoch),
        "no_improve_epochs": int(no_improve),
        "lr": float(epoch_lr),
        "config": asdict(cfg),
        "input_features": input_features,
        "preprocessing": preprocessing,
        "numeric_features": numeric_features,
        "metadata_numeric_features": metadata_numeric_features,
        "categorical_features_excluded": categorical,
        "model_name": MODEL_NAME,
        "feature_revision": FEATURE_REVISION,
        "context_metadata_path": str(CONTEXT_METADATA_PATH),
        "target_info": v2.TARGET_INFO,
        "target_metadata_columns": v2.TARGET_METADATA_COLUMNS,
        "board_encoding": make_board_cnn_encoding(cfg.current_hint_planes),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_state_all": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
    }
    if extra:
        payload.update(extra)
    return payload


def clean_output_dir() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUT_DIR.iterdir():
        if path.is_file():
            path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=BoardCNNConfig.epochs)
    parser.add_argument("--batch-size", type=int, default=BoardCNNConfig.batch_size)
    parser.add_argument("--channels", type=int, default=BoardCNNConfig.channels)
    parser.add_argument("--levels", type=int, default=BoardCNNConfig.levels)
    parser.add_argument("--kernel-size", type=int, default=BoardCNNConfig.kernel_size)
    parser.add_argument("--dropout", type=float, default=BoardCNNConfig.dropout)
    parser.add_argument("--board-embedding-dim", type=int, default=BoardCNNConfig.board_embedding_dim)
    parser.add_argument("--board-channels", type=int, default=BoardCNNConfig.board_channels)
    parser.add_argument("--board-dropout", type=float, default=BoardCNNConfig.board_dropout)
    parser.add_argument("--lr", type=float, default=BoardCNNConfig.lr)
    parser.add_argument("--patience", type=int, default=BoardCNNConfig.patience)
    parser.add_argument("--test-size", type=float, default=BoardCNNConfig.test_size)
    parser.add_argument("--warmup-epochs", type=int, default=BoardCNNConfig.warmup_epochs)
    parser.add_argument("--min-lr-ratio", type=float, default=BoardCNNConfig.min_lr_ratio)
    parser.add_argument("--prep-workers", type=int, default=BoardCNNConfig.prep_workers)
    parser.add_argument("--save-latest-every", type=int, default=BoardCNNConfig.save_latest_every)
    parser.add_argument("--current-hint-planes", type=int, default=BoardCNNConfig.current_hint_planes)
    parser.add_argument("--resume-from", type=Path, default=None)
    parser.add_argument("--resume-latest", action="store_true")
    parser.add_argument("--reset-best-on-resume", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = BoardCNNConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        channels=args.channels,
        levels=args.levels,
        kernel_size=args.kernel_size,
        dropout=args.dropout,
        board_embedding_dim=args.board_embedding_dim,
        board_channels=args.board_channels,
        board_dropout=args.board_dropout,
        lr=args.lr,
        patience=args.patience,
        test_size=args.test_size,
        warmup_epochs=args.warmup_epochs,
        min_lr_ratio=args.min_lr_ratio,
        prep_workers=args.prep_workers,
        save_latest_every=args.save_latest_every,
        current_hint_planes=args.current_hint_planes,
    )
    if not 0 <= cfg.current_hint_planes <= HINT6_MAX_PLANES:
        raise ValueError(f"--current-hint-planes must be between 0 and {HINT6_MAX_PLANES}")
    board_encoding = make_board_cnn_encoding(cfg.current_hint_planes)
    v2.set_seed(cfg.seed)
    resume_path = args.resume_from
    if args.resume_latest:
        resume_path = OUT_DIR / f"{MODEL_NAME}_latest.pt"
    if resume_path is None:
        clean_output_dir()
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
    base.MODEL_NAME = MODEL_NAME
    base.OUT_DIR = OUT_DIR
    base.ZIP_PATH = ZIP_PATH
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    t0 = time.time()

    write_progress(
        {
            "status": "preparing_frame",
            "model": MODEL_NAME,
            "config": asdict(cfg),
            "device": str(device),
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "feature_revision": FEATURE_REVISION,
            "context_metadata_path": str(CONTEXT_METADATA_PATH),
            "target_info": v2.TARGET_INFO,
            "board_encoding": board_encoding,
            "resume_path": str(resume_path) if resume_path else None,
            "reset_best_on_resume": bool(args.reset_best_on_resume),
        }
    )

    df, all_features, categorical, numeric_features, metadata_numeric_features = (
        v2.prepare_frame_v2_with_context_metadata(DATA_PATH)
    )
    groups = df["game_id"].to_numpy()
    splitter = GroupShuffleSplit(n_splits=1, test_size=cfg.test_size, random_state=cfg.seed)
    train_rows, _test_rows = next(splitter.split(df, df["target_log"].to_numpy(), groups=groups))
    train_games = pd.unique(df.iloc[train_rows]["game_id"])
    test_games = pd.unique(df.loc[~df["game_id"].isin(train_games), "game_id"])

    write_progress(
        {
            "status": "preparing_features_and_board_context",
            "model": MODEL_NAME,
            "device": str(device),
            "rows": int(len(df)),
            "train_games": int(len(train_games)),
            "test_games": int(len(test_games)),
            "prep_workers": int(cfg.prep_workers),
            "board_context_cache_dir": str(BOARD_CONTEXT_CACHE_DIR),
            "feature_revision": FEATURE_REVISION,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )

    feature_matrix, input_features, preprocessing = base.standardize_features(df, numeric_features, train_rows)
    preprocessing["target_info"] = v2.TARGET_INFO
    preprocessing["target_metadata_columns"] = v2.TARGET_METADATA_COLUMNS
    preprocessing["board_encoding"] = board_encoding

    X_train, y_train, mask_train, _train_meta = v2.make_sequences_v2(df, feature_matrix, train_games)
    X_test, y_test, mask_test, test_meta = v2.make_sequences_v2(df, feature_matrix, test_games)
    hint_move_train = make_current_hint_move_sequences(df, train_games, cfg.current_hint_planes)
    hint_move_test = make_current_hint_move_sequences(df, test_games, cfg.current_hint_planes)
    hint_value_train = make_current_hint_value_sequences(df, train_games, cfg.current_hint_planes)
    hint_value_test = make_current_hint_value_sequences(df, test_games, cfg.current_hint_planes)
    prev_own_hint_value_train = make_prev_own_hint_value_sequences(df, train_games)
    prev_own_hint_value_test = make_prev_own_hint_value_sequences(df, test_games)
    (
        board_train,
        board_move_train,
        board_test,
        board_move_test,
        board_context_cache_path,
        board_context_cache_hit,
    ) = load_or_build_board_contexts(df, train_games, test_games, cfg)

    train_loader = DataLoader(
        BoardSequenceDataset(
            X_train,
            board_train,
            board_move_train,
            hint_move_train,
            hint_value_train,
            prev_own_hint_value_train,
            y_train,
            mask_train,
        ),
        batch_size=cfg.batch_size,
        shuffle=True,
        num_workers=cfg.num_workers,
        pin_memory=device.type == "cuda",
    )
    test_loader = DataLoader(
        BoardSequenceDataset(
            X_test,
            board_test,
            board_move_test,
            hint_move_test,
            hint_value_test,
            prev_own_hint_value_test,
            y_test,
            mask_test,
        ),
        batch_size=cfg.batch_size,
        shuffle=False,
        num_workers=cfg.num_workers,
        pin_memory=device.type == "cuda",
    )

    write_progress(
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
            "input_features_with_missing_indicators": int(len(input_features)),
            "all_selected_features": int(len(all_features)),
            "categorical_features_excluded": int(len(categorical)),
            "numeric_features": int(len(numeric_features)),
            "metadata_numeric_features": int(len(metadata_numeric_features)),
            "board_shape": list(board_train.shape[1:]),
            "board_move_shape": list(board_move_train.shape[1:]),
            "current_hint_move_shape": list(hint_move_train.shape[1:]),
            "current_hint_value_shape": list(hint_value_train.shape[1:]),
            "prev_own_hint_value_shape": list(prev_own_hint_value_train.shape[1:]),
            "board_context_cache_path": str(board_context_cache_path),
            "board_context_cache_hit": bool(board_context_cache_hit),
            "feature_revision": FEATURE_REVISION,
            "board_encoding": board_encoding,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )

    model = BoardConditionedTCNRegressor(input_dim=X_train.shape[2], cfg=cfg).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    best_rmse = float("inf")
    best_epoch = 0
    no_improve = 0
    history = []
    start_epoch = 1
    best_path = OUT_DIR / f"{MODEL_NAME}.pt"
    named_best_path = OUT_DIR / f"{MODEL_NAME}_best.pt"
    latest_path = OUT_DIR / f"{MODEL_NAME}_latest.pt"

    if resume_path is not None:
        if not resume_path.exists():
            raise FileNotFoundError(f"resume checkpoint not found: {resume_path}")
        checkpoint = torch.load(resume_path, map_location=device)
        checkpoint_input_features = checkpoint.get("input_features")
        if checkpoint_input_features is not None and len(checkpoint_input_features) != X_train.shape[2]:
            raise ValueError(
                "resume checkpoint input feature count does not match current prepared data: "
                f"{len(checkpoint_input_features)} vs {X_train.shape[2]}"
            )
        model.load_state_dict(checkpoint["model_state_dict"])
        if "optimizer_state_dict" in checkpoint:
            optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        start_epoch = int(checkpoint.get("epoch", 0)) + 1
        if args.reset_best_on_resume:
            best_rmse = float("inf")
            best_epoch = 0
            no_improve = 0
        else:
            best_rmse = float(checkpoint.get("best_rmse_log", float("inf")))
            best_epoch = int(checkpoint.get("best_epoch", 0))
            no_improve = int(checkpoint.get("no_improve_epochs", 0))
            history_path = OUT_DIR / "training_history.csv"
            if history_path.exists():
                history = pd.read_csv(history_path).to_dict("records")
        write_progress(
            {
                "status": "resumed_checkpoint",
                "model": MODEL_NAME,
                "device": str(device),
                "resume_path": str(resume_path),
                "resume_checkpoint_epoch": int(checkpoint.get("epoch", 0)),
                "start_epoch": start_epoch,
                "epochs": cfg.epochs,
                "reset_best_on_resume": bool(args.reset_best_on_resume),
                "best_rmse_log": best_rmse,
                "best_epoch": best_epoch,
                "no_improve_epochs": no_improve,
                "latest_checkpoint_path": str(latest_path),
                "best_checkpoint_path": str(named_best_path),
                "feature_revision": FEATURE_REVISION,
                "elapsed_seconds": round(time.time() - t0, 2),
            }
        )

    for epoch in range(start_epoch, cfg.epochs + 1):
        epoch_lr = v2.learning_rate_for_epoch(epoch, cfg)
        v2.set_optimizer_lr(optimizer, epoch_lr)
        model.train()
        epoch_loss_sum = 0.0
        epoch_count = 0
        for xb, bb, bm, hm, hv, pv, yb, mb in train_loader:
            xb = xb.to(device, non_blocking=True)
            bb = bb.to(device, non_blocking=True)
            bm = bm.to(device, non_blocking=True)
            hm = hm.to(device, non_blocking=True)
            hv = hv.to(device, non_blocking=True)
            pv = pv.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            mb = mb.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            pred = model(xb, bb, bm, hm, hv, pv)
            loss = base.masked_mse(pred, yb, mb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
            optimizer.step()
            valid_count = int(mb.sum().item())
            epoch_loss_sum += float(loss.item()) * valid_count
            epoch_count += valid_count

        train_rmse = math.sqrt(epoch_loss_sum / max(epoch_count, 1))
        test_rmse = evaluate_rmse(model, test_loader, device)
        checkpoint_extra = {
            "train_rows": int(mask_train.sum()),
            "test_rows": int(mask_test.sum()),
            "train_games": int(len(train_games)),
            "test_games": int(len(test_games)),
            "max_seq_len": int(X_train.shape[1]),
            "input_dim": int(X_train.shape[2]),
        }
        if test_rmse < best_rmse - cfg.min_delta:
            best_rmse = test_rmse
            best_epoch = epoch
            no_improve = 0
            best_payload = build_checkpoint_payload(
                model=model,
                optimizer=optimizer,
                cfg=cfg,
                epoch=epoch,
                train_rmse=train_rmse,
                test_rmse=test_rmse,
                best_rmse=best_rmse,
                best_epoch=best_epoch,
                no_improve=no_improve,
                epoch_lr=epoch_lr,
                input_features=input_features,
                preprocessing=preprocessing,
                numeric_features=numeric_features,
                metadata_numeric_features=metadata_numeric_features,
                categorical=categorical,
                extra={**checkpoint_extra, "checkpoint_kind": "best"},
            )
            atomic_torch_save(best_payload, best_path)
            shutil.copy2(best_path, named_best_path)
        else:
            no_improve += 1

        if cfg.save_latest_every > 0 and (epoch % cfg.save_latest_every == 0 or epoch == cfg.epochs):
            latest_payload = build_checkpoint_payload(
                model=model,
                optimizer=optimizer,
                cfg=cfg,
                epoch=epoch,
                train_rmse=train_rmse,
                test_rmse=test_rmse,
                best_rmse=best_rmse,
                best_epoch=best_epoch,
                no_improve=no_improve,
                epoch_lr=epoch_lr,
                input_features=input_features,
                preprocessing=preprocessing,
                numeric_features=numeric_features,
                metadata_numeric_features=metadata_numeric_features,
                categorical=categorical,
                extra={**checkpoint_extra, "checkpoint_kind": "latest"},
            )
            atomic_torch_save(latest_payload, latest_path)

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
        write_progress(
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
                "latest_checkpoint_path": str(latest_path),
                "best_checkpoint_path": str(named_best_path),
                "legacy_best_checkpoint_path": str(best_path),
                "feature_revision": FEATURE_REVISION,
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

    eval_checkpoint_path = best_path if best_path.exists() else latest_path
    checkpoint = torch.load(eval_checkpoint_path, map_location=device)
    model.load_state_dict(checkpoint["model_state_dict"])
    pred_log = predict_flat(model, test_loader, device)
    metrics, pred_df = v2.compute_metrics_v2(test_meta, pred_log)
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
                    {"metric": "board_contexts_per_position", "value": len(BOARD_CONTEXT_NAMES)},
                    {
                        "metric": "board_cnn_channels",
                        "value": (
                            len(BOARD_BASE_CNN_CHANNELS)
                            + cfg.current_hint_planes
                            + current_hint_value_plane_count(cfg.current_hint_planes)
                            + PREV_OWN_HINT_VALUE_PLANES
                        ),
                    },
                    {"metric": "historical_move_planes_per_position", "value": 2},
                    {"metric": "current_hint_rank_planes_per_position", "value": int(cfg.current_hint_planes)},
                    {
                        "metric": "current_hint_value_planes_per_position",
                        "value": int(current_hint_value_plane_count(cfg.current_hint_planes)),
                    },
                    {
                        "metric": "prev_own_hint_value_planes_per_position",
                        "value": int(PREV_OWN_HINT_VALUE_PLANES),
                    },
                    {"metric": "numeric_base_features", "value": int(len(numeric_features))},
                    {"metric": "metadata_numeric_features", "value": int(len(metadata_numeric_features))},
                    {"metric": "excluded_categorical_features", "value": int(len(categorical))},
                    {"metric": "best_epoch", "value": int(best_epoch)},
                    {"metric": "eval_checkpoint", "value": str(eval_checkpoint_path)},
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
                "target_info": v2.TARGET_INFO,
                "target_metadata_columns": v2.TARGET_METADATA_COLUMNS,
                "board_encoding": board_encoding,
                "checkpoint_paths": {
                    "best": str(named_best_path),
                    "latest": str(latest_path),
                    "legacy_best": str(best_path),
                },
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
        "- model: `board-conditioned causal TCN with 8x8 CNN board encoder`",
        f"- device: `{device}`",
        f"- rows used: `{len(df)}`",
        f"- train rows: `{int(mask_train.sum())}`",
        f"- test rows: `{int(mask_test.sum())}`",
        f"- train games: `{len(train_games)}`",
        f"- test games: `{len(test_games)}`",
        f"- max seq len: `{X_train.shape[1]}`",
        f"- input numeric features: `{X_train.shape[2]}`",
        f"- board CNN channels: `{len(BOARD_BASE_CNN_CHANNELS) + cfg.current_hint_planes + current_hint_value_plane_count(cfg.current_hint_planes) + PREV_OWN_HINT_VALUE_PLANES}`",
        f"- current hint6 rank-specific planes: `{cfg.current_hint_planes}`",
        f"- current hint6 value planes: `{current_hint_value_plane_count(cfg.current_hint_planes)}`",
        f"- prev own hint6_1 value planes: `{PREV_OWN_HINT_VALUE_PLANES}`",
        f"- best epoch: `{best_epoch}`",
        f"- best checkpoint: `{named_best_path}`",
        f"- latest checkpoint: `{latest_path}`",
        "",
        "## Metrics",
        base.frame_to_markdown(metrics),
    ]
    (OUT_DIR / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    shutil.copy2(Path(__file__), OUT_DIR / "train_tcn_board_cnn_model.py")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in OUT_DIR.rglob("*"):
            zf.write(path, path.relative_to(OUT_DIR.parent))

    final_metrics = {row["metric"]: row["value"] for row in metrics.to_dict("records")}
    write_progress(
        {
            "status": "complete",
            "model": MODEL_NAME,
            "device": str(device),
            "best_epoch": best_epoch,
            "metrics": final_metrics,
            "outputs_dir": str(OUT_DIR),
            "zip_path": str(ZIP_PATH),
            "best_checkpoint_path": str(named_best_path),
            "latest_checkpoint_path": str(latest_path),
            "legacy_best_checkpoint_path": str(best_path),
            "feature_revision": FEATURE_REVISION,
            "context_metadata_path": str(CONTEXT_METADATA_PATH),
            "target_info": v2.TARGET_INFO,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )
    print(metrics.to_string(index=False))
    print(f"\nOUT_DIR\n{OUT_DIR}")
    print(f"\nZIP_PATH\n{ZIP_PATH}")


if __name__ == "__main__":
    main()
