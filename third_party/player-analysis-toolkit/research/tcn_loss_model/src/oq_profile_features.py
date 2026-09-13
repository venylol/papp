"""Fixed OQ Player profile features, snapshot selection, and train-only scaling."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .oq_player_profile import PROFILE_SCHEMA, normalize_account


OQ_PROFILE_SCHEMA = "tcn-oq-player-context-v1"
STRICT_PRE_GAME_POLICY = "latest-profile-snapshot-not-after-game-created-v1"
RETROSPECTIVE_TRUSTED_POLICY = "retrospective-current-profile-trusted-temporal-leakage-v1"
OQ_PROFILE_FEATURE_NAMES = (
    "oq_player_rating",
    "oq_player_win_rate",
    "oq_player_draw_rate",
    "oq_player_games_log",
    "oq_player_rating_maturity_40",
    "oq_player_color_rating",
    "oq_player_color_win_rate",
    "oq_player_color_draw_rate",
    "oq_player_color_games_log",
    "oq_opponent_rating",
    "oq_opponent_win_rate",
    "oq_opponent_draw_rate",
    "oq_opponent_games_log",
    "oq_opponent_rating_maturity_40",
    "oq_opponent_color_rating",
    "oq_opponent_color_win_rate",
    "oq_opponent_color_draw_rate",
    "oq_opponent_color_games_log",
    "oq_rating_difference",
    "oq_win_rate_difference",
    "oq_games_log_difference",
    "oq_color_rating_difference",
    "oq_color_win_rate_difference",
    "oq_player_vs_strong_win_rate",
    "oq_player_vs_strong_draw_rate",
    "oq_player_vs_strong_games_log",
    "oq_player_vs_strong_rating",
    "oq_player_vs_weak_win_rate",
    "oq_player_vs_weak_draw_rate",
    "oq_player_vs_weak_games_log",
    "oq_player_vs_weak_rating",
)

if len(OQ_PROFILE_FEATURE_NAMES) != 31 or len(set(OQ_PROFILE_FEATURE_NAMES)) != 31:
    raise AssertionError("OQ profile feature contract must contain 31 unique names")

# The requested cumulative ablations contain a deliberate duplicate: once the
# strong/weak block is added, the selection is already all 31 fields.  Keep both
# names auditable instead of inventing an extra feature.
OQ_PROFILE_ABLATIONS = {
    "overall-both-10-plus-five-differences": tuple([*range(0, 5), *range(9, 14), *range(18, 23)]),
    "with-color": tuple([*range(0, 23)]),
    "with-strong-weak": tuple(range(31)),
    "full-31": tuple(range(31)),
}


def profile_ablation_indices(name: str) -> tuple[int, ...]:
    try:
        return OQ_PROFILE_ABLATIONS[name]
    except KeyError as exc:
        raise ValueError(f"unknown OQ profile ablation {name!r}; choose {sorted(OQ_PROFILE_ABLATIONS)}") from exc


def profile_ablation_hash(name: str) -> str:
    indices = profile_ablation_indices(name)
    return canonical_json_hash({
        "name": name,
        "indices": list(indices),
        "feature_names": [OQ_PROFILE_FEATURE_NAMES[index] for index in indices],
    })


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_hash(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class ProfileSnapshot:
    account: str
    fetched_at: datetime
    document: dict[str, Any]
    path: Path
    file_sha256: str


def load_profile_snapshots(paths: Iterable[Path]) -> dict[str, list[ProfileSnapshot]]:
    by_account: dict[str, list[ProfileSnapshot]] = {}
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema") != PROFILE_SCHEMA:
            continue
        account = normalize_account(document.get("normalized_account") or document.get("id"))
        if not account:
            raise ValueError(f"profile snapshot has no normalized account: {path}")
        if normalize_account(document.get("id")) != account:
            raise ValueError(f"profile snapshot id/account mismatch: {path}")
        snapshot = ProfileSnapshot(
            account=account,
            fetched_at=parse_utc(document["profile_fetched_at_utc"]),
            document=document,
            path=path.resolve(),
            file_sha256=sha256_file(path),
        )
        by_account.setdefault(account, []).append(snapshot)
    for snapshots in by_account.values():
        snapshots.sort(key=lambda item: (item.fetched_at, str(item.path)))
    return by_account


def select_snapshot(
    snapshots: list[ProfileSnapshot],
    game_created: datetime,
    policy: str,
) -> ProfileSnapshot | None:
    if policy == STRICT_PRE_GAME_POLICY:
        eligible = [item for item in snapshots if item.fetched_at <= game_created]
        return eligible[-1] if eligible else None
    if policy == RETROSPECTIVE_TRUSTED_POLICY:
        return snapshots[-1] if snapshots else None
    raise ValueError(f"unsupported OQ profile temporal policy: {policy!r}")


def _overall(profile: dict[str, Any] | None) -> tuple[list[float], list[bool]]:
    if profile is None:
        return [0.0] * 5, [True] * 5
    n = int(profile["win"]) + int(profile["loss"]) + int(profile["draw"])
    if n <= 0:
        return [0.0] * 5, [True] * 5
    values = [
        float(profile["rating"]),
        float(profile["win"]) / n,
        float(profile["draw"]) / n,
        math.log1p(n),
        min(n / 40.0, 1.0),
    ]
    return values, [False] * 5


def _category(profile: dict[str, Any] | None, name: str) -> tuple[list[float], list[bool]]:
    record = profile.get(name) if profile is not None else None
    if not isinstance(record, dict):
        return [0.0] * 4, [True] * 4
    n = int(record["win"]) + int(record["loss"]) + int(record["draw"])
    if n <= 0:
        return [0.0] * 4, [True] * 4
    values = [
        float(record["rating"]),
        float(record["win"]) / n,
        float(record["draw"]) / n,
        math.log1p(n),
    ]
    return values, [False] * 4


def _opponent_category(profile: dict[str, Any] | None, name: str) -> tuple[list[float], list[bool]]:
    rating_first, missing = _category(profile, name)
    # Contract order for strong/weak is win rate, draw rate, games log, rating.
    return [rating_first[1], rating_first[2], rating_first[3], rating_first[0]], missing


def build_profile_feature_vector(
    player_profile: dict[str, Any] | None,
    opponent_profile: dict[str, Any] | None,
    *,
    player_color: str,
    opponent_color: str,
) -> tuple[np.ndarray, np.ndarray]:
    color_name = {"black": "sente", "white": "gote"}
    if player_color not in color_name or opponent_color not in color_name or player_color == opponent_color:
        raise ValueError(f"invalid fixed colors: player={player_color!r}, opponent={opponent_color!r}")
    player, player_missing = _overall(player_profile)
    opponent, opponent_missing = _overall(opponent_profile)
    player_color_values, player_color_missing = _category(player_profile, color_name[player_color])
    opponent_color_values, opponent_color_missing = _category(opponent_profile, color_name[opponent_color])
    differences = [
        player[0] - opponent[0],
        player[1] - opponent[1],
        player[3] - opponent[3],
        player_color_values[0] - opponent_color_values[0],
        player_color_values[1] - opponent_color_values[1],
    ]
    difference_missing = [
        player_missing[0] or opponent_missing[0],
        player_missing[1] or opponent_missing[1],
        player_missing[3] or opponent_missing[3],
        player_color_missing[0] or opponent_color_missing[0],
        player_color_missing[1] or opponent_color_missing[1],
    ]
    strong, strong_missing = _opponent_category(player_profile, "strong")
    weak, weak_missing = _opponent_category(player_profile, "weak")
    values = np.asarray(
        player + player_color_values + opponent + opponent_color_values + differences + strong + weak,
        dtype=np.float32,
    )
    missing = np.asarray(
        player_missing + player_color_missing + opponent_missing + opponent_color_missing
        + difference_missing + strong_missing + weak_missing,
        dtype=bool,
    )
    if values.shape != (31,) or missing.shape != (31,):
        raise AssertionError("OQ profile feature assembly shape failure")
    values[missing] = 0.0
    if not np.isfinite(values).all():
        raise ValueError("OQ profile features contain NaN or Inf")
    return values, missing


def fit_train_only_normalization(
    raw_features: np.ndarray,
    missing: np.ndarray,
    node_valid: np.ndarray,
    game_splits: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    if raw_features.shape != missing.shape or raw_features.shape[-1] != 31:
        raise ValueError("raw profile features and missing mask must have identical ...x31 shape")
    if node_valid.shape != raw_features.shape[:2]:
        raise ValueError("node_valid shape mismatch")
    if game_splits.shape != (raw_features.shape[0],):
        raise ValueError("game split shape mismatch")
    train_nodes = node_valid & (game_splits.astype(str)[:, None] == "train")
    means = np.zeros(31, dtype=np.float32)
    stds = np.ones(31, dtype=np.float32)
    for index in range(31):
        valid = train_nodes & ~missing[..., index]
        values = raw_features[..., index][valid].astype(np.float64)
        if values.size:
            means[index] = values.mean()
            std = values.std(ddof=0)
            stds[index] = std if std > 0 and np.isfinite(std) else 1.0
    normalized = (raw_features - means.reshape(1, 1, -1)) / stds.reshape(1, 1, -1)
    normalized = np.where(missing | ~node_valid[..., None], 0.0, normalized).astype(np.float32)
    if not np.isfinite(normalized).all():
        raise ValueError("normalized OQ profile features contain NaN or Inf")
    preprocessing = {
        "schema": "oq-profile-train-only-standardization-v1",
        "feature_names": list(OQ_PROFILE_FEATURE_NAMES),
        "fit_split": "train",
        "missing_fill_after_standardization": 0.0,
        "std_ddof": 0,
        "means": means.astype(float).tolist(),
        "stds": stds.astype(float).tolist(),
    }
    return normalized, means, stds, canonical_json_hash(preprocessing)
