"""Checkpoint-compatible board-CNN causal-TCN backbone.

The module layout intentionally matches the official time-model checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

BOARD_CONTEXT_NAMES = ("current", "prev_opponent", "prev_own")
BOARD_BASE_CNN_CHANNELS = (
    "current_empty", "current_X", "current_O",
    "prev_opponent_empty", "prev_opponent_X", "prev_opponent_O",
    "prev_own_empty", "prev_own_X", "prev_own_O",
    "prev_opponent_actual_move", "prev_own_actual_move",
)
HINT6_VALUE_GAP_MAX_RANK = 4
PREV_OWN_HINT_VALUE_PLANES = 2


@dataclass(frozen=True)
class ModelConfig:
    input_dim: int = 362
    channels: int = 128
    levels: int = 6
    kernel_size: int = 3
    dropout: float = 0.15
    board_embedding_dim: int = 96
    board_channels: int = 64
    board_dropout: float = 0.10
    current_hint_planes: int = 6

    @classmethod
    def from_checkpoint(cls, checkpoint: dict) -> "ModelConfig":
        cfg = checkpoint["config"]
        return cls(
            input_dim=int(checkpoint["input_dim"]),
            channels=int(cfg["channels"]),
            levels=int(cfg["levels"]),
            kernel_size=int(cfg["kernel_size"]),
            dropout=float(cfg["dropout"]),
            board_embedding_dim=int(cfg["board_embedding_dim"]),
            board_channels=int(cfg["board_channels"]),
            board_dropout=float(cfg["board_dropout"]),
            current_hint_planes=int(checkpoint["board_encoding"]["current_hint_planes"]),
        )


class Chomp1d(nn.Module):
    def __init__(self, chomp_size: int) -> None:
        super().__init__()
        self.chomp_size = chomp_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x if self.chomp_size == 0 else x[:, :, :-self.chomp_size].contiguous()


class TemporalBlock(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int, dilation: int, dropout: float) -> None:
        super().__init__()
        padding = (kernel_size - 1) * dilation
        self.net = nn.Sequential(
            nn.Conv1d(in_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding), nn.GELU(), nn.Dropout(dropout),
            nn.Conv1d(out_channels, out_channels, kernel_size, padding=padding, dilation=dilation),
            Chomp1d(padding), nn.GELU(), nn.Dropout(dropout),
        )
        self.downsample = nn.Conv1d(in_channels, out_channels, 1) if in_channels != out_channels else nn.Identity()
        self.norm = nn.LayerNorm(out_channels)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.net(x) + self.downsample(x)
        return self.norm(y.transpose(1, 2)).transpose(1, 2)


def current_hint_value_plane_count(current_hint_planes: int) -> int:
    return min(max(int(current_hint_planes), 0), HINT6_VALUE_GAP_MAX_RANK)


class BoardCNNEncoder(nn.Module):
    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        hidden = cfg.board_channels
        self.current_hint_planes = cfg.current_hint_planes
        self.current_hint_value_planes = current_hint_value_plane_count(cfg.current_hint_planes)
        self.prev_own_hint_value_planes = PREV_OWN_HINT_VALUE_PLANES
        input_channels = len(BOARD_BASE_CNN_CHANNELS) + self.current_hint_planes + self.current_hint_value_planes + self.prev_own_hint_value_planes
        self.cnn = nn.Sequential(
            nn.Conv2d(input_channels, hidden, 3, padding=1),
            nn.GroupNorm(8, hidden), nn.GELU(), nn.Dropout2d(cfg.board_dropout),
            nn.Conv2d(hidden, hidden, 3, padding=1),
            nn.GroupNorm(8, hidden), nn.GELU(), nn.Dropout2d(cfg.board_dropout),
            nn.Conv2d(hidden, cfg.board_embedding_dim, 3, padding=1),
            nn.GELU(), nn.AdaptiveAvgPool2d(1),
        )
        self.norm = nn.LayerNorm(cfg.board_embedding_dim)

    def forward(self, board_tokens: torch.Tensor, board_move_tokens: torch.Tensor,
                current_hint_tokens: torch.Tensor, current_hint_values: torch.Tensor,
                prev_own_hint_values: torch.Tensor) -> torch.Tensor:
        batch, seq_len, contexts, squares = board_tokens.shape
        if (contexts, squares) != (3, 64):
            raise ValueError(f"expected board shape BxTx3x64, got {tuple(board_tokens.shape)}")
        expected = {
            "board_move_tokens": ((batch, seq_len, 3), board_move_tokens),
            "current_hint_tokens": ((batch, seq_len, self.current_hint_planes), current_hint_tokens),
            "current_hint_values": ((batch, seq_len, self.current_hint_value_planes), current_hint_values),
            "prev_own_hint_values": ((batch, seq_len, 2), prev_own_hint_values),
        }
        for name, (shape, value) in expected.items():
            if tuple(value.shape) != shape:
                raise ValueError(f"expected {name} shape {shape}, got {tuple(value.shape)}")
        tokens = board_tokens.reshape(batch * seq_len, contexts, 64).long()
        moves = board_move_tokens.reshape(batch * seq_len, contexts).long()
        hints = current_hint_tokens.reshape(batch * seq_len, self.current_hint_planes).long()
        hint_values = current_hint_values.reshape(batch * seq_len, self.current_hint_value_planes).float()
        prev_values = prev_own_hint_values.reshape(batch * seq_len, 2).float()
        planes: list[torch.Tensor] = []
        for context_idx in range(contexts):
            context = tokens[:, context_idx]
            planes.extend((context == token_id).float() for token_id in (1, 2, 3))
        for context_idx in (1, 2):
            plane = torch.zeros(tokens.shape[0], 64, device=tokens.device)
            idx = moves[:, context_idx] - 1
            valid = idx >= 0
            if bool(valid.any()):
                plane[valid, idx[valid]] = 1.0
            planes.append(plane)
        for hint_idx in range(self.current_hint_planes):
            plane = torch.zeros(tokens.shape[0], 64, device=tokens.device)
            idx = hints[:, hint_idx] - 1
            valid = idx >= 0
            if bool(valid.any()):
                plane[valid, idx[valid]] = 1.0
            planes.append(plane)
        for value_idx in range(self.current_hint_value_planes):
            plane = torch.zeros(tokens.shape[0], 64, device=tokens.device)
            idx = hints[:, value_idx] - 1
            valid = idx >= 0
            if bool(valid.any()):
                plane[valid, idx[valid]] = hint_values[valid, value_idx]
            planes.append(plane)
        planes.extend(prev_values[:, idx].unsqueeze(1).expand(-1, 64) for idx in range(2))
        board_planes = torch.stack(planes, dim=1).reshape(batch * seq_len, -1, 8, 8)
        embedding = self.cnn(board_planes).flatten(1)
        return self.norm(embedding).reshape(batch, seq_len, -1)


class BoardConditionedBackbone(nn.Module):
    """Official backbone plus the original thinking-time head."""

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.input_norm = nn.LayerNorm(cfg.input_dim)
        self.input_proj = nn.Linear(cfg.input_dim, cfg.channels)
        self.board_encoder = BoardCNNEncoder(cfg)
        self.board_proj = nn.Sequential(nn.Linear(cfg.board_embedding_dim, cfg.channels), nn.GELU(), nn.Dropout(cfg.dropout))
        self.film = nn.Sequential(nn.Linear(cfg.board_embedding_dim, cfg.channels * 2))
        self.tcn = nn.Sequential(*[
            TemporalBlock(cfg.channels, cfg.channels, cfg.kernel_size, 2**level, cfg.dropout)
            for level in range(cfg.levels)
        ])
        self.head = nn.Sequential(
            nn.Conv1d(cfg.channels, cfg.channels // 2, 1), nn.GELU(), nn.Dropout(cfg.dropout),
            nn.Conv1d(cfg.channels // 2, 1, 1),
        )

    def encode(self, x: torch.Tensor, board_tokens: torch.Tensor, board_move_tokens: torch.Tensor,
               current_hint_tokens: torch.Tensor, current_hint_values: torch.Tensor,
               prev_own_hint_values: torch.Tensor) -> torch.Tensor:
        numeric = self.input_proj(self.input_norm(x))
        board = self.board_encoder(board_tokens, board_move_tokens, current_hint_tokens, current_hint_values, prev_own_hint_values)
        gamma, beta = self.film(board).chunk(2, dim=-1)
        fused = numeric * (1.0 + 0.1 * torch.tanh(gamma)) + 0.1 * beta + self.board_proj(board)
        return self.tcn(fused.transpose(1, 2)).transpose(1, 2)

    def forward(self, x: torch.Tensor, board_tokens: torch.Tensor, board_move_tokens: torch.Tensor,
                current_hint_tokens: torch.Tensor, current_hint_values: torch.Tensor,
                prev_own_hint_values: torch.Tensor) -> torch.Tensor:
        latent = self.encode(x, board_tokens, board_move_tokens, current_hint_tokens, current_hint_values, prev_own_hint_values)
        return self.head(latent.transpose(1, 2)).squeeze(1)
