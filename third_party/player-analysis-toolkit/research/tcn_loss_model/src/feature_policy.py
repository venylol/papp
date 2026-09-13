"""One auditable input policy for every split and downstream cohort."""

from __future__ import annotations

import re
from collections.abc import Iterable


INPUT_POLICY = "uniform-no-current-player-loss-history-v1"

# Labels and audit values remain available outside the model input tensor.  Any
# feature name containing one of these semantic tokens is rejected, including a
# corresponding ``__missing`` indicator.  This intentionally fails rather than
# retaining a forbidden feature with a zero or missing value.
FORBIDDEN_LOSS_HISTORY_TOKENS = {
    "loss",
    "losses",
    "blunder",
    "blunders",
    "mistake",
    "mistakes",
    "residual",
    "residuals",
    "severity",
    "zero",
    "ge4",
    "ge10",
}


def _tokens(name: str) -> set[str]:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name))
    return {token for token in re.split(r"[^a-z0-9]+", snake.lower()) if token}


def forbidden_loss_history_features(input_features: Iterable[str]) -> list[str]:
    """Return explicitly loss-derived model inputs, never target/audit arrays."""
    return sorted({
        str(name)
        for name in input_features
        if _tokens(str(name)) & FORBIDDEN_LOSS_HISTORY_TOKENS
    })


def assert_uniform_loss_history_policy(input_features: Iterable[str]) -> None:
    """Require omission, not masking, of all loss-history feature columns."""
    forbidden = forbidden_loss_history_features(input_features)
    if forbidden:
        raise ValueError(
            "loss-history fields must be omitted from model inputs under "
            f"{INPUT_POLICY}; do not retain them as zero/missing features: {forbidden}"
        )
