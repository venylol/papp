"""Shared deterministic off-book detection core.

The Level22 sentinel CLI and the Level18 TCN feature materializer both call
this module.  The callers are responsible for assembling their engine rows;
the candidate-selection algorithm itself lives here exactly once.
"""

from __future__ import annotations

import math
from typing import Any, Iterable


ALGORITHM_LABEL = "first-log-time-or-abs6-with-post-fast-v5"
MIN_PLY = 5
BASE_TIME_LIMIT_SECONDS = 300.0
BASE_TIME_THRESHOLD_MS = 5500.0
BASE_FAST_THRESHOLD_MS = 2000.0
POST_FAST_LOOKAHEAD_TARGET_MOVES = 4
POST_FAST_REJECT_STREAK = 3
ABSOLUTE_EVALUATION_THRESHOLD = 6.0


def finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def time_threshold_ms(time_limit_ms: Any) -> float:
    limit_ms = finite_number(time_limit_ms, "timeLimitMs")
    if limit_ms <= 0:
        raise ValueError("timeLimitMs must be positive")
    limit_seconds = limit_ms / 1000.0
    return BASE_TIME_THRESHOLD_MS * (
        math.log1p(limit_seconds) / math.log1p(BASE_TIME_LIMIT_SECONDS)
    )


def fast_threshold_ms(time_limit_ms: Any) -> float:
    limit_ms = finite_number(time_limit_ms, "timeLimitMs")
    if limit_ms <= 0:
        raise ValueError("timeLimitMs must be positive")
    limit_seconds = limit_ms / 1000.0
    return BASE_FAST_THRESHOLD_MS * (
        math.log1p(limit_seconds) / math.log1p(BASE_TIME_LIMIT_SECONDS)
    )


def algorithm_contract() -> dict[str, Any]:
    """Return the immutable algorithm contract for manifests and audits."""

    return {
        "label": ALGORITHM_LABEL,
        "clipMinPlyInclusive": MIN_PLY,
        "capMaxPlyInclusive": None,
        "timeComparison": (
            "first target-player placement with thinkingTimeMs greater than "
            "5500 * ln(1 + timeLimitSeconds) / ln(301)"
        ),
        "baseTimeLimitSeconds": BASE_TIME_LIMIT_SECONDS,
        "baseTimeThresholdMs": BASE_TIME_THRESHOLD_MS,
        "baseFastThresholdMs": BASE_FAST_THRESHOLD_MS,
        "postFastLookaheadTargetMoves": POST_FAST_LOOKAHEAD_TARGET_MOVES,
        "postFastRejectConsecutiveQuickMoves": POST_FAST_REJECT_STREAK,
        "evaluationComparison": "first target-player placement with abs(bestEval) > 6.0",
        "evaluationThresholdIsStrict": True,
        "evaluationUsesLoss": False,
        "timeSearchEnd": (
            "strictly before the first abs(bestEval) > 6.0 node, or through game end when absent"
        ),
        "anchorSelection": (
            "first time candidate passing post-fast validation before evaluation cutoff; "
            "otherwise evaluation cutoff if it passes; otherwise no_offbook"
        ),
    }


def _canonical_target_nodes(
    game_id: str, target_nodes: Iterable[dict[str, Any]], target_color: str
) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for node in target_nodes:
        if not isinstance(node, dict):
            raise ValueError(f"game {game_id!r} target node must be an object")
        ply_value = node.get("ply")
        if (
            isinstance(ply_value, bool)
            or not isinstance(ply_value, (int, float))
            or not float(ply_value).is_integer()
        ):
            raise ValueError(f"game {game_id!r} has an invalid target-player ply")
        ply = int(ply_value)
        thinking_time = finite_number(
            node.get("thinkingTimeMs"), f"game {game_id!r} ply {ply} thinkingTimeMs"
        )
        if thinking_time < 0:
            raise ValueError(f"game {game_id!r} ply {ply} thinkingTimeMs must be nonnegative")
        best_eval = finite_number(
            node.get("bestEval"), f"game {game_id!r} ply {ply} bestEval"
        )
        node_color = str(node.get("playerColor") or target_color).lower()
        if node_color not in {"black", "white"}:
            raise ValueError(f"game {game_id!r} ply {ply} has an invalid playerColor")
        if node_color != target_color:
            raise ValueError(f"game {game_id!r} target account changes color")
        canonical.append({
            "ply": ply,
            "move": node.get("move"),
            "thinkingTimeMs": thinking_time,
            "bestEval": best_eval,
            "targetDecisionNumber": len(canonical) + 1,
        })
    return canonical


