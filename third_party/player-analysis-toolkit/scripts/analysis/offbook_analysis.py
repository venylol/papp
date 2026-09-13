from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import re
import statistics
import sys
from pathlib import Path
from typing import Any

TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOLKIT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from player_analysis_toolkit.analysis_core import (
    ENGINE_WLD_TOTAL_FIELD,
    account_key,
    aggregate_loss_games,
    book_lookup,
    build_personal_book,
    bundle_games,
    compare_loss_groups,
    disc_loss,
    game_players,
    load_engine_games,
    mean,
    quantile,
    read_json,
    replay_game,
    rounded,
    target_engine_games,
    target_side,
    threshold_count,
    two_part_model,
    validate_wld_from_ply,
    model_probability_summary,
    write_json,
)


POST_OFFBOOK_PHASES = (
    {"key": "ply1To30", "start": 1, "stop": 31},
    {"key": "ply31To47", "start": 31, "stop": 48},
    {"key": "ply48To53", "start": 48, "stop": 54},
    {"key": "ply54To60", "start": 54, "stop": 61},
)
PHASE_METRICS = (
    "gameWeightedMeanLoss",
    "moveWeightedMeanLoss",
    "zeroLossRate",
    "positiveLossMean",
    "lossAtLeast4Rate",
    "lossAtLeast10Rate",
)
REFERENCE_EXPOSURE_COUNTS = {
    "ply1To30": 48631,
    "ply31To47": 51032,
    "ply48To53": 18040,
    "ply54To60": 17921,
}
REFERENCE_EXPOSURE_TOTAL = sum(REFERENCE_EXPOSURE_COUNTS.values())
PHASE_WEIGHT_SCHEMES = {
    "referenceHumanExposure": {
        "role": "primary",
        "weights": {
            key: count / REFERENCE_EXPOSURE_TOTAL
            for key, count in REFERENCE_EXPOSURE_COUNTS.items()
        },
    },
    "equalPhase": {
        "role": "sensitivity",
        "weights": {phase["key"]: 0.25 for phase in POST_OFFBOOK_PHASES},
    },
}
PHASE_BOOTSTRAP_MINIMUM_SUCCESS_RATE = 0.95
COORDINATE_MOVE_RE = re.compile(r"^[a-h][1-8]$", re.IGNORECASE)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_new_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"output already exists; choose a new path: {target}")
    write_json(target, value)


def packet_game(
    detail: dict[str, Any],
    details: list[dict[str, Any]],
    account: str,
    mode: str,
    excluded_from_book: set[str],
    max_ply: int,
) -> dict[str, Any]:
    game_id = str(detail.get("id") or "")
    color = target_side(detail, account)
    if color is None:
        raise ValueError(f"game {game_id!r} does not contain target account {account!r}")
    players = game_players(detail)
    target = account_key(account)
    records = replay_game(detail, max_ply=max_ply)
    lookup: dict[tuple[int, str], int] = {}
    book: dict[str, Any] | None = None
    if mode == "target":
        book = build_personal_book(
            details,
            account,
            color,
            excluded_from_book | {game_id},
            max_ply,
        )
        lookup = book_lookup(book)

    target_decision = 0
    plies: list[dict[str, Any]] = []
    for record in records:
        is_target = account_key(record["playerAccount"]) == target
        decision_number = None
        if is_target:
            target_decision += 1
            decision_number = target_decision
        item: dict[str, Any] = {
            "ply": int(record["ply"]),
            "sourceMoveIndex": int(record["sourceMoveIndex"]),
            "move": record["move"],
            "playerColor": record["playerColor"],
            "playerAccount": record["playerAccount"],
            "isTargetMove": is_target,
            "targetDecisionNumber": decision_number,
            "thinkingTimeMs": record.get("thinkingTimeMs"),
            "eligibleForManualOffBookMark": is_target,
        }
        if mode == "target":
            ply = int(record["ply"])
            parent_count = lookup.get((ply - 1, str(record["parentKey"])), 0)
            result_count = lookup.get((ply, str(record["childKey"])), 0)
            item["frequencyBook"] = {
                "available": True,
                "parentNode": [record["parentKey"], parent_count, ply - 1],
                "resultNode": [record["childKey"], result_count, ply],
                "parentChildRatio": rounded(result_count / parent_count) if parent_count else None,
            }
        plies.append(item)

    opponent_index = 1 if color == "black" else 0
    game: dict[str, Any] = {
        "gameId": game_id,
        "created": detail.get("created"),
        "targetColor": color,
        "opponentAccount": players[opponent_index].get("id"),
        "actualMoveCount": len(records),
        "targetMoveCount": target_decision,
        "eligibleOffBookPlies": [item["ply"] for item in plies if item["isTargetMove"]],
        "plies": plies,
    }
    if book is not None:
        game["frequencyBookPolicy"] = {
            "available": True,
            "color": color,
            "sameColorLeaveOneOut": True,
            "sourceGameCount": book["sourceGameCount"],
            "sourceGameIds": book["sourceGameIds"],
            "globallyExcludedGameIds": sorted(excluded_from_book),
        }
    else:
        game["frequencyBookPolicy"] = {
            "available": False,
            "reason": "reference player packets are reviewed from raw per-ply thinking time only",
        }
    return game


def build_review_packet(
    bundle: dict[str, Any],
    account: str,
    mode: str,
    game_ids: set[str] | None = None,
    excluded_from_book: set[str] | None = None,
    max_ply: int = 60,
) -> dict[str, Any]:
    if mode not in {"target", "reference"}:
        raise ValueError("mode must be target or reference")
    if max_ply < 1:
        raise ValueError("max_ply must be at least 1")
    if mode == "reference" and excluded_from_book:
        raise ValueError("reference mode has no Frequency Book, so book exclusions are not applicable")
    details = bundle_games(bundle)
    all_ids = {str(detail.get("id") or "") for detail in details}
    selected_ids = set(game_ids or [])
    if selected_ids:
        missing = sorted(selected_ids - all_ids)
        if missing:
            raise ValueError(f"game IDs not found in bundle: {missing}")
    selected = [
        detail for detail in details
        if target_side(detail, account) is not None
        and (not selected_ids or str(detail.get("id") or "") in selected_ids)
    ]
    selected_target_ids = {str(detail.get("id") or "") for detail in selected}
    wrong_account = sorted(selected_ids - selected_target_ids)
    if wrong_account:
        raise ValueError(f"game IDs do not contain target account {account!r}: {wrong_account}")
    if not selected:
        raise ValueError(f"no bundle games contain target account {account!r}")
    excluded = set(excluded_from_book or [])
    games = [
        packet_game(detail, details, account, mode, excluded, max_ply)
        for detail in sorted(selected, key=lambda item: (str(item.get("created") or ""), str(item.get("id") or "")))
    ]
    return {
        "schema": "player-offbook-agent-review-packet-v1",
        "account": account,
        "mode": mode,
        "gameCount": len(games),
        "maxPlyInclusive": max_ply,
        "thinkingTimePolicy": (
            "thinkingTimeMs is the original OQ position.moves[*].t value without transformation; "
            "manual review must treat per-ply thinking time and within-game continuity as the primary evidence"
        ),
        "manualReviewPolicy": {
            "agentJudgmentRequired": True,
            "automaticOffBookMarking": False,
            "thinkingTimeIsPrimaryEvidence": True,
            "reviewTimeContinuityBeforeAndAfterCandidate": True,
            "fixedTimeThresholdProhibited": True,
            "frequencyBookIsWeakReferenceOnly": True,
            "frequencyBookMissAloneCannotSetAnchor": True,
            "engineLossIsSupportingContextOnly": True,
            "noConfidentTimeTransitionMeansNoOffBook": True,
            "agentNoteRequired": True,
            "oneDecisionRequiredPerGame": True,
            "offBookPlyMustBeTargetMove": True,
            "postOffBookIncludesMarkedMove": True,
        },
        "marksInputExample": {
            "schema": "player-offbook-agent-marks-input-v1",
            "account": account,
            "mode": mode,
            "reviewedBy": "agent",
            "marks": [
                {
                    "gameId": "replace-with-game-id",
                    "judgment": "offbook",
                    "offBookPly": "replace-with-target-player-ply",
                    "agentNote": "manual judgment note",
                },
                {
                    "gameId": "replace-with-another-game-id",
                    "judgment": "no_offbook",
                    "offBookPly": None,
                    "agentNote": "manual judgment note",
                },
            ],
        },
        "games": games,
    }


