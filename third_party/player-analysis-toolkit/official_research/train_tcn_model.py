from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ModuleNotFoundError:
    plt = None
import numpy as np
import pandas as pd
import torch
from scipy.stats import spearmanr
from sklearn.metrics import r2_score
from sklearn.model_selection import GroupShuffleSplit
from torch import nn
from torch.utils.data import DataLoader, Dataset

from train_unified_model import (
    RANDOM_STATE,
    add_engine_features,
    add_history_features,
    capped_seconds,
    clean_feature_frame,
    select_features,
)


MODEL_NAME = "tcn_pre_move_time_model"
DATA_PATH = Path("oq_reversi_5min_elo2000_hints/position_hints.csv")
OUT_DIR = Path("tcn_time_model_outputs")
ZIP_PATH = Path("tcn_time_model_outputs.zip")


@dataclass
class Config:
    epochs: int = 100
    batch_size: int = 128
    channels: int = 128
    levels: int = 6
    kernel_size: int = 3
    dropout: float = 0.15
    lr: float = 1.0e-3
    weight_decay: float = 1.0e-4
    patience: int = 18
    min_delta: float = 1.0e-4
    num_workers: int = 0
    test_size: float = 0.2
    seed: int = RANDOM_STATE


def write_progress(payload: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "updated_at_unix": time.time()}
    tmp_path = OUT_DIR / "progress.json.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(OUT_DIR / "progress.json")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def frame_to_markdown(df: pd.DataFrame) -> str:
    view = df.copy()
    for col in view.columns:
        if pd.api.types.is_float_dtype(view[col]):
            view[col] = view[col].map(lambda x: "" if pd.isna(x) else f"{x:.6g}")
        else:
            view[col] = view[col].astype(str)
    header = "| " + " | ".join(view.columns.astype(str)) + " |"
    sep = "| " + " | ".join(["---"] * len(view.columns)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in view.astype(str).to_numpy()]
    return "\n".join([header, sep, *rows])


def prepare_frame(data_path: Path) -> tuple[pd.DataFrame, list[str], list[str], list[str]]:
    df = pd.read_csv(data_path, low_memory=False)
    df = df[pd.to_numeric(df["actual_thinking_time_ms"], errors="coerce").notna()].copy()
    df["actual_thinking_time_ms"] = pd.to_numeric(df["actual_thinking_time_ms"], errors="coerce").clip(lower=0)
    df["target_log"] = np.log1p(df["actual_thinking_time_ms"] / 1000.0)

    df = add_engine_features(df)
    df = add_history_features(df)
    df = add_engine_features(df)

    features, categorical = select_features(df)
    numeric_features = [c for c in features if c not in set(categorical)]
    return df, features, categorical, numeric_features


def standardize_features(
    df: pd.DataFrame,
    numeric_features: list[str],
    train_rows: np.ndarray,
) -> tuple[np.ndarray, list[str], dict]:
    X = clean_feature_frame(df[numeric_features], [])
    raw = X.to_numpy(dtype=np.float32)
    missing = ~np.isfinite(raw)
    train_raw = raw[train_rows]

    means = np.nanmean(train_raw, axis=0).astype(np.float32)
    stds = np.nanstd(train_raw, axis=0).astype(np.float32)
    means = np.where(np.isfinite(means), means, 0.0).astype(np.float32)
    stds = np.where(np.isfinite(stds) & (stds > 1.0e-6), stds, 1.0).astype(np.float32)

    scaled = (raw - means) / stds
    scaled = np.where(np.isfinite(scaled), scaled, 0.0).astype(np.float32)

    missing_rates = missing.mean(axis=0)
    missing_cols = [c for c, rate in zip(numeric_features, missing_rates) if rate > 0.0]
    if missing_cols:
        missing_matrix = missing[:, [numeric_features.index(c) for c in missing_cols]].astype(np.float32)
        scaled = np.concatenate([scaled, missing_matrix], axis=1).astype(np.float32)

    input_features = numeric_features + [f"{c}__missing" for c in missing_cols]
    preprocessing = {
        "numeric_features": numeric_features,
        "missing_indicator_features": missing_cols,
        "input_features": input_features,
        "means": means.tolist(),
        "stds": stds.tolist(),
    }
    return scaled, input_features, preprocessing