def detect_target_offbook(
    game_id: str,
    target_color: str,
    target_nodes: Iterable[dict[str, Any]],
    time_limit_ms: Any,
    *,
    label_source: str = ALGORITHM_LABEL,
    record_schema: str | None = None,
    extra_record_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect one directed side record using the formal algorithm.

    ``target_nodes`` must be ordered in the target player's actual placement
    order.  No caller may pre-filter evaluation cutoffs or post-fast lookahead;
    those rules are deliberately implemented here.
    """

    game_id = str(game_id or "")
    target_color = str(target_color or "").lower()
    if target_color not in {"black", "white"}:
        raise ValueError(f"game {game_id!r} has an invalid target color")
    limit_ms = finite_number(time_limit_ms, f"game {game_id!r} timeLimitMs")
    threshold_ms = time_threshold_ms(limit_ms)
    quick_threshold_ms = fast_threshold_ms(limit_ms)
    nodes = _canonical_target_nodes(game_id, target_nodes, target_color)

    evaluation_cutoff = next((
        node for node in nodes
        if node["ply"] >= MIN_PLY
        and abs(node["bestEval"]) > ABSOLUTE_EVALUATION_THRESHOLD
    ), None)
    cutoff_ply = evaluation_cutoff["ply"] if evaluation_cutoff is not None else None

    time_candidates: list[dict[str, Any]] = []
    for node in nodes:
        ply = node["ply"]
        eligible = (
            ply >= MIN_PLY
            and (cutoff_ply is None or ply < cutoff_ply)
        )
        if eligible and node["thinkingTimeMs"] > threshold_ms:
            time_candidates.append({
                **node,
                "anchorSource": (
                    "time_rule_before_evaluation_cutoff"
                    if cutoff_ply is not None
                    else "time_rule_without_evaluation_cutoff"
                ),
                "timeThresholdMs": threshold_ms,
            })
        if cutoff_ply is not None and ply >= cutoff_ply:
            break

    def post_fast_check(candidate: dict[str, Any]) -> dict[str, Any]:
        start = int(candidate["targetDecisionNumber"])
        following = nodes[start:start + POST_FAST_LOOKAHEAD_TARGET_MOVES]
        longest_streak = 0
        current_streak = 0
        moves: list[dict[str, Any]] = []
        for node in following:
            is_quick = node["thinkingTimeMs"] <= quick_threshold_ms
            current_streak = current_streak + 1 if is_quick else 0
            longest_streak = max(longest_streak, current_streak)
            moves.append({
                "ply": node["ply"],
                "targetDecisionNumber": node["targetDecisionNumber"],
                "thinkingTimeMs": node["thinkingTimeMs"],
                "isQuick": is_quick,
            })
        if len(following) < POST_FAST_REJECT_STREAK:
            status = "insufficient"
            accepted = True
        elif longest_streak >= POST_FAST_REJECT_STREAK:
            status = "rejected"
            accepted = False
        else:
            status = "passed"
            accepted = True
        return {
            "status": status,
            "accepted": accepted,
            "quickThresholdMs": quick_threshold_ms,
            "lookaheadTargetMoveLimit": POST_FAST_LOOKAHEAD_TARGET_MOVES,
            "rejectConsecutiveQuickMoves": POST_FAST_REJECT_STREAK,
            "observedTargetMoveCount": len(following),
            "longestConsecutiveQuickMoves": longest_streak,
            "moves": moves,
        }

    candidate_checks: list[dict[str, Any]] = []
    anchor: dict[str, Any] | None = None
    for candidate in time_candidates:
        check = post_fast_check(candidate)
        candidate_checks.append({
            "ply": candidate["ply"],
            "targetDecisionNumber": candidate["targetDecisionNumber"],
            "anchorSource": candidate["anchorSource"],
            "thinkingTimeMs": candidate["thinkingTimeMs"],
            "bestEval": candidate["bestEval"],
            "postFastCheck": check,
        })
        if check["accepted"]:
            anchor = {**candidate, "postFastCheck": check}
            break
    if anchor is None and evaluation_cutoff is not None:
        candidate = {**evaluation_cutoff, "anchorSource": "absolute_evaluation_cutoff"}
        check = post_fast_check(candidate)
        candidate_checks.append({
            "ply": candidate["ply"],
            "targetDecisionNumber": candidate["targetDecisionNumber"],
            "anchorSource": candidate["anchorSource"],
            "thinkingTimeMs": candidate["thinkingTimeMs"],
            "bestEval": candidate["bestEval"],
            "postFastCheck": check,
        })
        if check["accepted"]:
            anchor = {**candidate, "postFastCheck": check}

    result: dict[str, Any] = {
        "gameId": game_id,
        "targetColor": target_color,
        "judgment": "offbook" if anchor is not None else "no_offbook",
        "algorithmLabel": "offbook" if anchor is not None else "no_offbook",
        "labelSource": label_source,
        "algorithmLabelVersion": ALGORITHM_LABEL,
        "timeLimitMs": limit_ms,
        "timeThresholdMs": threshold_ms,
        "fastThresholdMs": quick_threshold_ms,
        "targetMoveCount": len(nodes),
        "offBookPly": anchor["ply"] if anchor is not None else None,
        "postOffBookStartsAtPly": anchor["ply"] if anchor is not None else None,
        "targetDecisionNumber": anchor["targetDecisionNumber"] if anchor is not None else None,
        "move": anchor["move"] if anchor is not None else None,
        "thinkingTimeMs": anchor["thinkingTimeMs"] if anchor is not None else None,
        "bestEval": anchor["bestEval"] if anchor is not None else None,
        "anchorSource": anchor["anchorSource"] if anchor is not None else None,
        "postFastCheck": anchor["postFastCheck"] if anchor is not None else None,
        "algorithmEvidence": {
            "noAnchorReason": (
                "target_player_has_no_placement"
                if not nodes
                else (
                    "no_time_threshold_or_abs6_match_from_ply_5"
                    if not time_candidates and evaluation_cutoff is None
                    else ("all_candidates_rejected_by_post_fast_check" if anchor is None else None)
                )
            ),
            "time": ({
                "timeThresholdMs": anchor["timeThresholdMs"],
            } if anchor is not None and anchor["anchorSource"].startswith("time_rule") else None),
            "postFastPolicy": {
                "baseFastThresholdMsAt300Seconds": BASE_FAST_THRESHOLD_MS,
                "quickComparison": "thinkingTimeMs <= dynamic quick threshold",
                "lookaheadTargetMoveLimit": POST_FAST_LOOKAHEAD_TARGET_MOVES,
                "rejectConsecutiveQuickMoves": POST_FAST_REJECT_STREAK,
                "insufficientFollowingMovesAcceptsCandidate": True,
            },
            "candidateChecks": candidate_checks,
            "rejectedCandidates": [
                item for item in candidate_checks
                if item["postFastCheck"]["status"] == "rejected"
            ],
            "evaluationCutoff": ({
                "ply": evaluation_cutoff["ply"],
                "bestEval": evaluation_cutoff["bestEval"],
                "absoluteBestEval": abs(evaluation_cutoff["bestEval"]),
                "comparison": ">",
                "threshold": ABSOLUTE_EVALUATION_THRESHOLD,
            } if evaluation_cutoff is not None else None),
        },
    }
    if record_schema is not None:
        result["recordSchema"] = record_schema
    if extra_record_fields:
        collision = set(result) & set(extra_record_fields)
        if collision:
            raise ValueError(f"off-book record extra fields collide with core fields: {sorted(collision)}")
        result.update(extra_record_fields)
    return result
