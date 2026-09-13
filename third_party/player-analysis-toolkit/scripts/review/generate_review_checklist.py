#!/usr/bin/env python3
"""Generate a reviewer-facing player information checklist from existing JSON data."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOLKIT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from player_analysis_toolkit.analysis_core import (
    ENGINE_WLD_TOTAL_FIELD,
    fit_mean_time_curve,
    load_engine_games,
    target_engine_games,
    time_game_metrics,
)


EXPECTED_SCHEMAS = {
    "accountBundle": "oq-public-account-bundle-v1",
    "engineSummary": "ega-account-bundle-summary-v1",
    "sameColorModel": "player-offbook-segment-model-v1",
    "allControlModel": "player-offbook-segment-model-v1",
    "comparisonStats": "player-offbook-segment-stats-v1",
}

RATING_BUCKETS = (
    ("<1200", -math.inf, 1200.0),
    ("[1200,1500)", 1200.0, 1500.0),
    ("[1500,1700)", 1500.0, 1700.0),
    ("[1700,2000)", 1700.0, 2000.0),
    ("[2000,2200)", 2000.0, 2200.0),
    (">=2200", 2200.0, math.inf),
)

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")


class ChecklistError(ValueError):
    """Raised for clear, user-actionable input errors."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a reviewer-facing player checklist from existing analysis JSON."
    )
    parser.add_argument("--config", required=True, help="UTF-8 JSON configuration file")
    return parser.parse_args()


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ChecklistError(f"{label} does not exist or is not a file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ChecklistError(f"{label} is not valid UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ChecklistError(
            f"{label} is not valid JSON at line {exc.lineno}, column {exc.colno}: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise ChecklistError(f"{label} must contain a JSON object: {path}")
    return value


def required_string(obj: dict[str, Any], key: str, label: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ChecklistError(f"{label}.{key} must be a non-empty string")
    return value.strip()


def required_number(obj: dict[str, Any], key: str, label: str) -> float:
    value = obj.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ChecklistError(f"{label}.{key} must be a finite number")
    return float(value)


def required_integer(obj: dict[str, Any], key: str, label: str) -> int:
    value = obj.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ChecklistError(f"{label}.{key} must be a non-negative integer")
    return value


def model_probability_cells(
    stats: dict[str, Any], key: str, label: str
) -> tuple[str, str, str]:
    container = stats.get("modelProbability")
    if not isinstance(container, dict):
        return ("模型概率不可用", "模型概率不可用", "模型概率不可用")
    summary = container.get(key)
    if not isinstance(summary, dict) or summary.get("status") != "available":
        return ("模型概率不可用", "模型概率不可用", "模型概率不可用")
    expected = required_number(summary, "expectedNodeCount", label)
    per_node = required_number(summary, "actualRateMinusMeanProbability", label)
    per_game = required_number(summary, "actualMinusExpectedPerGame", label)
    return (fmt_number(expected, 6), fmt_number(per_node, 6), fmt_number(per_game, 6))


def required_list(obj: dict[str, Any], key: str, label: str) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        raise ChecklistError(f"{label}.{key} must be an array")
    return value


def resolve_path(config_dir: Path, value: str, label: str) -> Path:
    raw = Path(value)
    path = raw if raw.is_absolute() else config_dir / raw
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise ChecklistError(f"cannot resolve {label}: {value}") from exc


def load_config(config_path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    config = load_json(config_path, "config")
    schema = config.get("schema")
    if schema is not None and schema != "review-checklist-config-v1":
        raise ChecklistError(
            f"config.schema must be review-checklist-config-v1, got {schema!r}"
        )

    required_string(config, "playerDisplayName", "config")
    required_string(config, "account", "config")
    reported = required_list(config, "reportedGameIds", "config")
    if not reported or any(not isinstance(item, str) or not item.strip() for item in reported):
        raise ChecklistError("config.reportedGameIds must contain non-empty strings")
    normalized = [item.strip() for item in reported]
    if len(set(normalized)) != len(normalized):
        raise ChecklistError("config.reportedGameIds contains duplicate game IDs")
    config["reportedGameIds"] = normalized

    config_dir = config_path.parent.resolve()
    input_keys = (
        "accountBundle",
        "engineSummary",
        "sameColorModel",
        "allControlModel",
        "comparisonStats",
        "profileImage",
        "openingImage",
        "template",
    )
    paths = {
        key: resolve_path(config_dir, required_string(config, key, "config"), f"config.{key}")
        for key in input_keys
    }
    paths["outputMarkdown"] = resolve_path(
        config_dir,
        required_string(config, "outputMarkdown", "config"),
        "config.outputMarkdown",
    )
    if "outputData" in config:
        paths["outputData"] = resolve_path(
            config_dir,
            required_string(config, "outputData", "config"),
            "config.outputData",
        )

    for key in input_keys:
        if not paths[key].is_file():
            raise ChecklistError(f"config.{key} does not exist or is not a file: {paths[key]}")
    return config, paths


def validate_schema(data: dict[str, Any], label: str) -> None:
    actual = data.get("schema")
    expected = EXPECTED_SCHEMAS[label]
    if actual != expected:
        raise ChecklistError(f"{label}.schema must be {expected}, got {actual!r}")


def account_equal(left: Any, right: str) -> bool:
    return isinstance(left, str) and left.casefold() == right.casefold()


def unique_by_id(items: list[Any], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ChecklistError(f"{label}[{index}] must be an object")
        game_id = required_string(item, "id", f"{label}[{index}]")
        if game_id in result:
            raise ChecklistError(f"duplicate game ID {game_id!r} in {label}")
        result[game_id] = item
    return result


def player_summary(summary: dict[str, Any], account: str) -> dict[str, Any]:
    players = required_list(summary, "players", "engineSummary")
    matches = [
        item
        for item in players
        if isinstance(item, dict) and account_equal(item.get("account"), account)
    ]
    if len(matches) != 1:
        raise ChecklistError(
            f"engineSummary must contain exactly one player for account {account!r}; found {len(matches)}"
        )
    return matches[0]


def validate_account_sources(
    account: str,
    bundle: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    if not account_equal(bundle.get("account"), account):
        raise ChecklistError(
            f"accountBundle.account {bundle.get('account')!r} does not match config.account {account!r}"
        )
    if not account_equal(summary.get("account"), account):
        raise ChecklistError(
            f"engineSummary.account {summary.get('account')!r} does not match config.account {account!r}"
        )


def validate_model_reported_ids(
    model: dict[str, Any], label: str, reported_ids: list[str]
) -> None:
    values = required_list(model, "reportedGameIds", label)
    if any(not isinstance(value, str) for value in values) or set(values) != set(reported_ids):
        raise ChecklistError(
            f"{label}.reportedGameIds does not match config.reportedGameIds"
        )
    for segment in ("fullGame", "postOffBookInclusive"):
        segment_data = model.get(segment)
        if not isinstance(segment_data, dict) or not isinstance(segment_data.get("comparison"), dict):
            raise ChecklistError(f"{label}.{segment}.comparison must be an object")
        comparison = segment_data["comparison"]
        for arm in ("reported", "control"):
            stats = comparison.get(arm)
            if not isinstance(stats, dict):
                raise ChecklistError(f"{label}.{segment}.comparison.{arm} must be an object")
            game_ids = set(extract_stat_game_ids(stats, f"{label}.{segment}.comparison.{arm}"))
            if arm == "reported" and game_ids != set(reported_ids):
                raise ChecklistError(
                    f"{label}.{segment}.comparison.reported game IDs do not match config.reportedGameIds"
                )
            overlap = game_ids.intersection(reported_ids) if arm == "control" else set()
            if overlap:
                raise ChecklistError(
                    f"{label}.{segment}.comparison.control contains reported game(s): {sorted(overlap)}"
                )


def extract_stat_game_ids(stats: dict[str, Any], label: str) -> list[str]:
    games = required_list(stats, "games", label)
    ids: list[str] = []
    for index, game in enumerate(games):
        if not isinstance(game, dict):
            raise ChecklistError(f"{label}.games[{index}] must be an object")
        ids.append(required_string(game, "gameId", f"{label}.games[{index}]"))
    if len(set(ids)) != len(ids):
        raise ChecklistError(f"{label}.games contains duplicate game IDs")
    return ids


def summarize_loss_stats(stats: dict[str, Any], label: str) -> dict[str, Any]:
    games = required_list(stats, "games", label)
    game_count = required_integer(stats, "gameCount", label)
    move_count = required_integer(stats, "moveCount", label)
    if game_count != len(games):
        raise ChecklistError(
            f"{label}.gameCount is {game_count}, but games contains {len(games)} rows"
        )
    total_loss = 0.0
    summed_moves = 0
    for index, game in enumerate(games):
        if not isinstance(game, dict):
            raise ChecklistError(f"{label}.games[{index}] must be an object")
        total_loss += required_number(game, "totalLoss", f"{label}.games[{index}]")
        summed_moves += required_integer(game, "moveCount", f"{label}.games[{index}]")
    if summed_moves != move_count:
        raise ChecklistError(
            f"{label}.moveCount is {move_count}, but game rows sum to {summed_moves}"
        )
    ge4_expected, ge4_residual_node, ge4_residual_game = model_probability_cells(
        stats, "lossGe4", f"{label}.modelProbability.lossGe4"
    )
    ge10_expected, ge10_residual_node, ge10_residual_game = model_probability_cells(
        stats, "lossGe10", f"{label}.modelProbability.lossGe10"
    )
    return {
        "gameCount": game_count,
        "moveCount": move_count,
        "totalLoss": total_loss,
        "gameWeightedMeanLoss": required_number(stats, "gameWeightedMeanLoss", label),
        "moveWeightedMeanLoss": required_number(stats, "moveWeightedMeanLoss", label),
        "zeroLossRate": required_number(stats, "zeroLossRate", label),
        "lossAtLeast4Count": required_integer(stats, "lossAtLeast4Count", label),
        "lossAtLeast10Count": required_integer(stats, "lossAtLeast10Count", label),
        "lossAtLeast4Rate": required_number(stats, "lossAtLeast4Rate", label),
        "lossAtLeast10Rate": required_number(stats, "lossAtLeast10Rate", label),
        "expectedLossAtLeast4Count": ge4_expected,
        "expectedLossAtLeast10Count": ge10_expected,
        "lossAtLeast4ActualMinusPredictedPerNode": ge4_residual_node,
        "lossAtLeast10ActualMinusPredictedPerNode": ge10_residual_node,
        "lossAtLeast4ActualMinusPredictedPerGame": ge4_residual_game,
        "lossAtLeast10ActualMinusPredictedPerGame": ge10_residual_game,
    }


def select_reference(
    comparison_stats: dict[str, Any], configured_name: Any
) -> tuple[str, dict[str, Any]]:
    references = comparison_stats.get("references")
    if not isinstance(references, dict) or not references:
        raise ChecklistError("comparisonStats.references must be a non-empty object")
    if configured_name is None:
        if len(references) != 1:
            raise ChecklistError(
                "comparisonStats has multiple references; set config.comparisonReference"
            )
        name = next(iter(references))
    else:
        if not isinstance(configured_name, str) or not configured_name.strip():
            raise ChecklistError("config.comparisonReference must be a non-empty string")
        name = configured_name.strip()
    reference = references.get(name)
    if not isinstance(reference, dict):
        raise ChecklistError(f"comparisonStats reference {name!r} was not found")
    return name, reference


def reported_game_rows(
    reported_ids: list[str], same_model: dict[str, Any]
) -> list[dict[str, Any]]:
    full_stats = same_model["fullGame"]["comparison"]["reported"]
    offbook_stats = same_model["postOffBookInclusive"]["comparison"]["reported"]
    full_games = {game["gameId"]: game for game in full_stats["games"]}
    offbook_games = {game["gameId"]: game for game in offbook_stats["games"]}
    rows: list[dict[str, Any]] = []
    for game_id in reported_ids:
        if game_id not in full_games or game_id not in offbook_games:
            raise ChecklistError(f"reported game {game_id!r} is missing from model segment data")
        full = full_games[game_id]
        offbook = offbook_games[game_id]
        rows.append(
            {
                "gameId": game_id,
                "fullMoveCount": required_integer(full, "moveCount", f"reported {game_id} fullGame"),
                "fullTotalLoss": required_number(full, "totalLoss", f"reported {game_id} fullGame"),
                "fullAverageLoss": required_number(full, "meanLoss", f"reported {game_id} fullGame"),
                "fullLossAtLeast4Count": required_integer(full, "lossAtLeast4Count", f"reported {game_id} fullGame"),
                "fullLossAtLeast10Count": required_integer(full, "lossAtLeast10Count", f"reported {game_id} fullGame"),
                "fullLossAtLeast4Rate": required_number(full, "lossAtLeast4Rate", f"reported {game_id} fullGame"),
                "fullLossAtLeast10Rate": required_number(full, "lossAtLeast10Rate", f"reported {game_id} fullGame"),
                "offbookMoveCount": required_integer(
                    offbook, "moveCount", f"reported {game_id} postOffBookInclusive"
                ),
                "offbookTotalLoss": required_number(
                    offbook, "totalLoss", f"reported {game_id} postOffBookInclusive"
                ),
                "offbookAverageLoss": required_number(
                    offbook, "meanLoss", f"reported {game_id} postOffBookInclusive"
                ),
                "offbookLossAtLeast4Count": required_integer(
                    offbook, "lossAtLeast4Count", f"reported {game_id} postOffBookInclusive"
                ),
                "offbookLossAtLeast10Count": required_integer(
                    offbook, "lossAtLeast10Count", f"reported {game_id} postOffBookInclusive"
                ),
                "offbookLossAtLeast4Rate": required_number(
                    offbook, "lossAtLeast4Rate", f"reported {game_id} postOffBookInclusive"
                ),
                "offbookLossAtLeast10Rate": required_number(
                    offbook, "lossAtLeast10Rate", f"reported {game_id} postOffBookInclusive"
                ),
                **(
                    {
                        "engineWldLossTotalFromPly39": required_number(
                            full,
                            ENGINE_WLD_TOTAL_FIELD,
                            f"reported {game_id} fullGame",
                        )
                    }
                    if ENGINE_WLD_TOTAL_FIELD in full
                    else {}
                ),
            }
        )
    return rows


def control_rows(
    same_model: dict[str, Any],
    all_model: dict[str, Any],
    reference_name: str,
    reference: dict[str, Any],
) -> list[dict[str, Any]]:
    sources = (
        ("举报局组合", same_model, "reported", False),
        ("同色个人非举报对照", same_model, "control", False),
        ("全部个人非举报对照", all_model, "control", False),
        (f"高分开局参照（{reference_name}）", reference, "loss", True),
    )
    rows: list[dict[str, Any]] = []
    for group_name, source, arm, is_reference in sources:
        for segment, segment_label in (
            ("fullGame", "整局"),
            ("postOffBookInclusive", "算法脱谱后（含脱谱手）"),
        ):
            stats = source[segment][arm] if is_reference else source[segment]["comparison"][arm]
            row = summarize_loss_stats(stats, f"{group_name}.{segment}")
            row.update({"group": group_name, "segment": segment_label})
            rows.append(row)
    return rows


def detail_players(detail: dict[str, Any], account: str, game_id: str) -> tuple[int, dict[str, Any]]:
    players = required_list(detail, "players", f"accountBundle detail {game_id}")
    if len(players) != 2 or any(not isinstance(player, dict) for player in players):
        raise ChecklistError(f"accountBundle detail {game_id} must contain exactly two players")
    target_indexes = [
        index for index, player in enumerate(players) if account_equal(player.get("id"), account)
    ]
    if len(target_indexes) != 1:
        raise ChecklistError(
            f"accountBundle detail {game_id} must contain account {account!r} exactly once"
        )
    target_index = target_indexes[0]
    opponent = players[1 - target_index]
    required_string(opponent, "id", f"accountBundle detail {game_id} opponent")
    required_number(opponent, "oldR", f"accountBundle detail {game_id} opponent")
    return target_index, opponent


def time_rows(
    reported_ids: list[str],
    account: str,
    engine_directory: Path,
    same_model: dict[str, Any],
    detail_by_id: dict[str, dict[str, Any]],
    loss_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        engine_games = target_engine_games(load_engine_games(engine_directory), account)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ChecklistError(
            f"could not load target-player EG game JSON from {engine_directory}: {exc}"
        ) from exc
    engine_by_id: dict[str, dict[str, Any]] = {}
    for game in engine_games:
        game_id = required_string(game, "gameId", "EG target game")
        if game_id in engine_by_id:
            raise ChecklistError(f"duplicate EG target game ID {game_id!r}")
        engine_by_id[game_id] = game

    control_stats = same_model["fullGame"]["comparison"]["control"]
    control_ids = extract_stat_game_ids(
        control_stats, "sameColorModel.fullGame.comparison.control"
    )
    missing_engine_ids = [
        game_id
        for game_id in [*reported_ids, *control_ids]
        if game_id not in engine_by_id
    ]
    if missing_engine_ids:
        raise ChecklistError(
            f"EG game JSON is missing reported/same-color control game(s): {missing_engine_ids}"
        )
    reported_engine_games = [engine_by_id[game_id] for game_id in reported_ids]
    control_engine_games = [engine_by_id[game_id] for game_id in control_ids]
    reported_colors = {game.get("color") for game in reported_engine_games}
    control_colors = {game.get("color") for game in control_engine_games}
    if len(reported_colors) != 1 or control_colors != reported_colors:
        raise ChecklistError(
            "sameColorModel controls do not have the same single color as all reported games"
        )
    try:
        predict, _, per_ply_p90 = fit_mean_time_curve(control_engine_games)
    except (ValueError, ImportError, ModuleNotFoundError) as exc:
        raise ChecklistError(f"could not fit the existing same-color time baseline: {exc}") from exc

    loss_by_id = {row["gameId"]: row for row in loss_rows}
    reported_metrics: list[dict[str, Any]] = []
    for game_id, engine_game in zip(reported_ids, reported_engine_games):
        detail = detail_by_id.get(game_id)
        if detail is None:
            raise ChecklistError(f"reported game {game_id!r} is missing from accountBundle.details")
        target_index, opponent = detail_players(detail, account, game_id)
        tcb = required_number(detail, "tcb", f"accountBundle detail {game_id}")
        metric = time_game_metrics(engine_game, predict, per_ply_p90)
        expected_moves = loss_by_id[game_id]["fullMoveCount"]
        if metric["moveCount"] != expected_moves:
            raise ChecklistError(
                f"reported game {game_id!r} has {metric['moveCount']} timed EG moves "
                f"but {expected_moves} loss-analysis moves"
            )
        timed_nodes = [
            node
            for node in engine_game.get("nodes", [])
            if isinstance(node, dict) and node.get("thinkingTimeMs") is not None
        ]
        maximum_time = max(float(node["thinkingTimeMs"]) for node in timed_nodes)
        metric.update(
            {
                "opponent": opponent["id"],
                "opponentOldR": float(opponent["oldR"]),
                "timeControlMs": tcb,
                "colorLabel": "黑" if target_index == 0 else "白",
                "maximumTimeMs": maximum_time,
            }
        )
        reported_metrics.append(metric)

    control_metrics = [
        time_game_metrics(game, predict, per_ply_p90) for game in control_engine_games
    ]

    def summarize_group(name: str, metrics: list[dict[str, Any]]) -> dict[str, Any]:
        move_count = sum(int(metric["moveCount"]) for metric in metrics)
        total_time = sum(float(metric["totalTimeMs"]) for metric in metrics)
        long_count = sum(int(metric["longThinkCount"]) for metric in metrics)
        return {
            "group": name,
            "gameCount": len(metrics),
            "moveCount": move_count,
            "totalTimeMs": total_time,
            "gameWeightedMeanTimeMs": statistics.fmean(
                float(metric["meanTimeMs"]) for metric in metrics
            ),
            "moveWeightedMeanTimeMs": total_time / move_count,
            "gameWeightedMeanResidualMs": statistics.fmean(
                float(metric["meanResidualFromBaselineMs"]) for metric in metrics
            ),
            "longThinkCount": long_count,
            "gameWeightedLongThinkRate": statistics.fmean(
                float(metric["longThinkRate"]) for metric in metrics
            ),
            "moveWeightedLongThinkRate": long_count / move_count,
        }

    comparisons = [
        summarize_group("举报局组合", reported_metrics),
        summarize_group("同色个人非举报对照", control_metrics),
    ]
    return reported_metrics, comparisons


def rating_bucket_rows(
    account: str,
    reported_ids: list[str],
    player: dict[str, Any],
    detail_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    games = required_list(player, "games", f"engineSummary player {account}")
    reported_set = set(reported_ids)
    valid_controls: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, game in enumerate(games):
        label = f"engineSummary player {account}.games[{index}]"
        if not isinstance(game, dict):
            raise ChecklistError(f"{label} must be an object")
        game_id = required_string(game, "gameId", label)
        if game_id in seen_ids:
            raise ChecklistError(f"duplicate game ID {game_id!r} in {label.rsplit('.', 1)[0]}")
        seen_ids.add(game_id)
        average_loss = game.get("averageLoss")
        node_count = game.get("nodeCount")
        if average_loss is None or node_count == 0:
            continue
        average_loss = required_number(game, "averageLoss", label)
        total_loss = required_number(game, "totalLoss", label)
        required_integer(game, "nodeCount", label)
        if game_id in reported_set:
            continue
        detail = detail_by_id.get(game_id)
        if detail is None:
            raise ChecklistError(
                f"valid EG control game {game_id!r} is missing from accountBundle.details"
            )
        _, opponent = detail_players(detail, account, game_id)
        valid_controls.append(
            {
                "gameId": game_id,
                "opponentOldR": float(opponent["oldR"]),
                "totalLoss": total_loss,
                "averageLoss": average_loss,
            }
        )

    leaked = reported_set.intersection(row["gameId"] for row in valid_controls)
    if leaked:
        raise ChecklistError(f"reported games leaked into Rating controls: {sorted(leaked)}")

    rows: list[dict[str, Any]] = []
    assigned_ids: set[str] = set()
    for label, lower, upper in RATING_BUCKETS:
        bucket_games = [
            game for game in valid_controls if lower <= game["opponentOldR"] < upper
        ]
        bucket_ids = {game["gameId"] for game in bucket_games}
        if assigned_ids.intersection(bucket_ids):
            raise ChecklistError(f"Rating bucket overlap detected for {label}")
        assigned_ids.update(bucket_ids)
        total_losses = [game["totalLoss"] for game in bucket_games]
        average_losses = [game["averageLoss"] for game in bucket_games]
        rows.append(
            {
                "rating": label,
                "gameCount": len(bucket_games),
                "totalLoss": sum(total_losses) if total_losses else None,
                "meanGameTotalLoss": statistics.fmean(total_losses) if total_losses else None,
                "medianGameTotalLoss": statistics.median(total_losses) if total_losses else None,
                "meanGameAverageLoss": statistics.fmean(average_losses) if average_losses else None,
                "medianGameAverageLoss": statistics.median(average_losses) if average_losses else None,
                "gameIds": sorted(bucket_ids),
            }
        )
    if assigned_ids != {game["gameId"] for game in valid_controls}:
        raise ChecklistError("one or more Rating control games were not assigned to a bucket")
    return rows, len(valid_controls)


def markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    escaped_headers = [markdown_cell(value) for value in headers]
    lines = [
        "| " + " | ".join(escaped_headers) + " |",
        "| " + " | ".join("---" for _ in escaped_headers) + " |",
    ]
    for row in rows:
        if len(row) != len(headers):
            raise ChecklistError("internal table row width mismatch")
        lines.append("| " + " | ".join(markdown_cell(value) for value in row) + " |")
    return "\n".join(lines)


LOSS_CONTROL_HEADERS = (
    "组别",
    "范围",
    "局数",
    "着手数",
    "组内总子损",
    "局等权平均子损",
    "着手等权平均子损",
    "零子损率",
    "子损≥4节点数",
    "子损≥10节点数",
    "子损≥4比例",
    "子损≥10比例",
    "预计子损≥4节点数",
    "预计子损≥10节点数",
    "实际−预测≥4（节点归一）",
    "实际−预测≥10（节点归一）",
    "实际−预测≥4（整局归一）",
    "实际−预测≥10（整局归一）",
)


def render_loss_control_table(controls: list[dict[str, Any]]) -> str:
    return markdown_table(
        list(LOSS_CONTROL_HEADERS),
        [
            [
                row["group"],
                row["segment"],
                row["gameCount"],
                row["moveCount"],
                fmt_number(row["totalLoss"]),
                fmt_number(row["gameWeightedMeanLoss"], 6),
                fmt_number(row["moveWeightedMeanLoss"], 6),
                fmt_percent(row["zeroLossRate"]),
                row["lossAtLeast4Count"],
                row["lossAtLeast10Count"],
                fmt_percent(row["lossAtLeast4Rate"]),
                fmt_percent(row["lossAtLeast10Rate"]),
                row["expectedLossAtLeast4Count"],
                row["expectedLossAtLeast10Count"],
                row["lossAtLeast4ActualMinusPredictedPerNode"],
                row["lossAtLeast10ActualMinusPredictedPerNode"],
                row["lossAtLeast4ActualMinusPredictedPerGame"],
                row["lossAtLeast10ActualMinusPredictedPerGame"],
            ]
            for row in controls
        ],
    )


def fmt_number(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "—"
    if math.isclose(value, round(value), abs_tol=10 ** (-(digits + 1))):
        return str(int(round(value)))
    return f"{value:.{digits}f}"


def fmt_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def fmt_rating(value: float) -> str:
    return fmt_number(value, 3)


def fmt_duration(ms: float) -> str:
    return f"{ms / 1000:.3f} 秒"


def fmt_signed_duration(ms: float) -> str:
    return f"{ms / 1000:+.3f} 秒"


def fmt_time_control(ms: float) -> str:
    if math.isclose(ms % 60000, 0.0):
        return f"{int(ms // 60000)} 分钟"
    return fmt_duration(ms)


def image_markdown(relative_path: str, alt: str, caption: str) -> str:
    return f"![{alt}]({relative_path})\n\n*{caption}*"


def build_sections(
    config: dict[str, Any],
    bundle: dict[str, Any],
    summary: dict[str, Any],
    player: dict[str, Any],
    loss_rows: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    time_rows: list[dict[str, Any]],
    time_comparisons: list[dict[str, Any]],
    rating_rows: list[dict[str, Any]],
    rating_control_count: int,
    profile_relative: str,
    opening_relative: str,
) -> dict[str, str]:
    engine = summary.get("engine")
    if not isinstance(engine, dict):
        raise ChecklistError("engineSummary.engine must be an object")
    engine_name = required_string(engine, "name", "engineSummary.engine")
    engine_version = required_string(engine, "version", "engineSummary.engine").splitlines()[0]
    engine_level = required_integer(engine, "level", "engineSummary.engine")
    engine_text = f"{engine_name}；{engine_version}；level {engine_level}"
    player_info = markdown_table(
        ["项目", "内容"],
        [
            ["被举报选手", config["playerDisplayName"]],
            ["OQ 账号", config["account"]],
            ["举报局 ID", "<br>".join(f"`{game_id}`" for game_id in config["reportedGameIds"])],
            ["OQ API 当前可见对局", len(bundle["index"])],
            ["有效 EG 整局结果", required_integer(player, "gameCount", f"engineSummary player {config['account']}")],
            ["Rating 分组对照局", f"{rating_control_count}（已排除 {len(config['reportedGameIds'])} 盘举报局）"],
            ["EG 引擎", engine_text],
        ],
    )

    reported_headers = [
            "gameId",
            "整局着手",
            "整局总子损",
            "整局平均子损",
            "整局子损≥4数",
            "整局子损≥10数",
            "整局子损≥4比例",
            "整局子损≥10比例",
            "脱谱后着手",
            "脱谱后总子损",
            "脱谱后平均子损",
            "脱谱后子损≥4数",
            "脱谱后子损≥10数",
            "脱谱后子损≥4比例",
            "脱谱后子损≥10比例",
        ]
    show_wld = any("engineWldLossTotalFromPly39" in row for row in loss_rows)
    if show_wld:
        if not all("engineWldLossTotalFromPly39" in row for row in loss_rows):
            raise ChecklistError("reported game WLD totals are only partially available")
        reported_headers.append("从 ply 39 起 WLD 损失加权总和")
    reported_values = [
            [
                f"`{row['gameId']}`",
                row["fullMoveCount"],
                fmt_number(row["fullTotalLoss"]),
                fmt_number(row["fullAverageLoss"], 6),
                row["fullLossAtLeast4Count"],
                row["fullLossAtLeast10Count"],
                fmt_percent(row["fullLossAtLeast4Rate"]),
                fmt_percent(row["fullLossAtLeast10Rate"]),
                row["offbookMoveCount"],
                fmt_number(row["offbookTotalLoss"]),
                fmt_number(row["offbookAverageLoss"], 6),
                row["offbookLossAtLeast4Count"],
                row["offbookLossAtLeast10Count"],
                fmt_percent(row["offbookLossAtLeast4Rate"]),
                fmt_percent(row["offbookLossAtLeast10Rate"]),
                *(
                    [fmt_number(row["engineWldLossTotalFromPly39"], 3)]
                    if show_wld
                    else []
                ),
            ]
            for row in loss_rows
        ]
    reported_table = markdown_table(reported_headers, reported_values)

    control_table = render_loss_control_table(controls)

    time_table = markdown_table(
        [
            "gameId",
            "对手",
            "对手赛前 Rating",
            "时限",
            "执色",
            "着手数",
            "总用时",
            "平均每手",
            "相对基线平均残差",
            "最长单手",
            "长考数",
            "长考率",
        ],
        [
            [
                f"`{row['gameId']}`",
                row["opponent"],
                fmt_rating(row["opponentOldR"]),
                fmt_time_control(row["timeControlMs"]),
                row["colorLabel"],
                row["moveCount"],
                fmt_duration(row["totalTimeMs"]),
                fmt_duration(row["meanTimeMs"]),
                fmt_signed_duration(row["meanResidualFromBaselineMs"]),
                fmt_duration(row["maximumTimeMs"]),
                row["longThinkCount"],
                fmt_percent(row["longThinkRate"]),
            ]
            for row in time_rows
        ],
    )
    time_comparison_table = markdown_table(
        [
            "组别",
            "局数",
            "着手数",
            "总用时",
            "每局的每手平均（局等权）",
            "每手平均（着手等权）",
            "相对基线平均残差（局等权）",
            "长考总数",
            "长考率（局等权）",
        ],
        [
            [
                row["group"],
                row["gameCount"],
                row["moveCount"],
                fmt_duration(row["totalTimeMs"]),
                fmt_duration(row["gameWeightedMeanTimeMs"]),
                fmt_duration(row["moveWeightedMeanTimeMs"]),
                fmt_signed_duration(row["gameWeightedMeanResidualMs"]),
                row["longThinkCount"],
                fmt_percent(row["gameWeightedLongThinkRate"]),
            ]
            for row in time_comparisons
        ],
    )
    time_section = (
        "### 4.1 举报局逐局用时\n\n"
        + time_table
        + "\n\n### 4.2 举报局与同色个人对照\n\n"
        + time_comparison_table
        + "\n\n> 基线使用同色非举报对照局，以算术平均为中心拟合固定四节点的三次回归样条；"
        "长考定义为超过相同 ply 对照用时的第 90 百分位。汇总表标明局等权或着手等权口径。"
    )

    rating_table = markdown_table(
        [
            "对手赛前 Rating",
            "样本局数",
            "组内总子损",
            "每盘总子损平均",
            "每盘总子损中位",
            "每盘平均子损平均",
            "每盘平均子损中位",
        ],
        [
            [
                row["rating"],
                row["gameCount"],
                fmt_number(row["totalLoss"]),
                fmt_number(row["meanGameTotalLoss"]),
                fmt_number(row["medianGameTotalLoss"]),
                fmt_number(row["meanGameAverageLoss"]),
                fmt_number(row["medianGameAverageLoss"]),
            ]
            for row in rating_rows
        ],
    )

    return {
        "PLAYER_INFO_TABLE": player_info,
        "PROFILE_IMAGE": image_markdown(profile_relative, "选手资料截图", "选手资料截图"),
        "REPORTED_LOSS_TABLE": reported_table,
        "CONTROL_LOSS_TABLE": control_table,
        "REPORTED_TIME_TABLE": time_section,
        "OPENING_IMAGE": image_markdown(opening_relative, "OQ 开局排行截图", "OQ 开局排行截图"),
        "RATING_BUCKET_TABLE": rating_table,
    }


def render_template(template: str, sections: dict[str, str]) -> str:
    rendered = template
    for name, value in sections.items():
        rendered = rendered.replace("{{" + name + "}}", value)
    unresolved = sorted(set(PLACEHOLDER_RE.findall(rendered)))
    if unresolved:
        raise ChecklistError(f"unresolved template placeholders: {', '.join(unresolved)}")
    return rendered.rstrip() + "\n"


def assert_output_targets_absent(targets: list[Path]) -> None:
    normalized = [str(path).casefold() for path in targets]
    if len(set(normalized)) != len(normalized):
        raise ChecklistError("outputMarkdown, outputData, and copied image targets must be distinct")
    conflicts = [path for path in targets if path.exists()]
    if conflicts:
        joined = "\n  ".join(str(path) for path in conflicts)
        raise ChecklistError(f"refusing to overwrite existing output(s):\n  {joined}")


def write_text_no_bom(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve(strict=False)
    config, paths = load_config(config_path)

    data: dict[str, dict[str, Any]] = {}
    for label in EXPECTED_SCHEMAS:
        data[label] = load_json(paths[label], label)
        validate_schema(data[label], label)

    bundle = data["accountBundle"]
    summary = data["engineSummary"]
    same_model = data["sameColorModel"]
    all_model = data["allControlModel"]
    comparison_stats = data["comparisonStats"]
    account = config["account"]
    reported_ids = config["reportedGameIds"]

    validate_account_sources(account, bundle, summary)
    validate_model_reported_ids(same_model, "sameColorModel", reported_ids)
    validate_model_reported_ids(all_model, "allControlModel", reported_ids)
    player = player_summary(summary, account)

    index_by_id = unique_by_id(required_list(bundle, "index", "accountBundle"), "accountBundle.index")
    detail_by_id = unique_by_id(
        required_list(bundle, "details", "accountBundle"), "accountBundle.details"
    )
    for game_id in reported_ids:
        if game_id not in index_by_id:
            raise ChecklistError(f"reported game {game_id!r} is missing from accountBundle.index")
        if game_id not in detail_by_id:
            raise ChecklistError(f"reported game {game_id!r} is missing from accountBundle.details")

    target = comparison_stats.get("target")
    if not isinstance(target, dict):
        raise ChecklistError("comparisonStats.target must be an object")
    target_full = target.get("fullGame")
    if not isinstance(target_full, dict) or not isinstance(target_full.get("loss"), dict):
        raise ChecklistError("comparisonStats.target.fullGame.loss must be an object")
    if set(extract_stat_game_ids(target_full["loss"], "comparisonStats.target.fullGame.loss")) != set(reported_ids):
        raise ChecklistError(
            "comparisonStats target full-game IDs do not match config.reportedGameIds"
        )

    reference_name, reference = select_reference(
        comparison_stats, config.get("comparisonReference")
    )
    loss_rows = reported_game_rows(reported_ids, same_model)
    controls = control_rows(same_model, all_model, reference_name, reference)
    time_metrics, time_comparisons = time_rows(
        reported_ids,
        account,
        paths["engineSummary"].parent,
        same_model,
        detail_by_id,
        loss_rows,
    )
    rating_rows, rating_control_count = rating_bucket_rows(
        account, reported_ids, player, detail_by_id
    )

    output_markdown = paths["outputMarkdown"]
    assets_dir = output_markdown.parent / "assets"
    profile_target = assets_dir / f"profile-image{paths['profileImage'].suffix.lower()}"
    opening_target = assets_dir / f"oq-opening-ranking{paths['openingImage'].suffix.lower()}"
    output_targets = [output_markdown, profile_target, opening_target]
    if "outputData" in paths:
        output_targets.append(paths["outputData"])
    assert_output_targets_absent(output_targets)

    profile_relative = profile_target.relative_to(output_markdown.parent).as_posix()
    opening_relative = opening_target.relative_to(output_markdown.parent).as_posix()
    template_text = paths["template"].read_text(encoding="utf-8")
    sections = build_sections(
        config,
        bundle,
        summary,
        player,
        loss_rows,
        controls,
        time_metrics,
        time_comparisons,
        rating_rows,
        rating_control_count,
        profile_relative,
        opening_relative,
    )
    rendered = render_template(template_text, sections)

    computed = {
        "schema": "review-checklist-data-v1",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "playerDisplayName": config["playerDisplayName"],
        "account": account,
        "reportedGameIds": reported_ids,
        "ratingControlGameCount": rating_control_count,
        "reportedGames": loss_rows,
        "reportedTimes": time_metrics,
        "timeComparisons": time_comparisons,
        "controlLossRows": controls,
        "ratingBuckets": rating_rows,
        "images": {
            "profile": profile_relative,
            "oqOpeningRanking": opening_relative,
        },
        "sources": {key: str(paths[key]) for key in EXPECTED_SCHEMAS},
    }

    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(paths["profileImage"], profile_target)
    shutil.copy2(paths["openingImage"], opening_target)
    write_text_no_bom(output_markdown, rendered)
    if "outputData" in paths:
        paths["outputData"].parent.mkdir(parents=True, exist_ok=True)
        write_text_no_bom(
            paths["outputData"],
            json.dumps(computed, ensure_ascii=False, indent=2) + "\n",
        )

    print(f"Checklist written: {output_markdown}")
    if "outputData" in paths:
        print(f"Computed data written: {paths['outputData']}")
    print(f"Rating control games: {rating_control_count}")
    print(f"Reported games excluded from Rating controls: {len(reported_ids)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ChecklistError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