def make_sequences(
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
        meta = df.iloc[row_idx][
            [
                c
                for c in [
                    "game_id",
                    "ply",
                    "move_index",
                    "player_id",
                    "side_to_move",
                    "remaining_before_s",
                    "hint6_1_nodes",
                    "last_2_time_avg_ms",
                    "actual_thinking_time_ms",
                    "target_log",
                ]
                if c in df.columns
            ]
        ].copy()
        meta["seq_game_index"] = game_idx
        meta["seq_pos"] = np.arange(length)
        meta_rows.append(meta)

    meta_df = pd.concat(meta_rows, ignore_index=True) if meta_rows else pd.DataFrame()
    return X_seq, y_seq, mask_seq, meta_df


class SequenceDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray, mask: np.ndarray) -> None:
        self.X = torch.from_numpy(X)
        self.y = torch.from_numpy(y)
        self.mask = torch.from_numpy(mask)

    def __len__(self) -> int:
        return self.X.shape[0]

    def __getitem__(self, idx: int):
        return self.X[idx], self.y[idx], self.mask[idx]


class Chomp1d(nn.Module):
    def __init__(self, chomp_size: int) -> None:
        super().__init__()
        self.chomp_size = chomp_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.chomp_size == 0:
            return x
        return x[:, :, : -self.chomp_size].contiguous()