def validate_manual_marks(packet: dict[str, Any], marks_input: dict[str, Any]) -> dict[str, Any]:
    if packet.get("schema") != "player-offbook-agent-review-packet-v1":
        raise ValueError("packet has an unsupported schema")
    if marks_input.get("schema") != "player-offbook-agent-marks-input-v1":
        raise ValueError("marks input has an unsupported schema")
    if account_key(packet.get("account")) != account_key(marks_input.get("account")):
        raise ValueError("packet and marks input accounts do not match")
    if packet.get("mode") != marks_input.get("mode"):
        raise ValueError("packet and marks input modes do not match")
    if account_key(marks_input.get("reviewedBy")) != "agent":
        raise ValueError("reviewedBy must be agent; automatic or unreviewed marks are not accepted")
    raw_marks = marks_input.get("marks")
    if not isinstance(raw_marks, list):
        raise ValueError("marks input must contain a marks array")
    games = {str(game.get("gameId") or ""): game for game in packet.get("games", []) if isinstance(game, dict)}
    marks_by_id: dict[str, dict[str, Any]] = {}
    for raw in raw_marks:
        if not isinstance(raw, dict):
            raise ValueError("every manual mark must be an object")
        game_id = str(raw.get("gameId") or "")
        if game_id not in games:
            raise ValueError(f"manual mark refers to a game outside the packet: {game_id!r}")
        if game_id in marks_by_id:
            raise ValueError(f"game {game_id!r} has more than one manual mark")
        marks_by_id[game_id] = raw
    missing = sorted(set(games) - set(marks_by_id))
    if missing:
        raise ValueError(f"every packet game requires an Agent decision; missing: {missing}")

    records: list[dict[str, Any]] = []
    for game_id, game in games.items():
        mark = marks_by_id[game_id]
        judgment = str(mark.get("judgment") or "").strip()
        if judgment not in {"offbook", "no_offbook"}:
            raise ValueError(f"game {game_id!r} judgment must be offbook or no_offbook")
        offbook_ply = mark.get("offBookPly")
        selected_ply: dict[str, Any] | None = None
        if judgment == "offbook":
            if isinstance(offbook_ply, bool) or not isinstance(offbook_ply, int):
                raise ValueError(f"game {game_id!r} offbook judgment requires an integer offBookPly")
            selected_ply = next((item for item in game["plies"] if item["ply"] == offbook_ply), None)
            if selected_ply is None:
                raise ValueError(f"game {game_id!r} has no actual move at ply {offbook_ply}")
            if not selected_ply["isTargetMove"]:
                raise ValueError(f"game {game_id!r} ply {offbook_ply} belongs to the opponent")
            if selected_ply["playerColor"] != game["targetColor"]:
                raise ValueError(
                    f"game {game_id!r} ply {offbook_ply} color {selected_ply['playerColor']} "
                    f"does not match target color {game['targetColor']}"
                )
        elif offbook_ply is not None:
            raise ValueError(f"game {game_id!r} no_offbook judgment requires null offBookPly")
        agent_note = str(mark.get("agentNote") or "").strip()
        if not agent_note:
            raise ValueError(f"game {game_id!r} requires a non-empty agentNote")
        record = {
            "gameId": game_id,
            "targetColor": game["targetColor"],
            "judgment": judgment,
            "offBookPly": offbook_ply,
            "postOffBookStartsAtPly": offbook_ply,
            "targetDecisionNumber": selected_ply.get("targetDecisionNumber") if selected_ply else None,
            "move": selected_ply.get("move") if selected_ply else None,
            "thinkingTimeMs": selected_ply.get("thinkingTimeMs") if selected_ply else None,
            "frequencyBook": selected_ply.get("frequencyBook") if selected_ply else None,
            "agentNote": agent_note,
        }
        records.append(record)
    return {
        "schema": "player-offbook-manual-records-v1",
        "account": packet["account"],
        "mode": packet["mode"],
        "reviewedBy": "agent",
        "sourcePacketSha256": marks_input.get("sourcePacketSha256"),
        "manualReviewPolicy": packet["manualReviewPolicy"],
        "recordCount": len(records),
        "offBookRecordCount": sum(record["judgment"] == "offbook" for record in records),
        "noOffBookRecordCount": sum(record["judgment"] == "no_offbook" for record in records),
        "records": records,
    }


def raw_time_game_summary(game: dict[str, Any]) -> dict[str, Any]:
    values = [float(node["thinkingTimeMs"]) for node in game["nodes"] if node.get("thinkingTimeMs") is not None]
    return {
        "gameId": game["gameId"],
        "round": game.get("round"),
        "color": game.get("color"),
        "moveCount": len(values),
        "totalTimeMs": rounded(sum(values), 3),
        "meanTimeMs": rounded(mean(values), 3),
        "minimumTimeMs": rounded(min(values), 3) if values else None,
        "maximumTimeMs": rounded(max(values), 3) if values else None,
    }


def aggregate_raw_time(games: list[dict[str, Any]]) -> dict[str, Any]:
    per_game = [raw_time_game_summary(game) for game in games]
    values = [float(node["thinkingTimeMs"]) for game in games for node in game["nodes"] if node.get("thinkingTimeMs") is not None]
    game_means = [float(item["meanTimeMs"]) for item in per_game if item["meanTimeMs"] is not None]
    return {
        "gameCount": len(games),
        "moveCount": len(values),
        "gameWeightedMeanTimeMs": rounded(mean(game_means), 3),
        "moveWeightedMeanTimeMs": rounded(mean(values), 3),
        "totalTimeMs": rounded(sum(values), 3),
        "games": per_game,
    }


