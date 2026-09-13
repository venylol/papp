"""Thinking-time, four-class severity, and three-class WLD multitask model."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as F

from .backbone import BoardConditionedBackbone, ModelConfig
from .oq_profile_features import OQ_PROFILE_FEATURE_NAMES, profile_ablation_indices

SEVERITY_CLASS_NAMES = ("class_zero", "class_1_3", "class_4_9", "class_ge10")
WLD_CLASS_NAMES = ("class_no_wld_loss", "class_half_wld_loss", "class_full_wld_loss")


@dataclass
class ModelOutput:
    pred_time_log_seconds: torch.Tensor
    severity_hidden: torch.Tensor
    severity_logits: torch.Tensor
    severity_class_probabilities: torch.Tensor
    probability_loss_zero: torch.Tensor
    probability_loss_positive: torch.Tensor
    probability_loss_ge4: torch.Tensor
    probability_loss_ge10: torch.Tensor
    wld_logits: torch.Tensor
    wld_probabilities: torch.Tensor
    probability_wld_any: torch.Tensor
    expected_wld_loss: torch.Tensor


class TimeConditionedLossModel(nn.Module):
    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.backbone = BoardConditionedBackbone(cfg)
        hidden = cfg.channels // 2
        self.severity_context = nn.Sequential(
            nn.Linear(cfg.channels + 2, hidden), nn.GELU(), nn.Dropout(cfg.dropout)
        )
        self.severity_head = nn.Linear(hidden, len(SEVERITY_CLASS_NAMES))
        self.wld_head = nn.Linear(hidden, len(WLD_CLASS_NAMES))

    def output_from_hidden(self, time_pred: torch.Tensor, hidden: torch.Tensor) -> ModelOutput:
        severity_logits = self.severity_head(hidden)
        severity_probabilities = torch.softmax(severity_logits, dim=-1)
        wld_logits = self.wld_head(hidden)
        wld_probabilities = torch.softmax(wld_logits, dim=-1)
        probability_zero = severity_probabilities[..., 0]
        probability_positive = 1.0 - probability_zero
        probability_ge4 = severity_probabilities[..., 2] + severity_probabilities[..., 3]
        probability_ge10 = severity_probabilities[..., 3]
        probability_wld_any = wld_probabilities[..., 1] + wld_probabilities[..., 2]
        expected_wld_loss = 0.5 * wld_probabilities[..., 1] + wld_probabilities[..., 2]
        return ModelOutput(
            time_pred, hidden, severity_logits, severity_probabilities,
            probability_zero, probability_positive, probability_ge4, probability_ge10,
            wld_logits, wld_probabilities, probability_wld_any, expected_wld_loss,
        )

    @staticmethod
    def actual_time_features(actual_thinking_time_ms: torch.Tensor) -> torch.Tensor:
        missing = ~torch.isfinite(actual_thinking_time_ms)
        seconds = torch.where(
            missing,
            torch.zeros_like(actual_thinking_time_ms),
            actual_thinking_time_ms.clamp_min(0) / 1000.0,
        )
        return torch.stack((torch.log1p(seconds), missing.to(seconds.dtype)), dim=-1)

    def forward(self, x: torch.Tensor, board_tokens: torch.Tensor, board_move_tokens: torch.Tensor,
                current_hint_tokens: torch.Tensor, current_hint_values: torch.Tensor,
                prev_own_hint_values: torch.Tensor, actual_thinking_time_ms: torch.Tensor) -> ModelOutput:
        latent = self.backbone.encode(
            x, board_tokens, board_move_tokens, current_hint_tokens,
            current_hint_values, prev_own_hint_values,
        )
        time_pred = self.backbone.head(latent.transpose(1, 2)).squeeze(1)
        severity_hidden = self.severity_context(
            torch.cat((latent, self.actual_time_features(actual_thinking_time_ms)), dim=-1)
        )
        return self.output_from_hidden(time_pred, severity_hidden)


class ProfileConditionedLossModel(TimeConditionedLossModel):
    """Independent Player-context schema; its zero FiLM projection is an exact identity."""

    def __init__(self, cfg: ModelConfig, profile_ablation: str = "full-31") -> None:
        super().__init__(cfg)
        indices = profile_ablation_indices(profile_ablation)
        if not indices:
            raise ValueError("profile-conditioned model requires at least one selected profile feature")
        self.profile_ablation = profile_ablation
        self.register_buffer(
            "oq_profile_feature_indices",
            torch.as_tensor(indices, dtype=torch.long),
            persistent=True,
        )
        embedding_dim = 32
        self.profile_encoder = nn.Sequential(
            nn.Linear(len(indices) * 2, embedding_dim),
            nn.GELU(),
            nn.Linear(embedding_dim, embedding_dim),
            nn.GELU(),
        )
        hidden = cfg.channels // 2
        self.profile_severity_film = nn.Linear(embedding_dim, hidden * 2)
        nn.init.zeros_(self.profile_severity_film.weight)
        nn.init.zeros_(self.profile_severity_film.bias)

    def forward(self, x: torch.Tensor, board_tokens: torch.Tensor, board_move_tokens: torch.Tensor,
                current_hint_tokens: torch.Tensor, current_hint_values: torch.Tensor,
                prev_own_hint_values: torch.Tensor, actual_thinking_time_ms: torch.Tensor,
                oq_profile_features: torch.Tensor, oq_profile_missing: torch.Tensor) -> ModelOutput:
        expected = (*x.shape[:2], len(OQ_PROFILE_FEATURE_NAMES))
        if tuple(oq_profile_features.shape) != expected or tuple(oq_profile_missing.shape) != expected:
            raise ValueError(
                f"expected OQ profile feature/missing shapes {expected}, got "
                f"{tuple(oq_profile_features.shape)} and {tuple(oq_profile_missing.shape)}"
            )
        latent = self.backbone.encode(
            x, board_tokens, board_move_tokens, current_hint_tokens,
            current_hint_values, prev_own_hint_values,
        )
        time_pred = self.backbone.head(latent.transpose(1, 2)).squeeze(1)
        severity_hidden = self.severity_context(
            torch.cat((latent, self.actual_time_features(actual_thinking_time_ms)), dim=-1)
        )
        indices = self.oq_profile_feature_indices
        selected_features = oq_profile_features.float().index_select(-1, indices)
        selected_missing = oq_profile_missing.to(selected_features.dtype).index_select(-1, indices)
        profile_embedding = self.profile_encoder(torch.cat((selected_features, selected_missing), dim=-1))
        gamma, beta = self.profile_severity_film(profile_embedding).chunk(2, dim=-1)
        conditioned_hidden = severity_hidden * (1.0 + gamma) + beta
        return self.output_from_hidden(time_pred, conditioned_hidden)


class OffbookProfileConditionedLossModel(ProfileConditionedLossModel):
    """Profile model with a required Level18 per-node off-book scalar.

    The new projection is applied only after the time head has been computed.
    Its zero initialization makes migration from the formal profile checkpoint
    exactly output-equivalent until the new branch is trained.
    """

    def __init__(self, cfg: ModelConfig, profile_ablation: str = "full-31") -> None:
        super().__init__(cfg, profile_ablation)
        hidden = cfg.channels // 2
        self.offbook_severity_film = nn.Linear(1, hidden * 2)
        nn.init.zeros_(self.offbook_severity_film.weight)
        nn.init.zeros_(self.offbook_severity_film.bias)

    def forward(
        self, x: torch.Tensor, board_tokens: torch.Tensor, board_move_tokens: torch.Tensor,
        current_hint_tokens: torch.Tensor, current_hint_values: torch.Tensor,
        prev_own_hint_values: torch.Tensor, actual_thinking_time_ms: torch.Tensor,
        offbook_feature: torch.Tensor, oq_profile_features: torch.Tensor,
        oq_profile_missing: torch.Tensor,
    ) -> ModelOutput:
        expected_profile = (*x.shape[:2], len(OQ_PROFILE_FEATURE_NAMES))
        if (
            tuple(oq_profile_features.shape) != expected_profile
            or tuple(oq_profile_missing.shape) != expected_profile
        ):
            raise ValueError(
                f"expected OQ profile feature/missing shapes {expected_profile}, got "
                f"{tuple(oq_profile_features.shape)} and {tuple(oq_profile_missing.shape)}"
            )
        expected_offbook = tuple(x.shape[:2])
        if tuple(offbook_feature.shape) != expected_offbook:
            raise ValueError(
                f"expected required Level18 offbook feature shape {expected_offbook}, "
                f"got {tuple(offbook_feature.shape)}"
            )
        offbook_feature = offbook_feature.float()
        if not bool(torch.isfinite(offbook_feature).all()):
            raise ValueError("Level18 offbook feature contains NaN or Inf")
        if bool((offbook_feature < 0).any()) or bool((offbook_feature > 1).any()):
            raise ValueError("Level18 offbook feature must be in the closed interval [0, 1]")

        latent = self.backbone.encode(
            x, board_tokens, board_move_tokens, current_hint_tokens,
            current_hint_values, prev_own_hint_values,
        )
        time_pred = self.backbone.head(latent.transpose(1, 2)).squeeze(1)
        severity_hidden = self.severity_context(
            torch.cat((latent, self.actual_time_features(actual_thinking_time_ms)), dim=-1)
        )
        indices = self.oq_profile_feature_indices
        selected_features = oq_profile_features.float().index_select(-1, indices)
        selected_missing = oq_profile_missing.to(selected_features.dtype).index_select(-1, indices)
        profile_embedding = self.profile_encoder(torch.cat((selected_features, selected_missing), dim=-1))
        gamma, beta = self.profile_severity_film(profile_embedding).chunk(2, dim=-1)
        conditioned_hidden = severity_hidden * (1.0 + gamma) + beta
        offbook_gamma, offbook_beta = self.offbook_severity_film(
            offbook_feature.unsqueeze(-1)
        ).chunk(2, dim=-1)
        conditioned_hidden = conditioned_hidden * (1.0 + offbook_gamma) + offbook_beta
        return self.output_from_hidden(time_pred, conditioned_hidden)


def multitask_loss(output: ModelOutput, actual_time_ms: torch.Tensor,
                   severity_class: torch.Tensor, mask: torch.Tensor,
                   time_weight: float = 0.25,
                   severity_weight: float = 1.0,
                   class_weights: tuple[float, float, float, float] | list[float] = (1, 1, 1, 1),
                   wld_class: torch.Tensor | None = None,
                   wld_label_available: torch.Tensor | None = None,
                   global_placement_ply: torch.Tensor | None = None,
                   wld_weight: float = 1.0) -> dict[str, torch.Tensor]:
    valid = mask.bool() & torch.isfinite(severity_class) & torch.isfinite(actual_time_ms)
    if not bool(valid.any()):
        raise ValueError("batch has no valid supervised nodes")
    target_class = severity_class[valid].long()
    if bool(((target_class < 0) | (target_class > 3)).any()):
        raise ValueError("severity class must be in 0..3")
    weights = torch.as_tensor(class_weights, dtype=output.severity_logits.dtype, device=output.severity_logits.device)
    if weights.shape != (4,) or not bool(torch.isfinite(weights).all()) or bool((weights <= 0).any()):
        raise ValueError("severity class weights must contain four finite positive values")
    time_target = torch.log1p(actual_time_ms[valid].clamp_min(0) / 1000.0)
    time_loss = F.mse_loss(output.pred_time_log_seconds[valid], time_target)
    severity_loss = F.cross_entropy(output.severity_logits[valid], target_class, weight=weights)
    wld_loss = output.wld_logits.sum() * 0.0
    if wld_class is not None or wld_label_available is not None or global_placement_ply is not None:
        if wld_class is None or wld_label_available is None or global_placement_ply is None:
            raise ValueError("WLD supervision requires class, availability, and global placement ply together")
        wld_valid = (
            mask.bool() & wld_label_available.bool() & torch.isfinite(wld_class)
            & (global_placement_ply >= 39)
        )
        if bool(wld_valid.any()):
            wld_target = wld_class[wld_valid].long()
            if bool(((wld_target < 0) | (wld_target > 2)).any()):
                raise ValueError("WLD class must be in 0..2")
            wld_loss = F.cross_entropy(output.wld_logits[wld_valid], wld_target)
    total = time_weight * time_loss + severity_weight * severity_loss + wld_weight * wld_loss
    return {
        "total": total, "thinking_time": time_loss,
        "severity_classification": severity_loss, "wld_classification": wld_loss,
    }
