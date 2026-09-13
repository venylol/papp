from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
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


MODEL_NAME = "causal_transformer_board_time_model"
DATA_PATH = v2.DATA_PATH
CONTEXT_METADATA_PATH = v2.CONTEXT_METADATA_PATH
OUT_DIR = Path("causal_transformer_board_time_model_outputs")
ZIP_PATH = Path("causal_transformer_board_time_model_outputs.zip")
CACHE_DIR = Path(".cache_causal_transformer_board")
FEATURE_REVISION = f"{v2.FEATURE_REVISION}_explicit_current_prev_boards_moves_causal_transformer"
BOARD_CONTEXT_NAMES = ["current", "prev_opponent", "prev_own"]
BOARD_ENCODING = {
    "shape": "batch x time x 3 x 64 flattened 8x8 board tokens",
    "contexts": BOARD_CONTEXT_NAMES,
    "tokens_per_position": 3 * 64,
    "token_ids": {"padding": 0, "empty": 1, "X": 2, "O": 3},
    "move_shape": "batch x time x 3; current move is always hidden as 0",
    "move_token_ids": {"no_move_or_hidden_current": 0, "a1": 1, "h8": 64},
    "source": "board column after context metadata validation",
}


@dataclass
class BoardTransformerConfig:
    epochs: int = 600
    batch_size: int = 128
    micro_batch_size: int = 16
    prep_workers: int = 16
    d_model: int = 192
    n_heads: int = 6
    layers: int = 4
    dim_feedforward: int = 512
    board_d_model: int = 96
    board_heads: int = 4
    board_layers: int = 2
    board_feedforward: int = 256
    dropout: float = 0.15
    lr: float = 5.0e-4
    weight_decay: float = 1.0e-4
    patience: int = 105
    min_delta: float = 1.0e-4
    warmup_epochs: int = 12
    min_lr_ratio: float = 0.08
    num_workers: int = 0
    test_size: float = 0.1
    seed: int = v2.RANDOM_STATE


class BoardSequenceDataset(Dataset):
    def __init__(
        self,
        X: np.ndarray,
        boards: np.ndarray,
        board_moves: np.ndarray,
        y: np.ndarray,
        mask: np.ndarray,
    ) -> None:
        self.X = torch.from_numpy(X)
        self.boards = torch.from_numpy(boards)
        self.board_moves = torch.from_numpy(board_moves)
        self.y = torch.from_numpy(y)
        self.mask = torch.from_numpy(mask)

    def __len__(self) -> int:
        return self.X.shape[0]

    def __getitem__(self, idx: int):
        return self.X[idx], self.boards[idx], self.board_moves[idx], self.y[idx], self.mask[idx]


class BoardStateEncoder(nn.Module):
    def __init__(self, cfg: BoardTransformerConfig) -> None:
        super().__init__()
        self.cls_token = nn.Parameter(torch.zeros(1, 1, cfg.board_d_model))
        self.token_embedding = nn.Embedding(4, cfg.board_d_model, padding_idx=0)
        self.move_embedding = nn.Embedding(65, cfg.board_d_model, padding_idx=0)
        self.context_embedding = nn.Embedding(len(BOARD_CONTEXT_NAMES), cfg.board_d_model)
        self.square_embedding = nn.Parameter(torch.zeros(1, 64, cfg.board_d_model))
        layer = nn.TransformerEncoderLayer(
            d_model=cfg.board_d_model,
            nhead=cfg.board_heads,
            dim_feedforward=cfg.board_feedforward,
            dropout=cfg.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=cfg.board_layers)
        self.norm = nn.LayerNorm(cfg.board_d_model)
        nn.init.normal_(self.cls_token, mean=0.0, std=0.02)
        nn.init.normal_(self.square_embedding, mean=0.0, std=0.02)

    def forward(self, board_tokens: torch.Tensor, board_move_tokens: torch.Tensor) -> torch.Tensor:
        batch, seq_len, contexts, squares = board_tokens.shape
        if contexts != len(BOARD_CONTEXT_NAMES):
            raise ValueError(f"expected {len(BOARD_CONTEXT_NAMES)} board contexts, got {contexts}")
        if squares != 64:
            raise ValueError(f"expected 64 board squares, got {squares}")
        if board_move_tokens.shape != (batch, seq_len, contexts):
            raise ValueError(
                f"expected board move tokens shape {(batch, seq_len, contexts)}, got {tuple(board_move_tokens.shape)}"
            )
        flat = board_tokens.reshape(batch * seq_len * contexts, squares).long()
        flat_moves = board_move_tokens.reshape(batch * seq_len * contexts).long()
        context_ids = torch.arange(contexts, device=board_tokens.device).view(1, 1, contexts)
        context_ids = context_ids.expand(batch, seq_len, contexts).reshape(-1)
        board_pad = flat.eq(0).all(dim=1)
        context_h = self.context_embedding(context_ids).unsqueeze(1)
        h = self.token_embedding(flat) + self.square_embedding + context_h
        cls = self.cls_token.expand(flat.shape[0], -1, -1)
        cls = cls + context_h + self.move_embedding(flat_moves).unsqueeze(1)
        h = torch.cat([cls, h], dim=1)
        encoded = self.encoder(h)
        summary = self.norm(encoded[:, 0])
        summary = summary.masked_fill(board_pad.unsqueeze(-1), 0.0)
        return summary.reshape(batch, seq_len, contexts, -1).reshape(batch, seq_len, contexts * summary.shape[-1])