def aggregate_segment_loss(
    games: list[dict[str, Any]], wld_from_ply: int | None = None
) -> dict[str, Any]:
    """Aggregate loss while retaining explicit post-off-book boundaries per game."""
    value = aggregate_loss_games(games, wld_from_ply)
    metadata = {str(game["gameId"]): game for game in games}
    for item in value["games"]:
        game = metadata[str(item["gameId"])]
        if game.get("offBookPly") is None:
            continue
        item["offBookPly"] = int(game["offBookPly"])
        item["postOffBookStartsAtPly"] = int(game["offBookPly"])
        item["excludedPreOffBookMoveCount"] = int(game["excludedPreOffBookMoveCount"])
    return value


def audit_global_placement_ply(game: dict[str, Any]) -> dict[str, Any]:
    """Verify that EG ply is the pass-free global actual-placement coordinate."""
    source = game.get("source")
    source_nodes = source.get("nodes") if isinstance(source, dict) else None
    if not isinstance(source_nodes, list) or not source_nodes:
        raise ValueError(f"game {game.get('gameId')!r} has no source EG nodes for ply audit")
    plies: list[int] = []
    source_indices: list[int] = []
    for node in source_nodes:
        raw_ply = node.get("ply") if isinstance(node, dict) else None
        raw_source_index = node.get("sourceMoveIndex") if isinstance(node, dict) else None
        if isinstance(raw_ply, bool) or not isinstance(raw_ply, int):
            raise ValueError(f"game {game.get('gameId')!r} has a non-integer EG ply")
        if isinstance(raw_source_index, bool) or not isinstance(raw_source_index, int):
            raise ValueError(f"game {game.get('gameId')!r} has a non-integer sourceMoveIndex")
        if not COORDINATE_MOVE_RE.fullmatch(str(node.get("move") or "")):
            raise ValueError(
                f"game {game.get('gameId')!r} EG ply {raw_ply} is not an actual coordinate move"
            )
        plies.append(raw_ply)
        source_indices.append(raw_source_index)
    expected = list(range(1, len(source_nodes) + 1))
    if plies != expected:
        raise ValueError(
            f"game {game.get('gameId')!r} EG ply is not contiguous pass-free global placement ply"
        )
    if plies[-1] > 60:
        raise ValueError(f"game {game.get('gameId')!r} EG ply exceeds the Othello maximum of 60")
    if any(right <= left for left, right in zip(source_indices, source_indices[1:])):
        raise ValueError(f"game {game.get('gameId')!r} sourceMoveIndex is not strictly increasing")
    source_index_gap_count = sum(
        right > left + 1 for left, right in zip(source_indices, source_indices[1:])
    )
    return {
        "gameId": game["gameId"],
        "actualPlacementNodeCount": len(source_nodes),
        "minimumPly": plies[0],
        "maximumPly": plies[-1],
        "contiguousFromOne": True,
        "coordinateMovesOnly": True,
        "sourceMoveIndexGapCount": source_index_gap_count,
    }


def summarize_ply_audits(segments: list[dict[str, Any]]) -> dict[str, Any]:
    audits = [audit for segment in segments for audit in segment["plyAudits"]]
    return {
        "coordinateName": "globalActualPlacementPly",
        "rangeInclusive": [1, 60],
        "explicitPassConsumesPly": False,
        "targetNodesAreActualPlacementsOnly": True,
        "manualOffBookPlyUsesSameCoordinate": True,
        "conversionApplied": False,
        "auditedGameCount": len(audits),
        "maximumObservedPly": max((audit["maximumPly"] for audit in audits), default=None),
        "gamesWithSourceIndexGaps": sum(audit["sourceMoveIndexGapCount"] > 0 for audit in audits),
        "sourceIndexGapInterpretation": (
            "gaps are skipped non-placement source events such as explicit passes; "
            "they do not consume EG ply"
        ),
    }


def phase_nodes(game: dict[str, Any], start: int, stop: int) -> list[dict[str, Any]]:
    return [node for node in game["nodes"] if start <= int(node["ply"]) < stop]


def phase_metric_values(
    games: list[dict[str, Any]], start: int, stop: int
) -> dict[str, float | None]:
    per_game_losses = [
        [disc_loss(node["lossClipped"]) for node in phase_nodes(game, start, stop) if node.get("lossClipped") is not None]
        for game in games
    ]
    contributing = [losses for losses in per_game_losses if losses]
    losses = [value for game_losses in contributing for value in game_losses]
    positives = [value for value in losses if value > 0]
    return {
        "gameWeightedMeanLoss": mean(mean(game_losses) for game_losses in contributing),
        "moveWeightedMeanLoss": mean(losses),
        "zeroLossRate": (sum(value == 0 for value in losses) / len(losses)) if losses else None,
        "positiveLossMean": mean(positives),
        "lossAtLeast4Rate": (sum(value >= 4 for value in losses) / len(losses)) if losses else None,
        "lossAtLeast10Rate": (sum(value >= 10 for value in losses) / len(losses)) if losses else None,
    }


def phase_group_summary(
    games: list[dict[str, Any]], start: int, stop: int
) -> dict[str, Any]:
    all_phase_nodes = [node for game in games for node in phase_nodes(game, start, stop)]
    contributing_games = [
        game for game in games
        if any(node.get("lossClipped") is not None for node in phase_nodes(game, start, stop))
    ]
    loss_node_count = sum(
        node.get("lossClipped") is not None for node in all_phase_nodes
    )
    losses = [
        disc_loss(node["lossClipped"])
        for node in all_phase_nodes
        if node.get("lossClipped") is not None
    ]
    phase_games = [{**game, "nodes": phase_nodes(game, start, stop)} for game in games]
    values = phase_metric_values(games, start, stop)
    return {
        "groupGameCount": len(games),
        "targetNodeCount": len(all_phase_nodes),
        "lossNodeCount": loss_node_count,
        "contributingGameCount": len(contributing_games),
        "contributingIndependentGameCount": len({game["gameId"] for game in contributing_games}),
        "lossAtLeast4Count": threshold_count(losses, 4),
        "lossAtLeast10Count": threshold_count(losses, 10),
        **{metric: rounded(values[metric]) for metric in PHASE_METRICS},
        "modelProbability": {
            "lossGe4": model_probability_summary(phase_games, 4),
            "lossGe10": model_probability_summary(phase_games, 10),
        },
    }


def draw_game_cluster_indices(rng: random.Random, size: int) -> list[int]:
    return [rng.randrange(size) for _ in range(size)]


def bootstrap_interval_result(
    values: list[float], repetitions: int, minimum_success_rate: float
) -> dict[str, Any]:
    successful = len(values)
    failed = repetitions - successful
    success_rate = successful / repetitions
    accepted = successful > 0 and success_rate >= minimum_success_rate
    if accepted:
        interval = [rounded(quantile(values, 0.025)), rounded(quantile(values, 0.975))]
        reason = None
    else:
        interval = None
        reason = (
            "no successful bootstrap repetitions"
            if not successful
            else "successful repetition rate is below the fixed minimum"
        )
    return {
        "clusterBootstrap95CI": interval,
        "successfulRepetitions": successful,
        "failedRepetitions": failed,
        "successRate": rounded(success_rate),
        "minimumSuccessRate": minimum_success_rate,
        "accepted": accepted,
        "nullReason": reason,
    }