class TemporalBlock(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int, dilation: int, dropout: float) -> None:
        super().__init__()
        padding = (kernel_size - 1) * dilation
        self.net = nn.Sequential(
            nn.Conv1d(in_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Conv1d(out_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.downsample = nn.Conv1d(in_channels, out_channels, 1) if in_channels != out_channels else nn.Identity()
        self.norm = nn.LayerNorm(out_channels)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.net(x) + self.downsample(x)
        return self.norm(y.transpose(1, 2)).transpose(1, 2)


class TCNRegressor(nn.Module):
    def __init__(
        self,
        input_dim: int,
        channels: int,
        levels: int,
        kernel_size: int,
        dropout: float,
    ) -> None:
        super().__init__()
        blocks = []
        in_channels = input_dim
        for level in range(levels):
            dilation = 2**level
            blocks.append(TemporalBlock(in_channels, channels, kernel_size, dilation, dropout))
            in_channels = channels
        self.tcn = nn.Sequential(*blocks)
        self.head = nn.Sequential(
            nn.Conv1d(channels, channels // 2, 1),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Conv1d(channels // 2, 1, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: batch, time, features
        y = self.tcn(x.transpose(1, 2))
        return self.head(y).squeeze(1)


def masked_mse(pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    valid = mask.bool()
    return torch.mean((pred[valid] - target[valid]) ** 2)


@torch.no_grad()
def evaluate_loss(model: nn.Module, loader: DataLoader, device: torch.device) -> float:
    model.eval()
    total_loss = 0.0
    total_count = 0
    for xb, yb, mb in loader:
        xb = xb.to(device, non_blocking=True)
        yb = yb.to(device, non_blocking=True)
        mb = mb.to(device, non_blocking=True)
        pred = model(xb)
        valid = mb.bool()
        loss_sum = torch.sum((pred[valid] - yb[valid]) ** 2).item()
        total_loss += loss_sum
        total_count += int(valid.sum().item())
    return math.sqrt(total_loss / max(total_count, 1))


@torch.no_grad()
def predict_flat(model: nn.Module, loader: DataLoader, device: torch.device) -> np.ndarray:
    model.eval()
    chunks = []
    for xb, _yb, mb in loader:
        xb = xb.to(device, non_blocking=True)
        pred = model(xb).detach().cpu().numpy()
        mask = mb.numpy().astype(bool)
        chunks.append(pred[mask])
    return np.concatenate(chunks)


def compute_metrics(meta_df: pd.DataFrame, pred_log: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    remaining = meta_df["remaining_before_s"] if "remaining_before_s" in meta_df.columns else None
    pred_s = capped_seconds(pred_log, remaining)
    actual_s = pd.to_numeric(meta_df["actual_thinking_time_ms"], errors="coerce").to_numpy(dtype=np.float64) / 1000.0
    actual_log = meta_df["target_log"].to_numpy(dtype=np.float64)

    spear = spearmanr(pred_s, actual_s, nan_policy="omit").statistic
    metrics = pd.DataFrame(
        [
            {"metric": "MAE_seconds", "value": float(np.mean(np.abs(pred_s - actual_s)))},
            {"metric": "Median_AE_seconds", "value": float(np.median(np.abs(pred_s - actual_s)))},
            {"metric": "RMSE_log", "value": float(math.sqrt(np.mean((pred_log - actual_log) ** 2)))},
            {"metric": "R2_seconds", "value": float(r2_score(actual_s, pred_s))},
            {"metric": "R2_log", "value": float(r2_score(actual_log, pred_log))},
            {
                "metric": "Spearman_pred_seconds_vs_actual_seconds",
                "value": float(spear) if pd.notna(spear) else np.nan,
            },
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


def write_plots(pred_df: pd.DataFrame) -> None:
    plot_df = pred_df[["actual_s", "pred_s"]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(plot_df) > 25000:
        plot_df = plot_df.sample(25000, random_state=RANDOM_STATE)
    max_s = float(np.nanpercentile(np.r_[plot_df["actual_s"].to_numpy(), plot_df["pred_s"].to_numpy()], 99.5))
    max_s = max(max_s, 1.0)
    fig, ax = plt.subplots(figsize=(7.2, 6.0), dpi=160)
    ax.scatter(plot_df["actual_s"], plot_df["pred_s"], s=7, alpha=0.22, linewidths=0)
    ax.plot([0, max_s], [0, max_s], color="black", linewidth=1.2)
    ax.set_xlim(0, max_s)
    ax.set_ylim(0, max_s)
    ax.set_xlabel("Actual thinking time (s)")
    ax.set_ylabel("Predicted thinking time (s)")
    ax.set_title("TCN pre-move time model: pred vs actual")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "scatter_pred_vs_actual_seconds.png")
    plt.close(fig)

    diag_frames = []
    for feature in ["hint6_1_nodes", "last_2_time_avg_ms"]:
        if feature not in pred_df.columns:
            continue
        d = pred_df[[feature, "actual_s", "pred_s", "residual_s"]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) == 0:
            continue
        d["feature_bin"] = pd.qcut(d[feature], q=10, labels=False, duplicates="drop")
        bins = (
            d.groupby("feature_bin", dropna=False)
            .agg(
                count=("actual_s", "size"),
                feature_min=(feature, "min"),
                feature_max=(feature, "max"),
                feature_mean=(feature, "mean"),
                pred_mean_s=("pred_s", "mean"),
                actual_mean_s=("actual_s", "mean"),
                residual_mean_s=("residual_s", "mean"),
                residual_median_s=("residual_s", "median"),
            )
            .reset_index()
        )
        bins.insert(0, "feature", feature)
        diag_frames.append(bins)
    if diag_frames:
        pd.concat(diag_frames, ignore_index=True).to_csv(OUT_DIR / "diagnostic_feature_bins.csv", index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=Config.epochs)
    parser.add_argument("--batch-size", type=int, default=Config.batch_size)
    parser.add_argument("--channels", type=int, default=Config.channels)
    parser.add_argument("--levels", type=int, default=Config.levels)
    parser.add_argument("--dropout", type=float, default=Config.dropout)
    parser.add_argument("--lr", type=float, default=Config.lr)
    parser.add_argument("--patience", type=int, default=Config.patience)
    parser.add_argument("--cpu", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = Config(
        epochs=args.epochs,
        batch_size=args.batch_size,
        channels=args.channels,
        levels=args.levels,
        dropout=args.dropout,
        lr=args.lr,
        patience=args.patience,
    )
    set_seed(cfg.seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUT_DIR.iterdir():
        if path.is_file():
            path.unlink()

    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    write_progress(
        {
            "status": "starting",
            "model": MODEL_NAME,
            "config": asdict(cfg),
            "device": str(device),
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }
    )

    t0 = time.time()
    df, all_features, categorical, numeric_features = prepare_frame(DATA_PATH)
    groups = df["game_id"].to_numpy()
    splitter = GroupShuffleSplit(n_splits=1, test_size=cfg.test_size, random_state=cfg.seed)
    train_rows, test_rows = next(splitter.split(df, df["target_log"].to_numpy(), groups=groups))
    train_games = pd.unique(df.iloc[train_rows]["game_id"])
    test_games = pd.unique(df.iloc[test_rows]["game_id"])

    feature_matrix, input_features, preprocessing = standardize_features(df, numeric_features, train_rows)
    X_train, y_train, mask_train, train_meta = make_sequences(df, feature_matrix, train_games)
    X_test, y_test, mask_test, test_meta = make_sequences(df, feature_matrix, test_games)

    train_dataset = SequenceDataset(X_train, y_train, mask_train)
    test_dataset = SequenceDataset(X_test, y_test, mask_test)
    train_loader = DataLoader(
        train_dataset,
        batch_size=cfg.batch_size,
        shuffle=True,
        num_workers=cfg.num_workers,
        pin_memory=device.type == "cuda",
    )
    test_loader = DataLoader(
        test_dataset,
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
            "all_selected_features": int(len(all_features)),
            "categorical_features_excluded": int(len(categorical)),
            "numeric_features": int(len(numeric_features)),
            "input_features_with_missing_indicators": int(len(input_features)),
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )

    model = TCNRegressor(
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
        model.train()
        epoch_loss_sum = 0.0
        epoch_count = 0
        for xb, yb, mb in train_loader:
            xb = xb.to(device, non_blocking=True)
            yb = yb.to(device, non_blocking=True)
            mb = mb.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = masked_mse(pred, yb, mb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
            optimizer.step()

            valid_count = int(mb.sum().item())
            epoch_loss_sum += float(loss.item()) * valid_count
            epoch_count += valid_count

        train_rmse = math.sqrt(epoch_loss_sum / max(epoch_count, 1))
        test_rmse = evaluate_loss(model, test_loader, device)
        improved = test_rmse < best_rmse - cfg.min_delta
        if improved:
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
                    "categorical_features_excluded": categorical,
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
            "lr": optimizer.param_groups[0]["lr"],
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
                "elapsed_seconds": round(time.time() - t0, 2),
            }
        )
        print(
            f"epoch={epoch:03d} train_rmse_log={train_rmse:.5f} "
            f"test_rmse_log={test_rmse:.5f} best={best_rmse:.5f}@{best_epoch}",
            flush=True,
        )
        if no_improve >= cfg.patience:
            break

    checkpoint = torch.load(best_path, map_location=device)
    model.load_state_dict(checkpoint["model_state_dict"])
    pred_log = predict_flat(model, test_loader, device)
    metrics, pred_df = compute_metrics(test_meta, pred_log)
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
    (OUT_DIR / "config.json").write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    write_plots(pred_df)

    summary = [
        f"# {MODEL_NAME}",
        "",
        f"- data: `{DATA_PATH}`",
        f"- model: `causal TCN`",
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
        "## Metrics",
        frame_to_markdown(metrics),
        "",
        "Note: this first TCN uses numeric pre-move features plus missingness indicators. "
        "Categorical columns are intentionally excluded for the initial sequence baseline.",
    ]
    (OUT_DIR / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    shutil.copy2(Path(__file__), OUT_DIR / "train_tcn_model.py")

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
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )
    print(metrics.to_string(index=False))
    print(f"\nOUT_DIR\n{OUT_DIR}")
    print(f"\nZIP_PATH\n{ZIP_PATH}")


if __name__ == "__main__":
    main()