class CausalBoardTransformerTimeModel(nn.Module):
    def __init__(self, input_dim: int, max_seq_len: int, cfg: BoardTransformerConfig) -> None:
        super().__init__()
        self.micro_batch_size = cfg.micro_batch_size
        self.input_norm = nn.LayerNorm(input_dim)
        self.input_proj = nn.Linear(input_dim, cfg.d_model)
        self.board_encoder = BoardStateEncoder(cfg)
        self.board_proj = nn.Linear(cfg.board_d_model * len(BOARD_CONTEXT_NAMES), cfg.d_model)
        self.pos_embedding = nn.Parameter(torch.zeros(1, max_seq_len, cfg.d_model))
        layer = nn.TransformerEncoderLayer(
            d_model=cfg.d_model,
            nhead=cfg.n_heads,
            dim_feedforward=cfg.dim_feedforward,
            dropout=cfg.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=cfg.layers)
        self.dropout = nn.Dropout(cfg.dropout)
        self.head = nn.Sequential(
            nn.LayerNorm(cfg.d_model),
            nn.Linear(cfg.d_model, cfg.d_model // 2),
            nn.GELU(),
            nn.Dropout(cfg.dropout),
            nn.Linear(cfg.d_model // 2, 1),
        )
        nn.init.normal_(self.pos_embedding, mean=0.0, std=0.02)

    def forward(
        self,
        x: torch.Tensor,
        board_tokens: torch.Tensor,
        board_move_tokens: torch.Tensor,
        mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        seq_len = x.shape[1]
        h = self.input_proj(self.input_norm(x))
        h = h + self.board_proj(self.board_encoder(board_tokens, board_move_tokens))
        h = self.dropout(h + self.pos_embedding[:, :seq_len])
        causal_mask = torch.triu(
            torch.ones(seq_len, seq_len, device=x.device, dtype=torch.bool),
            diagonal=1,
        )
        padding_mask = None if mask is None else ~mask.bool()
        h = self.encoder(h, mask=causal_mask, src_key_padding_mask=padding_mask)
        return self.head(h).squeeze(-1)


def encode_board64(board) -> np.ndarray:
    text = v2.normalize_board64_for_compare(board)
    values = np.zeros(64, dtype=np.uint8)
    for idx, ch in enumerate(text):
        if ch == "X":
            values[idx] = 2
        elif ch == "O":
            values[idx] = 3
        else:
            values[idx] = 1
    return values


def encode_move_token(move) -> int:
    idx = v2.move_to_index(move)
    return 0 if idx is None else idx + 1


def build_board_context_chunk(args) -> tuple[np.ndarray, np.ndarray]:
    game_records, max_len = args
    board_seq = np.zeros((len(game_records), max_len, len(BOARD_CONTEXT_NAMES), 64), dtype=np.uint8)
    move_seq = np.zeros((len(game_records), max_len, len(BOARD_CONTEXT_NAMES)), dtype=np.uint8)
    for game_idx, record in enumerate(game_records):
        sides, boards, moves = record
        board_matrix = np.stack([encode_board64(board) for board in boards]).astype(np.uint8)
        move_tokens = np.asarray([encode_move_token(move) for move in moves], dtype=np.uint8)
        for pos, side in enumerate(sides):
            board_seq[game_idx, pos, 0] = board_matrix[pos]

            prev_opponent_pos = None
            prev_own_pos = None
            for prev_pos in range(pos - 1, -1, -1):
                if prev_own_pos is None and sides[prev_pos] == side:
                    prev_own_pos = prev_pos
                if prev_opponent_pos is None and sides[prev_pos] != side:
                    prev_opponent_pos = prev_pos
                if prev_opponent_pos is not None and prev_own_pos is not None:
                    break

            for context_idx, prev_pos in [(1, prev_opponent_pos), (2, prev_own_pos)]:
                if prev_pos is None:
                    continue
                board_seq[game_idx, pos, context_idx] = board_matrix[prev_pos]
                move_seq[game_idx, pos, context_idx] = move_tokens[prev_pos]
    return board_seq, move_seq


def make_board_context_sequences(
    df: pd.DataFrame,
    game_ids: np.ndarray,
    prep_workers: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    use = df["game_id"].isin(game_ids).to_numpy()
    game_order = list(pd.unique(df.loc[use, "game_id"]))
    max_len = int(df.loc[use].groupby("game_id", sort=False).size().max())

    row_by_game = df.loc[use].groupby("game_id", sort=False).indices
    game_records = []
    for game_id in game_order:
        row_idx = np.asarray(row_by_game[game_id], dtype=np.int64)
        view = df.iloc[row_idx]
        game_records.append(
            (
                [str(side).strip().lower() for side in view["side_to_move"]],
                [str(board) for board in view["board"]],
                [str(move) for move in view["actual_move"]],
            )
        )

    workers = max(1, min(int(prep_workers), len(game_records)))
    main_path = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if main_path is None or main_path.name in {"-", "<stdin>"} or not main_path.exists():
        workers = 1
    if workers == 1:
        return build_board_context_chunk((game_records, max_len))

    chunk_size = math.ceil(len(game_records) / workers)
    chunks = [game_records[i : i + chunk_size] for i in range(0, len(game_records), chunk_size)]
    chunks = [chunk for chunk in chunks if chunk]
    try:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            parts = list(executor.map(build_board_context_chunk, [(chunk, max_len) for chunk in chunks]))
    except Exception:
        return build_board_context_chunk((game_records, max_len))

    board_seq = np.concatenate([part[0] for part in parts], axis=0)
    move_seq = np.concatenate([part[1] for part in parts], axis=0)
    return board_seq, move_seq


def board_context_cache_path(train_games: np.ndarray, test_games: np.ndarray, cfg: BoardTransformerConfig) -> Path:
    DATA_PATH.resolve()
    key_payload = {
        "feature_revision": FEATURE_REVISION,
        "board_encoding": BOARD_ENCODING,
        "data_path": str(DATA_PATH),
        "data_mtime_ns": DATA_PATH.stat().st_mtime_ns if DATA_PATH.exists() else None,
        "context_metadata_path": str(CONTEXT_METADATA_PATH),
        "context_metadata_mtime_ns": CONTEXT_METADATA_PATH.stat().st_mtime_ns
        if CONTEXT_METADATA_PATH.exists()
        else None,
        "seed": cfg.seed,
        "test_size": cfg.test_size,
        "train_games": [str(game) for game in train_games],
        "test_games": [str(game) for game in test_games],
    }
    digest = hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"board_context_{digest}.npz"


def load_or_build_board_contexts(
    df: pd.DataFrame,
    train_games: np.ndarray,
    test_games: np.ndarray,
    cfg: BoardTransformerConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Path, bool]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = board_context_cache_path(train_games, test_games, cfg)
    if cache_path.exists():
        data = np.load(cache_path)
        return (
            data["board_train"],
            data["board_move_train"],
            data["board_test"],
            data["board_move_test"],
            cache_path,
            True,
        )

    board_train, board_move_train = make_board_context_sequences(df, train_games, cfg.prep_workers)
    board_test, board_move_test = make_board_context_sequences(df, test_games, cfg.prep_workers)
    tmp_path = cache_path.with_suffix(".tmp.npz")
    np.savez(
        tmp_path,
        board_train=board_train,
        board_move_train=board_move_train,
        board_test=board_test,
        board_move_test=board_move_test,
    )
    tmp_path.replace(cache_path)
    return board_train, board_move_train, board_test, board_move_test, cache_path, False


def batch_slices(batch_size: int, micro_batch_size: int):
    step = max(1, int(micro_batch_size))
    for start in range(0, batch_size, step):
        yield slice(start, min(start + step, batch_size))


@torch.no_grad()
def evaluate_rmse(model: nn.Module, loader: DataLoader, device: torch.device) -> float:
    model.eval()
    total_loss = 0.0
    total_count = 0
    for xb, bb, bm, yb, mb in loader:
        xb = xb.to(device, non_blocking=True)
        bb = bb.to(device, non_blocking=True)
        bm = bm.to(device, non_blocking=True)
        yb = yb.to(device, non_blocking=True)
        mb = mb.to(device, non_blocking=True)
        for sl in batch_slices(xb.shape[0], model.micro_batch_size):
            pred = model(xb[sl], bb[sl], bm[sl], mb[sl])
            valid = mb[sl].bool()
            total_loss += torch.sum((pred[valid] - yb[sl][valid]) ** 2).item()
            total_count += int(valid.sum().item())
    return math.sqrt(total_loss / max(total_count, 1))


@torch.no_grad()
def predict_flat(model: nn.Module, loader: DataLoader, device: torch.device) -> np.ndarray:
    model.eval()
    chunks = []
    for xb, bb, bm, _yb, mb in loader:
        xb = xb.to(device, non_blocking=True)
        bb = bb.to(device, non_blocking=True)
        bm = bm.to(device, non_blocking=True)
        mb_device = mb.to(device, non_blocking=True)
        for sl in batch_slices(xb.shape[0], model.micro_batch_size):
            pred = model(xb[sl], bb[sl], bm[sl], mb_device[sl]).detach().cpu().numpy()
            chunks.append(pred[mb[sl].numpy().astype(bool)])
    return np.concatenate(chunks)


def write_progress(payload: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "updated_at_unix": time.time()}
    tmp_path = OUT_DIR / "progress.json.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(OUT_DIR / "progress.json")


def clean_output_dir() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUT_DIR.iterdir():
        if path.is_file():
            path.unlink()


def learning_rate_for_epoch(epoch: int, cfg: BoardTransformerConfig) -> float:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=BoardTransformerConfig.epochs)
    parser.add_argument("--batch-size", type=int, default=BoardTransformerConfig.batch_size)
    parser.add_argument("--micro-batch-size", type=int, default=BoardTransformerConfig.micro_batch_size)
    parser.add_argument("--prep-workers", type=int, default=BoardTransformerConfig.prep_workers)
    parser.add_argument("--d-model", type=int, default=BoardTransformerConfig.d_model)
    parser.add_argument("--n-heads", type=int, default=BoardTransformerConfig.n_heads)
    parser.add_argument("--layers", type=int, default=BoardTransformerConfig.layers)
    parser.add_argument("--dim-feedforward", type=int, default=BoardTransformerConfig.dim_feedforward)
    parser.add_argument("--board-d-model", type=int, default=BoardTransformerConfig.board_d_model)
    parser.add_argument("--board-heads", type=int, default=BoardTransformerConfig.board_heads)
    parser.add_argument("--board-layers", type=int, default=BoardTransformerConfig.board_layers)
    parser.add_argument("--board-feedforward", type=int, default=BoardTransformerConfig.board_feedforward)
    parser.add_argument("--dropout", type=float, default=BoardTransformerConfig.dropout)
    parser.add_argument("--lr", type=float, default=BoardTransformerConfig.lr)
    parser.add_argument("--patience", type=int, default=BoardTransformerConfig.patience)
    parser.add_argument("--test-size", type=float, default=BoardTransformerConfig.test_size)
    parser.add_argument("--warmup-epochs", type=int, default=BoardTransformerConfig.warmup_epochs)
    parser.add_argument("--min-lr-ratio", type=float, default=BoardTransformerConfig.min_lr_ratio)
    parser.add_argument("--cpu", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = BoardTransformerConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        micro_batch_size=args.micro_batch_size,
        prep_workers=args.prep_workers,
        d_model=args.d_model,
        n_heads=args.n_heads,
        layers=args.layers,
        dim_feedforward=args.dim_feedforward,
        board_d_model=args.board_d_model,
        board_heads=args.board_heads,
        board_layers=args.board_layers,
        board_feedforward=args.board_feedforward,
        dropout=args.dropout,
        lr=args.lr,
        patience=args.patience,
        test_size=args.test_size,
        warmup_epochs=args.warmup_epochs,
        min_lr_ratio=args.min_lr_ratio,
    )
    v2.set_seed(cfg.seed)
    clean_output_dir()
    base.MODEL_NAME = MODEL_NAME
    base.OUT_DIR = OUT_DIR
    base.ZIP_PATH = ZIP_PATH
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    t0 = time.time()

    write_progress(
        {
            "status": "starting",
            "model": MODEL_NAME,
            "config": asdict(cfg),
            "device": str(device),
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "feature_revision": FEATURE_REVISION,
            "context_metadata_path": str(CONTEXT_METADATA_PATH),
            "target_info": v2.TARGET_INFO,
            "board_encoding": BOARD_ENCODING,
        }
    )

    write_progress(
        {
            "status": "preparing_frame",
            "model": MODEL_NAME,
            "device": str(device),
            "config": asdict(cfg),
            "feature_revision": FEATURE_REVISION,
            "context_metadata_path": str(CONTEXT_METADATA_PATH),
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )
    df, all_features, categorical, numeric_features, metadata_numeric_features = (
        v2.prepare_frame_v2_with_context_metadata(DATA_PATH)
    )
    write_progress(
        {
            "status": "preparing_split_and_features",
            "model": MODEL_NAME,
            "device": str(device),
            "rows": int(len(df)),
            "config": asdict(cfg),
            "feature_revision": FEATURE_REVISION,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )
    groups = df["game_id"].to_numpy()
    splitter = GroupShuffleSplit(n_splits=1, test_size=cfg.test_size, random_state=cfg.seed)
    train_rows, _test_rows = next(splitter.split(df, df["target_log"].to_numpy(), groups=groups))
    train_games = pd.unique(df.iloc[train_rows]["game_id"])
    test_games = pd.unique(df.loc[~df["game_id"].isin(train_games), "game_id"])

    feature_matrix, input_features, preprocessing = base.standardize_features(df, numeric_features, train_rows)
    preprocessing["target_info"] = v2.TARGET_INFO
    preprocessing["target_metadata_columns"] = v2.TARGET_METADATA_COLUMNS
    preprocessing["board_encoding"] = BOARD_ENCODING

    X_train, y_train, mask_train, _train_meta = v2.make_sequences_v2(df, feature_matrix, train_games)
    X_test, y_test, mask_test, test_meta = v2.make_sequences_v2(df, feature_matrix, test_games)
    write_progress(
        {
            "status": "preparing_board_context",
            "model": MODEL_NAME,
            "device": str(device),
            "train_games": int(len(train_games)),
            "test_games": int(len(test_games)),
            "prep_workers": int(cfg.prep_workers),
            "board_context_cache_dir": str(CACHE_DIR),
            "feature_revision": FEATURE_REVISION,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )
    (
        board_train,
        board_move_train,
        board_test,
        board_move_test,
        board_context_cache_path,
        board_context_cache_hit,
    ) = load_or_build_board_contexts(df, train_games, test_games, cfg)

    train_loader = DataLoader(
        BoardSequenceDataset(X_train, board_train, board_move_train, y_train, mask_train),
        batch_size=cfg.batch_size,
        shuffle=True,
        num_workers=cfg.num_workers,
        pin_memory=device.type == "cuda",
    )
    test_loader = DataLoader(
        BoardSequenceDataset(X_test, board_test, board_move_test, y_test, mask_test),
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
            "prep_workers": int(cfg.prep_workers),
            "board_context_cache_path": str(board_context_cache_path),
            "board_context_cache_hit": bool(board_context_cache_hit),
            "feature_revision": FEATURE_REVISION,
            "board_encoding": BOARD_ENCODING,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )

    model = CausalBoardTransformerTimeModel(
        input_dim=X_train.shape[2],
        max_seq_len=X_train.shape[1],
        cfg=cfg,
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
        for xb, bb, bm, yb, mb in train_loader:
            xb = xb.to(device, non_blocking=True)
            bb = bb.to(device, non_blocking=True)
            bm = bm.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            mb = mb.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            valid_total = int(mb.sum().item())
            if valid_total == 0:
                continue
            batch_loss_sum = 0.0
            for sl in batch_slices(xb.shape[0], cfg.micro_batch_size):
                pred = model(xb[sl], bb[sl], bm[sl], mb[sl])
                valid = mb[sl].bool()
                loss_sum = torch.sum((pred[valid] - yb[sl][valid]) ** 2)
                (loss_sum / valid_total).backward()
                batch_loss_sum += float(loss_sum.detach().item())
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
            optimizer.step()
            epoch_loss_sum += batch_loss_sum
            epoch_count += valid_total

        train_rmse = math.sqrt(epoch_loss_sum / max(epoch_count, 1))
        test_rmse = evaluate_rmse(model, test_loader, device)
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
                    "target_info": v2.TARGET_INFO,
                    "target_metadata_columns": v2.TARGET_METADATA_COLUMNS,
                    "board_encoding": BOARD_ENCODING,
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

    checkpoint = torch.load(best_path, map_location=device)
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
                    {"metric": "board_square_tokens_per_position", "value": 64 * len(BOARD_CONTEXT_NAMES)},
                    {"metric": "historical_move_tokens_per_position", "value": 2},
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
                "target_info": v2.TARGET_INFO,
                "target_metadata_columns": v2.TARGET_METADATA_COLUMNS,
                "board_encoding": BOARD_ENCODING,
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
        "- model: `causal Transformer over move sequence + explicit current/previous 8x8 board-state encoder`",
        f"- device: `{device}`",
        f"- rows used: `{len(df)}`",
        f"- train rows: `{int(mask_train.sum())}`",
        f"- test rows: `{int(mask_test.sum())}`",
        f"- train games: `{len(train_games)}`",
        f"- test games: `{len(test_games)}`",
        f"- max seq len: `{X_train.shape[1]}`",
        f"- input numeric features: `{X_train.shape[2]}`",
        f"- board contexts per position: `{len(BOARD_CONTEXT_NAMES)}`",
        f"- board square tokens per position: `{64 * len(BOARD_CONTEXT_NAMES)}`",
        "- historical move tokens per position: `2`",
        f"- best epoch: `{best_epoch}`",
        "",
        "## Board Encoding",
        "- contexts: `current`, `prev_opponent`, `prev_own`",
        "- `0`: padding",
        "- `1`: empty square",
        "- `2`: X disc",
        "- `3`: O disc",
        "- historical move tokens: `1..64` for `a1..h8` on `prev_opponent` and `prev_own`",
        "- current actual move token: always `0` and hidden from the model",
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
        "- explicit current raw board state as 8x8 tokens",
        "- explicit previous opponent and previous own board states as 8x8 tokens",
        "- explicit historical actual move location for previous opponent and previous own board contexts",
        "",
        "## Metrics",
        base.frame_to_markdown(metrics),
    ]
    (OUT_DIR / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    shutil.copy2(Path(__file__), OUT_DIR / "train_causal_transformer_board_model.py")

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
            "feature_revision": FEATURE_REVISION,
            "context_metadata_path": str(CONTEXT_METADATA_PATH),
            "target_info": v2.TARGET_INFO,
            "board_encoding": BOARD_ENCODING,
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )
    print(metrics.to_string(index=False))
    print(f"\nOUT_DIR\n{OUT_DIR}")
    print(f"\nZIP_PATH\n{ZIP_PATH}")


if __name__ == "__main__":
    main()