def weighted_phase_point_estimate(
    phase_values: dict[str, dict[str, float | None]],
    weights: dict[str, float],
    metric: str,
) -> float | None:
    values = [phase_values[phase["key"]].get(metric) for phase in POST_OFFBOOK_PHASES]
    if any(value is None for value in values):
        return None
    return sum(
        weights[phase["key"]] * float(value)
        for phase, value in zip(POST_OFFBOOK_PHASES, values)
    )


def post_offbook_phase_comparison(
    reported: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    repetitions: int,
    seed: int,
    minimum_success_rate: float = PHASE_BOOTSTRAP_MINIMUM_SUCCESS_RATE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not reported or not controls:
        raise ValueError("phase bootstrap requires at least one reported and one control game")
    if repetitions < 1:
        raise ValueError("phase bootstrap repetitions must be at least 1")
    if not 0 < minimum_success_rate <= 1:
        raise ValueError("phase bootstrap minimum success rate must be in (0, 1]")
    for scheme in PHASE_WEIGHT_SCHEMES.values():
        if abs(sum(scheme["weights"].values()) - 1.0) > 1e-12:
            raise ValueError("fixed phase weights must sum to one")

    point_reported: dict[str, dict[str, float | None]] = {}
    point_control: dict[str, dict[str, float | None]] = {}
    phase_output: dict[str, Any] = {}
    phase_samples = {
        phase["key"]: {metric: [] for metric in PHASE_METRICS}
        for phase in POST_OFFBOOK_PHASES
    }
    coverage = {
        phase["key"]: {
            "reportedMissingRepetitions": 0,
            "controlMissingRepetitions": 0,
            "eitherGroupMissingRepetitions": 0,
        }
        for phase in POST_OFFBOOK_PHASES
    }
    combined_samples = {
        scheme_name: {metric: [] for metric in PHASE_METRICS}
        for scheme_name in PHASE_WEIGHT_SCHEMES
    }
    strict_successes = 0

    for phase in POST_OFFBOOK_PHASES:
        key = phase["key"]
        start = phase["start"]
        stop = phase["stop"]
        point_reported[key] = phase_metric_values(reported, start, stop)
        point_control[key] = phase_metric_values(controls, start, stop)

    rng = random.Random(seed)
    for _ in range(repetitions):
        reported_indices = draw_game_cluster_indices(rng, len(reported))
        control_indices = draw_game_cluster_indices(rng, len(controls))
        sampled_reported = [reported[index] for index in reported_indices]
        sampled_control = [controls[index] for index in control_indices]
        replicate_values: dict[str, dict[str, dict[str, float | None]]] = {}
        core_complete = True
        for phase in POST_OFFBOOK_PHASES:
            key = phase["key"]
            left = phase_metric_values(sampled_reported, phase["start"], phase["stop"])
            right = phase_metric_values(sampled_control, phase["start"], phase["stop"])
            replicate_values[key] = {"reported": left, "control": right}
            left_missing = left["gameWeightedMeanLoss"] is None
            right_missing = right["gameWeightedMeanLoss"] is None
            coverage[key]["reportedMissingRepetitions"] += left_missing
            coverage[key]["controlMissingRepetitions"] += right_missing
            coverage[key]["eitherGroupMissingRepetitions"] += left_missing or right_missing
            core_complete = core_complete and not left_missing and not right_missing
            for metric in PHASE_METRICS:
                if left[metric] is not None and right[metric] is not None:
                    phase_samples[key][metric].append(float(left[metric]) - float(right[metric]))
        strict_successes += core_complete

        for scheme_name, scheme in PHASE_WEIGHT_SCHEMES.items():
            weights = scheme["weights"]
            for metric in PHASE_METRICS:
                left_values = [
                    replicate_values[phase["key"]]["reported"][metric]
                    for phase in POST_OFFBOOK_PHASES
                ]
                right_values = [
                    replicate_values[phase["key"]]["control"][metric]
                    for phase in POST_OFFBOOK_PHASES
                ]
                if any(value is None for value in left_values + right_values):
                    continue
                left_combined = sum(
                    weights[phase["key"]] * float(value)
                    for phase, value in zip(POST_OFFBOOK_PHASES, left_values)
                )
                right_combined = sum(
                    weights[phase["key"]] * float(value)
                    for phase, value in zip(POST_OFFBOOK_PHASES, right_values)
                )
                combined_samples[scheme_name][metric].append(left_combined - right_combined)

    for phase in POST_OFFBOOK_PHASES:
        key = phase["key"]
        reported_summary = phase_group_summary(reported, phase["start"], phase["stop"])
        control_summary = phase_group_summary(controls, phase["start"], phase["stop"])
        differences: dict[str, Any] = {}
        for metric in PHASE_METRICS:
            left = point_reported[key][metric]
            right = point_control[key][metric]
            interval = bootstrap_interval_result(
                phase_samples[key][metric], repetitions, minimum_success_rate
            )
            differences[metric] = {
                "estimate": rounded(float(left) - float(right))
                if left is not None and right is not None else None,
                **interval,
            }
            if differences[metric]["estimate"] is None:
                differences[metric]["pointEstimateNullReason"] = (
                    "reported or control group has no data for this phase and metric"
                )
        phase_output[key] = {
            "startPlyInclusive": phase["start"],
            "endPlyInclusive": phase["stop"] - 1,
            "halfOpenInterval": [phase["start"], phase["stop"]],
            "clusterUnit": "game",
            "postOffBookIncludesMarkedMove": True,
            "reported": reported_summary,
            "control": control_summary,
            "reportedMinusControl": differences,
            "bootstrapCoverage": coverage[key],
        }

    combined_output: dict[str, Any] = {}
    for scheme_name, scheme in PHASE_WEIGHT_SCHEMES.items():
        weights = scheme["weights"]
        metrics: dict[str, Any] = {}
        for metric in PHASE_METRICS:
            left = weighted_phase_point_estimate(point_reported, weights, metric)
            right = weighted_phase_point_estimate(point_control, weights, metric)
            interval = bootstrap_interval_result(
                combined_samples[scheme_name][metric], repetitions, minimum_success_rate
            )
            metrics[metric] = {
                "reported": rounded(left),
                "control": rounded(right),
                "reportedMinusControl": rounded(float(left) - float(right))
                if left is not None and right is not None else None,
                **interval,
            }
            if left is None or right is None:
                metrics[metric]["pointEstimateNullReason"] = (
                    "at least one fixed phase is missing; weights were not renormalized"
                )
        combined_output[scheme_name] = {
            "role": scheme["role"],
            "weights": {key: rounded(value, 12) for key, value in weights.items()},
            "metrics": metrics,
        }

    boundaries = [
        {
            "key": phase["key"],
            "startPlyInclusive": phase["start"],
            "endPlyInclusive": phase["stop"] - 1,
            "halfOpenInterval": [phase["start"], phase["stop"]],
        }
        for phase in POST_OFFBOOK_PHASES
    ]
    by_phase = {
        "phaseDefinitions": boundaries,
        "fixedBoundaryPolicy": (
            "user-selected fixed four-phase refinement for interpretive separation; "
            "not re-estimated from the reported/control dataset or per bootstrap repetition"
        ),
        "boundarySource": (
            "investigations/oq_loss_phase_boundaries_20260803/REPORT.md; "
            "user selected the archived 30|47|53 refinement after reviewing its stability tradeoff"
        ),
        "stabilityLimitation": (
            "30|47|53 improved in-sample fit over the archived three-phase partition by 28.3% "
            "but had 38.4% exact bootstrap reproduction; it is not a stable universal natural boundary"
        ),
        "engineRegimeCaution": (
            "the ply 31/32 neighborhood also contains an Egaroucid search-regime change, "
            "so phase differences must not be attributed wholly to a natural human-behavior transition"
        ),
        "bootstrapPolicy": {
            "clusterUnit": "game",
            "repetitions": repetitions,
            "seed": seed,
            "reportedSampleSizePerRepetition": len(reported),
            "controlSampleSizePerRepetition": len(controls),
            "sameSampledGameListsSharedAcrossAllPhases": True,
            "sameSampledGameListsSharedAcrossAllMetrics": True,
            "sampledGameCarriesAllPostOffBookNodesAndPhases": True,
            "minimumSuccessRate": minimum_success_rate,
            "missingPhaseRetry": False,
            "intervalInterpretation": (
                "current-sample empirical/stability interval; not a cheating probability or a reliable population boundary"
            ),
        },
        **phase_output,
    }
    combined = {
        "phaseDefinitions": boundaries,
        "clusterUnit": "game",
        "bootstrapRepetitions": repetitions,
        "bootstrapSeed": seed,
        "primaryWeightScheme": "referenceHumanExposure",
        "combinationOrder": (
            "within each bootstrap repetition, compute all four phases from the shared sampled game lists, "
            "then combine with fixed weights; only afterward take 2.5% and 97.5% quantiles"
        ),
        "missingPhasePolicy": (
            "a missing fixed phase or metric makes that combined replicate fail; "
            "weights are never renormalized and failed replicates are never silently redrawn"
        ),
        "minimumSuccessRate": minimum_success_rate,
        "intervalInterpretation": (
            "current-sample empirical/stability interval; repeated resampling does not create new independent games"
        ),
        "strictAllPhaseCoreDataSuccessfulRepetitions": strict_successes,
        "strictAllPhaseCoreDataFailedRepetitions": repetitions - strict_successes,
        "weightSchemes": combined_output,
        "weightSources": {
            "referenceHumanExposure": {
                "description": (
                    "fixed valid-loss node exposure proportions from the independent archived human reference sample"
                ),
                "archivedArtifact": (
                    "investigations/oq_loss_phase_boundaries_20260803/ply_statistics_postbook.csv"
                ),
                "nodeCounts": REFERENCE_EXPOSURE_COUNTS,
                "totalNodeCount": REFERENCE_EXPOSURE_TOTAL,
                "estimatedFromCurrentReportedOrControlSample": False,
            },
            "equalPhase": {
                "description": "four fixed phases receive 25% each; short late phases therefore receive more influence",
                "estimatedFromCurrentReportedOrControlSample": False,
            },
        },
        "coverageDiagnostics": coverage,
    }
    return by_phase, combined


def segment_member(member: dict[str, Any], expected_mode: str) -> dict[str, Any]:
    marks_path = Path(member["marks"]).resolve()
    engine_directory = Path(member["engineDirectory"]).resolve()
    account = str(member["account"])
    marks = read_json(marks_path)
    supported_schemas = {
        "player-offbook-algorithm-records-v1",
        "player-offbook-manual-records-v1",
    }
    if marks.get("schema") not in supported_schemas:
        raise ValueError(f"unsupported off-book records schema in {marks_path}")
    if marks.get("mode") != expected_mode:
        raise ValueError(f"off-book records in {marks_path} must use mode {expected_mode!r}")
    if marks.get("schema") == "player-offbook-algorithm-records-v1":
        if marks.get("labeledBy") != "algorithm":
            raise ValueError(f"algorithmic records in {marks_path} have an invalid label source")
        for record in marks.get("records", []):
            if record.get("algorithmLabel") != record.get("judgment"):
                raise ValueError(f"algorithm label/judgment mismatch in {marks_path}")
    elif account_key(marks.get("reviewedBy")) != "agent":
        raise ValueError(f"legacy manual records in {marks_path} were not recorded as Agent-reviewed")
    if account_key(marks.get("account")) != account_key(account):
        raise ValueError(f"off-book records account does not match configured account {account!r}")
    records = {str(item.get("gameId") or ""): item for item in marks.get("records", []) if isinstance(item, dict)}
    raw_game_ids = member.get("gameIds")
    configured_game_ids: list[str] | None = None
    if raw_game_ids is not None:
        if not isinstance(raw_game_ids, list) or not raw_game_ids:
            raise ValueError(f"configured gameIds for {account!r} must be a non-empty array")
        configured_game_ids = [str(game_id or "").strip() for game_id in raw_game_ids]
        if any(not game_id for game_id in configured_game_ids):
            raise ValueError(f"configured gameIds for {account!r} must not contain empty values")
        if len(set(configured_game_ids)) != len(configured_game_ids):
            raise ValueError(f"configured gameIds for {account!r} must be unique")
        unknown = sorted(set(configured_game_ids) - set(records))
        if unknown:
            raise ValueError(f"configured gameIds are missing from off-book records for {account!r}: {unknown}")
        records = {game_id: records[game_id] for game_id in configured_game_ids}
    raw_engine_games = load_engine_games(engine_directory)
    engine_games = target_engine_games(raw_engine_games, account)
    by_id = {game["gameId"]: game for game in engine_games}
    for game in raw_engine_games:
        game_id = str(game.get("gameId") or "")
        if game_id in by_id or records.get(game_id, {}).get("judgment") != "no_offbook":
            continue
        colors = [
            color for color in ("black", "white")
            if account_key((game.get(color) or {}).get("account")) == account_key(account)
        ]
        if len(colors) == 1:
            by_id[game_id] = {
                "gameId": game_id,
                "round": game.get("round"),
                "color": colors[0],
                "isTournamentGame": bool(game.get("isTournamentGame")),
                "nodes": [],
                "source": game,
            }
    missing = sorted(set(records) - set(by_id))
    if missing:
        raise ValueError(f"off-book-record games missing from EG JSON for {account!r}: {missing}")
    full_games = [by_id[game_id] for game_id in records]
    ply_audits = [audit_global_placement_ply(game) for game in full_games]
    post_games: list[dict[str, Any]] = []
    for game_id, record in records.items():
        if record.get("judgment") != "offbook":
            continue
        start = int(record["offBookPly"])
        source = by_id[game_id]
        if not any(int(node["ply"]) == start for node in source["nodes"]):
            raise ValueError(
                f"game {game_id!r} offBookPly {start} is not a target-player EG actual-placement node"
            )
        nodes = [node for node in source["nodes"] if int(node.get("ply") or 0) >= start]
        if not nodes:
            raise ValueError(f"game {game_id!r} has no target EG nodes at or after offBookPly {start}")
        post_games.append({
            **source,
            "nodes": nodes,
            "offBookPly": start,
            "excludedPreOffBookMoveCount": sum(int(node.get("ply") or 0) < start for node in source["nodes"]),
        })
    return {
        "name": str(member.get("name") or account),
        "account": account,
        "marks": str(marks_path),
        "engineDirectory": str(engine_directory),
        "configuredGameIds": configured_game_ids,
        "recordCount": len(records),
        "offBookGameCount": len(post_games),
        "noOffBookGameCount": sum(item.get("judgment") == "no_offbook" for item in records.values()),
        "fullGames": full_games,
        "postGames": post_games,
        "plyAudits": ply_audits,
    }


def public_segment(
    member_segments: list[dict[str, Any]], wld_from_ply: int | None = None
) -> dict[str, Any]:
    full_games = [game for member in member_segments for game in member["fullGames"]]
    post_games = [game for member in member_segments for game in member["postGames"]]
    return {
        "memberCount": len(member_segments),
        "members": [
            {
                key: member[key]
                for key in (
                    "name", "account", "marks", "engineDirectory", "configuredGameIds",
                    "recordCount", "offBookGameCount", "noOffBookGameCount",
                )
            }
            for member in member_segments
        ],
        "fullGame": {
            "loss": aggregate_segment_loss(full_games, wld_from_ply),
            "time": aggregate_raw_time(full_games),
        },
        "postOffBookInclusive": {
            "loss": aggregate_segment_loss(post_games, wld_from_ply),
            "time": aggregate_raw_time(post_games),
        },
    }


def consolidated_group_members(raw_specs: Any, label: str) -> list[dict[str, Any]]:
    specs = raw_specs if isinstance(raw_specs, list) else [raw_specs]
    if not specs or any(not isinstance(spec, dict) for spec in specs):
        raise ValueError(f"{label} membersFromConsolidated must be an object or object array")
    output: list[dict[str, Any]] = []
    for spec in specs:
        source_path = Path(spec["path"]).resolve()
        engine_directory = str(Path(spec["engineDirectory"]).resolve())
        opening = str(spec.get("opening") or "").strip()
        if not opening:
            raise ValueError(f"{label} consolidated member source requires opening")
        source = read_json(source_path)
        raw_records = source.get("records")
        if not isinstance(raw_records, list):
            raise ValueError(f"consolidated records file has no records array: {source_path}")
        grouped: dict[tuple[str, str], list[str]] = {}
        for raw in raw_records:
            if not isinstance(raw, dict) or str(raw.get("opening") or "") != opening:
                continue
            validated = raw.get("validatedRecord")
            if not isinstance(validated, dict):
                raise ValueError(f"consolidated record lacks validatedRecord in {source_path}")
            account = str(validated.get("account") or raw.get("leaderboardAccount") or "").strip()
            marks = str(validated.get("sourceRecords") or "").strip()
            game_id = str(raw.get("gameId") or "").strip()
            if not account or not marks or not game_id:
                raise ValueError(f"consolidated record lacks account, sourceRecords or gameId in {source_path}")
            grouped.setdefault((account, marks), []).append(game_id)
        if not grouped:
            raise ValueError(f"no consolidated records matched opening {opening!r} in {source_path}")
        for (account, marks), game_ids in grouped.items():
            output.append({
                "name": account,
                "account": account,
                "marks": marks,
                "engineDirectory": engine_directory,
                "gameIds": game_ids,
            })
    return output


def group_members(config: dict[str, Any], label: str) -> list[dict[str, Any]]:
    if config.get("membersFromConsolidated") is not None:
        if config.get("members") is not None:
            raise ValueError(f"{label} cannot define both members and membersFromConsolidated")
        return consolidated_group_members(config["membersFromConsolidated"], label)
    raw = config.get("members")
    members = raw if isinstance(raw, list) else [config]
    if not members or any(not isinstance(member, dict) for member in members):
        raise ValueError(f"{label} must contain a member object or members array")
    return members


def difference(left: dict[str, Any], right: dict[str, Any], field: str) -> float | None:
    left_value = left.get(field)
    right_value = right.get(field)
    if left_value is None or right_value is None:
        return None
    return rounded(float(left_value) - float(right_value), 6)


def individual_game_empirical_position(
    target_loss: dict[str, Any], reference_loss: dict[str, Any]
) -> dict[str, Any] | None:
    target_games = target_loss.get("games") or []
    reference_games = reference_loss.get("games") or []
    if len(target_games) != 1 or not reference_games:
        return None
    target_mean = target_games[0].get("meanLoss")
    reference_means = [game.get("meanLoss") for game in reference_games if game.get("meanLoss") is not None]
    if target_mean is None or not reference_means:
        return None
    target_value = float(target_mean)
    values = [float(value) for value in reference_means]
    less = sum(value < target_value for value in values)
    equal = sum(value == target_value for value in values)
    return {
        "targetGameId": target_games[0]["gameId"],
        "targetMeanLoss": rounded(target_value),
        "referenceGameCount": len(values),
        "referenceMinimumMeanLoss": rounded(min(values)),
        "referenceMedianMeanLoss": rounded(statistics.median(values)),
        "referenceMaximumMeanLoss": rounded(max(values)),
        "referenceLessThanTarget": less,
        "referenceEqualToTarget": equal,
        "referenceLessThanOrEqualToTarget": less + equal,
        "inclusiveEmpiricalPercentile": rounded(100 * (less + equal) / len(values), 3),
    }


def offbook_stats(
    config: dict[str, Any], wld_from_ply: int | None = None
) -> dict[str, Any]:
    boundary = validate_wld_from_ply(
        wld_from_ply if wld_from_ply is not None else config.get("wldFromPly")
    )
    target_config = config.get("target")
    if not isinstance(target_config, dict):
        raise ValueError("stats config requires a target object")
    target_segments = [segment_member(member, "target") for member in group_members(target_config, "target")]
    target = public_segment(target_segments, boundary)
    references: dict[str, Any] = {}
    comparisons: dict[str, Any] = {}
    raw_references = config.get("references", [])
    if not isinstance(raw_references, list):
        raise ValueError("stats config references must be an array")
    for index, group in enumerate(raw_references):
        if not isinstance(group, dict):
            raise ValueError("every reference group must be an object")
        name = str(group.get("name") or f"reference-{index + 1}")
        segments = [segment_member(member, "reference") for member in group_members(group, name)]
        reference = public_segment(segments, boundary)
        references[name] = reference
        target_full_loss = target["fullGame"]["loss"]
        reference_full_loss = reference["fullGame"]["loss"]
        target_loss = target["postOffBookInclusive"]["loss"]
        reference_loss = reference["postOffBookInclusive"]["loss"]
        target_time = target["postOffBookInclusive"]["time"]
        reference_time = reference["postOffBookInclusive"]["time"]
        comparisons[name] = {
            "targetMinusReferenceFullGameGameWeightedMeanLoss": difference(
                target_full_loss, reference_full_loss, "gameWeightedMeanLoss"
            ),
            "targetMinusReferenceFullGameMoveWeightedMeanLoss": difference(
                target_full_loss, reference_full_loss, "moveWeightedMeanLoss"
            ),
            "targetMinusReferenceFullGameLossAtLeast4Count": difference(
                target_full_loss, reference_full_loss, "lossAtLeast4Count"
            ),
            "targetMinusReferenceFullGameLossAtLeast10Count": difference(
                target_full_loss, reference_full_loss, "lossAtLeast10Count"
            ),
            "targetMinusReferenceFullGameLossAtLeast4Rate": difference(
                target_full_loss, reference_full_loss, "lossAtLeast4Rate"
            ),
            "targetMinusReferenceFullGameLossAtLeast10Rate": difference(
                target_full_loss, reference_full_loss, "lossAtLeast10Rate"
            ),
            "targetMinusReferencePostOffBookGameWeightedMeanLoss": difference(target_loss, reference_loss, "gameWeightedMeanLoss"),
            "targetMinusReferencePostOffBookMoveWeightedMeanLoss": difference(target_loss, reference_loss, "moveWeightedMeanLoss"),
            "targetMinusReferencePostOffBookLossAtLeast4Count": difference(
                target_loss, reference_loss, "lossAtLeast4Count"
            ),
            "targetMinusReferencePostOffBookLossAtLeast10Count": difference(
                target_loss, reference_loss, "lossAtLeast10Count"
            ),
            "targetMinusReferencePostOffBookLossAtLeast4Rate": difference(
                target_loss, reference_loss, "lossAtLeast4Rate"
            ),
            "targetMinusReferencePostOffBookLossAtLeast10Rate": difference(
                target_loss, reference_loss, "lossAtLeast10Rate"
            ),
            "targetMinusReferencePostOffBookGameWeightedMeanTimeMs": difference(target_time, reference_time, "gameWeightedMeanTimeMs"),
            "targetMinusReferencePostOffBookMoveWeightedMeanTimeMs": difference(target_time, reference_time, "moveWeightedMeanTimeMs"),
            "individualFullGameEmpiricalPosition": individual_game_empirical_position(
                target_full_loss, reference_full_loss
            ),
            "individualPostOffBookEmpiricalPosition": individual_game_empirical_position(
                target_loss, reference_loss
            ),
        }
    result = {
        "schema": "player-offbook-segment-stats-v1",
        "segmentPolicy": "post-off-book begins at and includes the Agent-marked target-player move",
        "offBookBoundaryPolicy": "segment boundaries come from the supplied validated off-book records",
        "target": target,
        "references": references,
        "comparisons": comparisons,
    }
    if boundary is not None:
        result["wldFromPly"] = boundary
    return result


def offbook_model(
    config: dict[str, Any], wld_from_ply: int | None = None
) -> dict[str, Any]:
    boundary = validate_wld_from_ply(
        wld_from_ply if wld_from_ply is not None else config.get("wldFromPly")
    )
    dataset_config = config.get("dataset")
    if not isinstance(dataset_config, dict):
        raise ValueError("offbook model config requires a dataset object")
    segments = [segment_member(member, "target") for member in group_members(dataset_config, "dataset")]
    full_games = [game for segment in segments for game in segment["fullGames"]]
    post_games = [game for segment in segments for game in segment["postGames"]]
    reported_ids = {str(game_id or "").strip() for game_id in config.get("reportedGameIds", [])}
    if not reported_ids or "" in reported_ids:
        raise ValueError("offbook model config requires non-empty reportedGameIds")
    full_by_id = {game["gameId"]: game for game in full_games}
    post_by_id = {game["gameId"]: game for game in post_games}
    missing_full = sorted(reported_ids - set(full_by_id))
    missing_post = sorted(reported_ids - set(post_by_id))
    if missing_full or missing_post:
        raise ValueError(
            f"reported games missing from segmented dataset: full={missing_full}, postOffBook={missing_post}"
        )
    bootstrap = int(config.get("bootstrap", 10000))
    model_bootstrap = int(config.get("modelBootstrap", 1000))
    seed = int(config.get("seed", 20260801))

    def analyze(games: list[dict[str, Any]], seed_offset: int) -> dict[str, Any]:
        reported = [game for game in games if game["gameId"] in reported_ids]
        controls = [game for game in games if game["gameId"] not in reported_ids]
        return {
            "comparison": compare_loss_groups(
                reported, controls, games, bootstrap, seed + seed_offset, boundary
            ),
            "clusterAwareTwoPartModel": two_part_model(
                games, reported_ids, model_bootstrap, seed + seed_offset + 20
            ),
        }

    reported_post = [game for game in post_games if game["gameId"] in reported_ids]
    control_post = [game for game in post_games if game["gameId"] not in reported_ids]
    post_by_phase, phase_combined = post_offbook_phase_comparison(
        reported_post,
        control_post,
        bootstrap,
        seed + 200,
    )

    result = {
        "schema": "player-offbook-segment-model-v1",
        "segmentPolicy": "post-off-book begins at and includes the Agent-marked target-player move",
        "offBookBoundaryPolicy": "segment boundaries come from the supplied validated off-book records",
        "reportedGameIds": sorted(reported_ids),
        "datasetGameCount": len(full_games),
        "postOffBookDatasetGameCount": len(post_games),
        "fullGame": analyze(full_games, 0),
        "postOffBookInclusive": analyze(post_games, 100),
        "postOffBookByPhase": post_by_phase,
        "postOffBookPhaseCombined": phase_combined,
        "plyCoordinateAudit": summarize_ply_audits(segments),
    }
    if boundary is not None:
        result["wldFromPly"] = boundary
    return result


def offbook_stratified_combinations(config: dict[str, Any]) -> dict[str, Any]:
    target_path = Path(config["targetStats"]).resolve()
    target_stats = read_json(target_path)
    raw_strata = config.get("strata")
    if not isinstance(raw_strata, list) or not raw_strata:
        raise ValueError("stratified comparison requires a non-empty strata array")

    def compare(segment: str) -> dict[str, Any]:
        target_loss = target_stats["target"][segment]["loss"]
        target_games = target_loss.get("games") or []
        if len(target_games) != len(raw_strata):
            raise ValueError("target game count must equal the number of reference strata")
        strata_values: list[list[float]] = []
        stratum_summaries: list[dict[str, Any]] = []
        for raw in raw_strata:
            if not isinstance(raw, dict):
                raise ValueError("every stratum must be an object")
            stats_path = Path(raw["stats"]).resolve()
            stats = read_json(stats_path)
            group = str(raw["referenceGroup"])
            games = stats["references"][group][segment]["loss"].get("games") or []
            values = [float(game["meanLoss"]) for game in games if game.get("meanLoss") is not None]
            if not values:
                raise ValueError(f"reference stratum {group!r} has no loss values for {segment}")
            strata_values.append(values)
            stratum_summaries.append({
                "referenceGroup": group,
                "stats": str(stats_path),
                "gameCount": len(values),
            })
        target_mean = mean(float(game["meanLoss"]) for game in target_games)
        combinations = [mean(values) for values in itertools.product(*strata_values)]
        less = sum(value < target_mean for value in combinations)
        equal = sum(value == target_mean for value in combinations)
        count = len(combinations)
        return {
            "targetGameCount": len(target_games),
            "targetGameWeightedMeanLoss": rounded(target_mean),
            "strata": stratum_summaries,
            "referenceCombinationCount": count,
            "referenceCombinationsLessThanTarget": less,
            "referenceCombinationsEqualToTarget": equal,
            "referenceCombinationsLessThanOrEqualToTarget": less + equal,
            "inclusiveEmpiricalPercentile": rounded(100 * (less + equal) / count, 3),
            "lowerTailPlusOneP": rounded((less + equal + 1) / (count + 1), 6),
            "referenceCombinationMinimumMeanLoss": rounded(min(combinations)),
            "referenceCombinationMedianMeanLoss": rounded(statistics.median(combinations)),
            "referenceCombinationMaximumMeanLoss": rounded(max(combinations)),
        }

    return {
        "schema": "player-offbook-stratified-combination-v1",
        "segmentPolicy": target_stats.get("segmentPolicy"),
        "targetStats": str(target_path),
        "fullGame": compare("fullGame"),
        "postOffBookInclusive": compare("postOffBookInclusive"),
    }


def command_packet(args: argparse.Namespace) -> dict[str, Any]:
    bundle_path = Path(args.bundle).resolve()
    value = build_review_packet(
        read_json(bundle_path),
        args.account,
        args.mode,
        set(args.game_id or []),
        set(args.exclude_from_book_game_id or []),
        args.max_ply,
    )
    value["sourceBundle"] = str(bundle_path)
    value["sourceBundleSha256"] = file_sha256(bundle_path)
    write_new_json(args.output, value)
    return {key: value[key] for key in ("schema", "account", "mode", "gameCount", "sourceBundle")}


def command_record(args: argparse.Namespace) -> dict[str, Any]:
    packet_path = Path(args.packet).resolve()
    marks_path = Path(args.marks).resolve()
    packet = read_json(packet_path)
    marks = read_json(marks_path)
    expected_hash = marks.get("sourcePacketSha256")
    actual_hash = file_sha256(packet_path)
    if expected_hash and expected_hash != actual_hash:
        raise ValueError("marks input sourcePacketSha256 does not match the review packet")
    value = validate_manual_marks(packet, marks)
    value["sourcePacket"] = str(packet_path)
    value["sourcePacketSha256"] = actual_hash
    value["sourceMarksInput"] = str(marks_path)
    value["sourceMarksInputSha256"] = file_sha256(marks_path)
    write_new_json(args.output, value)
    return {
        key: value[key]
        for key in ("schema", "account", "mode", "recordCount", "offBookRecordCount", "noOffBookRecordCount")
    }


def command_stats(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config).resolve()
    value = offbook_stats(read_json(config_path), args.wld_from_ply)
    value["sourceConfig"] = str(config_path)
    value["sourceConfigSha256"] = file_sha256(config_path)
    write_new_json(args.output, value)
    summary = {
        "schema": value["schema"],
        "targetPostOffBookGameCount": value["target"]["postOffBookInclusive"]["loss"]["gameCount"],
        "targetPostOffBookLossAtLeast4Count": value["target"]["postOffBookInclusive"]["loss"]["lossAtLeast4Count"],
        "targetPostOffBookLossAtLeast10Count": value["target"]["postOffBookInclusive"]["loss"]["lossAtLeast10Count"],
        "targetPostOffBookLossAtLeast4Rate": value["target"]["postOffBookInclusive"]["loss"]["lossAtLeast4Rate"],
        "targetPostOffBookLossAtLeast10Rate": value["target"]["postOffBookInclusive"]["loss"]["lossAtLeast10Rate"],
        "referenceGroups": list(value["references"]),
    }
    if value.get("wldFromPly") is not None:
        summary[ENGINE_WLD_TOTAL_FIELD] = value["target"]["fullGame"]["loss"][ENGINE_WLD_TOTAL_FIELD]
    return summary


def command_model(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config).resolve()
    value = offbook_model(read_json(config_path), args.wld_from_ply)
    value["sourceConfig"] = str(config_path)
    value["sourceConfigSha256"] = file_sha256(config_path)
    write_new_json(args.output, value)
    summary = {
        "schema": value["schema"],
        "datasetGameCount": value["datasetGameCount"],
        "postOffBookDatasetGameCount": value["postOffBookDatasetGameCount"],
        "reportedGameIds": value["reportedGameIds"],
        "reportedPostOffBookLossAtLeast4Rate": value["postOffBookInclusive"]["comparison"]["reported"]["lossAtLeast4Rate"],
        "reportedPostOffBookLossAtLeast10Rate": value["postOffBookInclusive"]["comparison"]["reported"]["lossAtLeast10Rate"],
    }
    if value.get("wldFromPly") is not None:
        summary[ENGINE_WLD_TOTAL_FIELD] = value["fullGame"]["comparison"]["reported"][ENGINE_WLD_TOTAL_FIELD]
    return summary


def command_stratified(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config).resolve()
    value = offbook_stratified_combinations(read_json(config_path))
    value["sourceConfig"] = str(config_path)
    value["sourceConfigSha256"] = file_sha256(config_path)
    write_new_json(args.output, value)
    return {
        "schema": value["schema"],
        "fullGameCombinationCount": value["fullGame"]["referenceCombinationCount"],
        "postOffBookCombinationCount": value["postOffBookInclusive"]["referenceCombinationCount"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Post-off-book statistics and model comparisons")
    sub = parser.add_subparsers(dest="command", required=True)

    stats = sub.add_parser("offbook-stats", help="calculate full-game and inclusive post-off-book statistics")
    stats.add_argument("--config", required=True)
    stats.add_argument("--wld-from-ply", type=int, choices=(39,))
    stats.add_argument("--output", required=True)
    stats.set_defaults(handler=command_stats)

    model = sub.add_parser(
        "offbook-model",
        help="compare reported games and fit two-part models before and after off-book segmentation",
    )
    model.add_argument("--config", required=True)
    model.add_argument("--wld-from-ply", type=int, choices=(39,))
    model.add_argument("--output", required=True)
    model.set_defaults(handler=command_model)

    stratified = sub.add_parser(
        "offbook-stratified",
        help="compare a target set with one reference game drawn from each configured stratum",
    )
    stratified.add_argument("--config", required=True)
    stratified.add_argument("--output", required=True)
    stratified.set_defaults(handler=command_stratified)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = args.handler(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
