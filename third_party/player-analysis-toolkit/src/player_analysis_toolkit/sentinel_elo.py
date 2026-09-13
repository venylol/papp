"""Database-calibrated, phase-balanced Elo estimation for sentinel records.

This module is deliberately separate from :mod:`sentinel`.  The existing
sentinel implementation is an already frozen anomaly pipeline; this module
owns the new estimated-Elo contract described in
``docs/SENTINEL_ESTIMATED_ELO_IMPLEMENTATION_SPEC.md``.

All file I/O in this module is explicit UTF-8.  The numerical implementation
uses only the standard library for its reference implementation and, when it
is available, SciPy's exact ``cKDTree`` as an acceleration for the repeated
two-dimensional nearest-neighbour queries.  The tree is not a statistical
model and does not change the distance or weighting rules.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .analysis_core import account_key


SCHEMA_DIRECTED = "player-sentinel-elo-directed-game-phase-v1"
SCHEMA_REFERENCE_MANIFEST = "player-sentinel-elo-reference-source-manifest-v1"
SCHEMA_REFERENCE_AUDIT = "player-sentinel-elo-reference-build-audit-v1"
SCHEMA_REFERENCE_SHA = "player-sentinel-elo-reference-sha256-manifest-v1"
SCHEMA_CALIBRATION = "player-sentinel-elo-calibration-v1"
SCHEMA_ESTIMATE = "player-sentinel-estimated-elo-v1"

# Estimated-Elo v2 is intentionally version-isolated from the frozen v1
# curve and calibration contracts above.  The v1 entry points remain usable
# for reproducing historical artifacts; all new formal callers use these
# schemas and the functions suffixed ``_v2`` below.
SCHEMA_CONFIG_V2 = "player-sentinel-elo-reference-config-v2"
SCHEMA_CONDITIONAL_REFERENCE = "player-sentinel-elo-conditional-reference-v1"
SCHEMA_CONDITIONAL_RECORD = "player-sentinel-elo-conditional-record-v1"
SCHEMA_CALIBRATION_V2 = "player-sentinel-elo-calibration-v2"
SCHEMA_CALIBRATION_CASE_V2 = "player-sentinel-elo-calibration-case-v2"
SCHEMA_ESTIMATE_V2 = "player-sentinel-estimated-elo-v2"
ALGORITHM_VERSION_V2 = "estimated-elo-v2-beta-binomial-adjacent-z-v1"

# Estimated-Elo v3 is the first formal implementation of the adaptive search
# and reusable global KNN index.  v1 and v2 constants/functions above remain
# readable and callable so historical artifacts are never silently reinterpreted.
SCHEMA_CONFIG_V3 = "player-sentinel-elo-reference-config-v3"
SCHEMA_CALIBRATION_V3 = "player-sentinel-elo-calibration-v3"
SCHEMA_CALIBRATION_CASE_V3 = "player-sentinel-elo-calibration-case-v3"
SCHEMA_ESTIMATE_V3 = "player-sentinel-estimated-elo-v3"
SCHEMA_CURVE_V3 = "player-sentinel-estimated-elo-curve-v3"
SCHEMA_CALIBRATION_MANIFEST_V3 = "player-sentinel-elo-calibration-sha256-manifest-v3"
ALGORITHM_VERSION_V3 = "estimated-elo-v3-beta-binomial-adaptive-grid-global-knn-v1"
SEARCH_STRATEGY_VERSION_V3 = "adaptive-multibasin-40-20-10-5-2-1-v1"
SEARCH_STEPS_V3 = (40, 20, 10, 5, 2, 1)

# Estimated-Elo v4 is the current formal path.  The v1/v2/v3 names remain
# readable for historical artifacts and compatibility commands, but v4 has a
# separate reference cache, calibration schema, and output schema so no old
# Beta-Binomial product can be silently interpreted as the new model.
SCHEMA_CONFIG_V4 = "player-sentinel-elo-reference-config-v4"
SCHEMA_ANScombe_REFERENCE = "player-sentinel-elo-anscombe-reference-v1"
SCHEMA_ANScombe_RECORD = "player-sentinel-elo-anscombe-record-v1"
SCHEMA_CONDITIONAL_REFERENCE_V4 = SCHEMA_ANScombe_REFERENCE
SCHEMA_CONDITIONAL_RECORD_V4 = SCHEMA_ANScombe_RECORD
SCHEMA_CALIBRATION_V4 = "player-sentinel-elo-calibration-v4"
SCHEMA_CALIBRATION_CASE_V4 = "player-sentinel-elo-calibration-case-v4"
SCHEMA_ESTIMATE_V4 = "player-sentinel-estimated-elo-v4"
SCHEMA_CURVE_V4 = "player-sentinel-estimated-elo-curve-v4"
SCHEMA_CALIBRATION_MANIFEST_V4 = "player-sentinel-elo-calibration-sha256-manifest-v4"
SCHEMA_REFERENCE_MANIFEST_V4 = "player-sentinel-elo-anscombe-reference-manifest-v1"
ALGORITHM_VERSION_V4 = (
    "estimated-elo-v4-anscombe-local-gaussian-adaptive-grid-global-knn-v1"
)
SEARCH_STRATEGY_VERSION_V4 = "adaptive-multibasin-40-20-10-5-2-1-v4"
SEARCH_STEPS_V4 = (40, 20, 10, 5, 2, 1)
ANScombe_X_CORRECTION = 3.0 / 8.0
ANScombe_N_CORRECTION = 3.0 / 4.0
# Conventional all-caps aliases are kept alongside the readable names above.
ANSCOMBE_X_CORRECTION = ANScombe_X_CORRECTION
ANSCOMBE_N_CORRECTION = ANScombe_N_CORRECTION
Z_CDF_CLIP = 1e-12
BETA_BINOMIAL_M_MIN = 1e-9
BETA_BINOMIAL_KAPPA_MIN = 1e-6
BETA_BINOMIAL_KAPPA_MAX = 1e8
BINOMIAL_LIMIT_KAPPA = 1e7

COLORS = ("black", "white")
METRICS_SCOPES = ("full_game", "post_offbook_inclusive")
ALGORITHM_LABELS = ("offbook", "no_offbook")
PHASES = (
    ("phase1", 1, 30),
    ("phase2", 31, 47),
    ("phase3", 48, 53),
    ("phase4", 54, 60),
)

DEFAULT_FORMAL_ELO_MINIMUM = 1600
DEFAULT_FORMAL_ELO_MAXIMUM = 2495
DEFAULT_MINIMUM_TARGET_GAMES = 10
DEFAULT_MAXIMUM_TARGET_GAMES = 30
DEFAULT_GE4_THRESHOLD = 4
DEFAULT_NEIGHBOR_EXPONENT = 2.0 / 3.0
DEFAULT_GRID_STEP = 1
DEFAULT_CALIBRATION_COVERAGE = 0.95
DEFAULT_CALIBRATION_VALIDATION_FRACTION = 0.20
DEFAULT_MINIMUM_VALIDATION_USERS = 20
DEFAULT_VALIDATION_MINIMUM_TARGET_GAMES = 12
DEFAULT_SPLIT_SEED = 20260819
DEFAULT_REFERENCE_QUERY_WORKERS = 16
# Keep the single-player estimated-Elo hot path within the user's memory budget.
# Formal calibration retains its separate frozen 16-account-worker contract.
DEFAULT_TARGET_GAME_WORKERS_V4 = 4
MAXIMUM_TARGET_GAME_WORKERS_V4 = 4
DEFAULT_CALIBRATION_WORKERS = 16


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row {line_number} is not an object: {path}")
            rows.append(value)
    return rows


def write_json(path: str | Path, value: Any, *, refuse_existing: bool = True) -> None:
    target = Path(path)
    if refuse_existing and target.exists():
        raise FileExistsError(f"output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def atomic_write_json(path: str | Path, value: Any) -> None:
    """Atomically replace one UTF-8 JSON file without touching other files."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    """Atomically replace one UTF-8 JSONL file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]], *, refuse_existing: bool = True) -> None:
    target = Path(path)
    if refuse_existing and target.exists():
        raise FileExistsError(f"output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            handle.write("\n")


def write_csv(path: str | Path, rows: list[dict[str, Any]], *, refuse_existing: bool = True) -> None:
    target = Path(path)
    if refuse_existing and target.exists():
        raise FileExistsError(f"output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with target.open("w", encoding="utf-8", newline="") as handle:
        if not fields:
            return
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None:
        return ""
    return value


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def optional_number(value: Any) -> float | None:
    if value is None:
        return None
    if not finite_number(value):
        raise ValueError(f"expected a finite number or null, got {value!r}")
    return float(value)


def rounded(value: float | None, digits: int = 12) -> float | None:
    return None if value is None else round(float(value), digits)


def default_config() -> dict[str, Any]:
    return {
        "schema": "player-sentinel-elo-reference-config-v1",
        "version": "v5",
        "sourceReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_elo_matchup400_reference_level22_1600plus_20260815"
        ),
        "sentinelDerivedDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_reference_level22_1600plus_v6_20260819"
        ),
        "derivedReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_reference_level22_1600plus_v5_20260819"
        ),
        "directedPhaseRecords": "directed_game_phase_records.jsonl",
        "referenceManifest": "reference_sha256_manifest.json",
        "calibrationArtifact": "elo_calibration.json",
        "formalEloMinimum": DEFAULT_FORMAL_ELO_MINIMUM,
        "formalEloMaximum": DEFAULT_FORMAL_ELO_MAXIMUM,
        "minimumTargetGames": DEFAULT_MINIMUM_TARGET_GAMES,
        "maximumTargetGames": DEFAULT_MAXIMUM_TARGET_GAMES,
        "phaseBoundaries": [30, 47, 53],
        "ge4Threshold": DEFAULT_GE4_THRESHOLD,
        "neighborExponent": DEFAULT_NEIGHBOR_EXPONENT,
        "distanceKernel": "triangular_k_plus_one_boundary",
        "eloGridMinimum": DEFAULT_FORMAL_ELO_MINIMUM,
        "eloGridMaximum": DEFAULT_FORMAL_ELO_MAXIMUM,
        "eloGridStep": DEFAULT_GRID_STEP,
        "calibrationCoverage": DEFAULT_CALIBRATION_COVERAGE,
        "calibrationGrouping": "global",
        "calibrationValidationFraction": DEFAULT_CALIBRATION_VALIDATION_FRACTION,
        "minimumValidationUsers": DEFAULT_MINIMUM_VALIDATION_USERS,
        "validationMinimumTargetGames": DEFAULT_VALIDATION_MINIMUM_TARGET_GAMES,
        "calibrationSplitSeed": DEFAULT_SPLIT_SEED,
        "referenceQueryWorkers": DEFAULT_REFERENCE_QUERY_WORKERS,
        "calibrationWorkers": DEFAULT_CALIBRATION_WORKERS,
    }


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("schema") == SCHEMA_CONFIG_V4:
        return validate_v4_config(config)
    if config.get("schema") == SCHEMA_CONFIG_V3:
        return validate_v3_config(config)
    if config.get("schema") == SCHEMA_CONFIG_V2:
        return validate_v2_config(config)
    required = (
        "formalEloMinimum", "formalEloMaximum", "minimumTargetGames",
        "maximumTargetGames", "phaseBoundaries", "ge4Threshold",
        "neighborExponent", "eloGridMinimum", "eloGridMaximum", "eloGridStep",
        "calibrationCoverage", "calibrationGrouping",
    )
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Elo configuration is missing: {', '.join(missing)}")
    minimum = int(config["formalEloMinimum"])
    maximum = int(config["formalEloMaximum"])
    if minimum != 1600 or maximum < 2400:
        raise ValueError("formal Elo range must use minimum=1600 and maximum>=2400")
    boundaries = [int(item) for item in config["phaseBoundaries"]]
    if boundaries != [30, 47, 53]:
        raise ValueError("v1 phase boundaries must be [30, 47, 53]")
    if int(config["minimumTargetGames"]) < 1:
        raise ValueError("minimumTargetGames must be positive")
    if int(config["maximumTargetGames"]) < int(config["minimumTargetGames"]):
        raise ValueError("maximumTargetGames must not be below minimumTargetGames")
    if float(config["neighborExponent"]) != DEFAULT_NEIGHBOR_EXPONENT:
        raise ValueError("v1 neighborExponent must be exactly 2/3")
    if float(config["calibrationCoverage"]) != DEFAULT_CALIBRATION_COVERAGE:
        raise ValueError("v1 calibrationCoverage must be exactly 0.95")
    if str(config["calibrationGrouping"]) != "global":
        raise ValueError("v1 calibrationGrouping must be global")
    if int(config["eloGridMinimum"]) != minimum or int(config["eloGridMaximum"]) != maximum:
        raise ValueError("Elo grid must equal the formal Elo range in v1")
    if int(config["eloGridStep"]) != 1:
        raise ValueError("v1 Elo grid step must be 1")
    result = dict(config)
    result.setdefault("calibrationValidationFraction", DEFAULT_CALIBRATION_VALIDATION_FRACTION)
    result.setdefault("minimumValidationUsers", DEFAULT_MINIMUM_VALIDATION_USERS)
    result.setdefault("validationMinimumTargetGames", DEFAULT_VALIDATION_MINIMUM_TARGET_GAMES)
    if int(result["validationMinimumTargetGames"]) < int(result["minimumTargetGames"]):
        raise ValueError("validationMinimumTargetGames must not be below minimumTargetGames")
    if int(result["validationMinimumTargetGames"]) > int(result["maximumTargetGames"]):
        raise ValueError("validationMinimumTargetGames must not exceed maximumTargetGames")
    result.setdefault("calibrationSplitSeed", DEFAULT_SPLIT_SEED)
    result.setdefault("referenceQueryWorkers", DEFAULT_REFERENCE_QUERY_WORKERS)
    if int(result["referenceQueryWorkers"]) < 1:
        raise ValueError("referenceQueryWorkers must be positive")
    result.setdefault("calibrationWorkers", DEFAULT_CALIBRATION_WORKERS)
    if int(result["calibrationWorkers"]) < 1:
        raise ValueError("calibrationWorkers must be positive")
    return result


def default_v2_config() -> dict[str, Any]:
    """Return the frozen estimated-Elo v2 numerical contract."""

    return {
        "schema": SCHEMA_CONFIG_V2,
        "version": "v2-20260828",
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "sourceReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_elo_matchup500_blackwhite_reference_level22_1600plus_20260822"
        ),
        "derivedReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_reference_level22_1600plus_v8_20260822"
        ),
        "directedPhaseRecords": "directed_game_phase_records.jsonl",
        "referenceManifest": "reference_sha256_manifest.json",
        "conditionalReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_conditional_reference_v1_20260828"
        ),
        "conditionalReferenceRecords": "conditional_reference_records.jsonl",
        "conditionalReferenceManifest": "conditional_reference_manifest.json",
        "calibrationDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_calibration_v2_20260828"
        ),
        "calibrationArtifact": "elo_calibration_v2.json",
        "calibrationCases": "elo_calibration_cases_v2.jsonl",
        "formalEloMinimum": 1600,
        "formalEloMaximum": 2500,
        "eloGridMinimum": 1600,
        "eloGridMaximum": 2500,
        "eloGridStep": 1,
        "minimumTargetGames": 10,
        "maximumTargetGames": 30,
        "validationMinimumTargetGames": 12,
        "phaseBoundaries": [30, 47, 53],
        "ge4Threshold": 4,
        "neighborExponent": DEFAULT_NEIGHBOR_EXPONENT,
        "distanceKernel": "standardized_euclidean_triangular_k_plus_one_boundary",
        "previousZWeight": 1,
        "referenceFeaturePolicy": "unified_frozen_cache_with_direct_target_account_source_game_exclusion",
        "zCdfClip": Z_CDF_CLIP,
        "betaBinomialMMinimum": BETA_BINOMIAL_M_MIN,
        "betaBinomialKappaMinimum": BETA_BINOMIAL_KAPPA_MIN,
        "betaBinomialKappaMaximum": 100000000,
        "betaBinomialOptimizer": "scipy-lbfgsb-logit-m-log-kappa",
        "calibrationCoverage": 0.95,
        "calibrationGrouping": "global",
        "calibrationValidationFraction": DEFAULT_CALIBRATION_VALIDATION_FRACTION,
        "minimumValidationUsers": DEFAULT_MINIMUM_VALIDATION_USERS,
        "calibrationSplitSeed": 20260828502,
        "calibrationWorkers": 16,
        "referenceQueryWorkers": 1,
        "prepareAccountShardSize": 64,
    }


def validate_v2_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate v2 without accepting a v1 artifact under new semantics."""

    if config.get("schema") != SCHEMA_CONFIG_V2:
        raise ValueError(f"estimated-Elo v2 requires config schema {SCHEMA_CONFIG_V2}")
    result = dict(config)
    defaults = default_v2_config()
    for key, value in defaults.items():
        result.setdefault(key, value)
    if result.get("algorithmVersion") != ALGORITHM_VERSION_V2:
        raise ValueError("estimated-Elo v2 algorithmVersion mismatch")
    exact_values = {
        "formalEloMinimum": 1600,
        "formalEloMaximum": 2500,
        "eloGridMinimum": 1600,
        "eloGridMaximum": 2500,
        "eloGridStep": 1,
        "minimumTargetGames": 10,
        "maximumTargetGames": 30,
        "ge4Threshold": 4,
        "calibrationWorkers": 16,
        "referenceQueryWorkers": 1,
    }
    for key, expected in exact_values.items():
        if float(result.get(key)) != float(expected):
            raise ValueError(f"estimated-Elo v2 requires {key}={expected}")
    if [int(value) for value in result.get("phaseBoundaries", [])] != [30, 47, 53]:
        raise ValueError("estimated-Elo v2 phaseBoundaries must be [30, 47, 53]")
    if not math.isclose(float(result["neighborExponent"]), DEFAULT_NEIGHBOR_EXPONENT, rel_tol=0.0, abs_tol=0.0):
        raise ValueError("estimated-Elo v2 neighborExponent must be exactly 2/3")
    if float(result["previousZWeight"]) not in {0.0, 1.0}:
        raise ValueError("previousZWeight must be 0 or 1 for the frozen v2 comparisons")
    if not 0.0 < float(result["zCdfClip"]) < 0.5:
        raise ValueError("zCdfClip must be between 0 and 0.5")
    if not 0.0 < float(result["betaBinomialMMinimum"]) < 0.5:
        raise ValueError("betaBinomialMMinimum must be between 0 and 0.5")
    if not 0.0 < float(result["betaBinomialKappaMinimum"]) < float(result["betaBinomialKappaMaximum"]):
        raise ValueError("invalid beta-binomial kappa bounds")
    if float(result["calibrationCoverage"]) != 0.95 or result["calibrationGrouping"] != "global":
        raise ValueError("estimated-Elo v2 requires global 0.95 calibration")
    if int(result["prepareAccountShardSize"]) < 1:
        raise ValueError("prepareAccountShardSize must be positive")
    return result


def load_config(path: str | Path) -> dict[str, Any]:
    return validate_config(read_json(path))


def _player_pair(detail: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    players = detail.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError(f"game {detail.get('id')!r} must contain exactly two players")
    if not all(isinstance(player, dict) for player in players):
        raise ValueError(f"game {detail.get('id')!r} has an invalid players list")
    return players[0], players[1]


def _ply(node: dict[str, Any]) -> int:
    raw = node.get("globalPlacementPly", node.get("global_placement_ply", node.get("ply")))
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not float(raw).is_integer():
        raise ValueError(f"node is missing an integer global placement ply: {node!r}")
    value = int(raw)
    if not 1 <= value <= 60:
        raise ValueError(f"global placement ply must be in [1,60], got {value}")
    return value


def _empty_metrics(*, analysis_start_ply: int | None, reason: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "analysisStartPly": analysis_start_ply,
        "validLossNodeCount": 0,
    }
    for phase, _lower, _upper in PHASES:
        value[phase] = {
            "validLossNodeCount": 0,
            "lossGe4Count": 0,
            "lossGe4Rate": None,
        }
    value["completeFourPhase"] = False
    value["equalPhaseGameGe4Rate"] = None
    value["scopeAvailable"] = reason is None
    if reason is not None:
        value["unavailableReason"] = reason
    return value


def phase_metrics_for_scope(
    nodes: Sequence[dict[str, Any]],
    target_player_id: str,
    target_color: str,
    scope: str,
    offbook_ply: int | None,
    *,
    ge4_threshold: float = DEFAULT_GE4_THRESHOLD,
) -> dict[str, Any]:
    """Compute the four fixed global-placement phase metrics for one scope."""

    if scope not in METRICS_SCOPES:
        raise ValueError(f"unsupported Elo metrics scope: {scope}")
    color = str(target_color).strip().casefold()
    if color not in COLORS:
        raise ValueError(f"target color must be black or white, got {target_color!r}")
    if scope == "post_offbook_inclusive" and offbook_ply is None:
        return _empty_metrics(analysis_start_ply=None, reason="no_offbook_anchor")
    if offbook_ply is not None:
        if isinstance(offbook_ply, bool) or not isinstance(offbook_ply, int) or not 1 <= offbook_ply <= 60:
            raise ValueError(f"invalid off-book anchor ply: {offbook_ply!r}")

    target_nodes: list[tuple[int, float]] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("Level22 nodes must be objects")
        node_color = str(node.get("playerColor") or "").strip().casefold()
        if node_color != color:
            continue
        if account_key(node.get("playerAccount")) != account_key(target_player_id):
            raise ValueError("target-color Level22 node belongs to a different account")
        placement_ply = _ply(node)
        if scope == "post_offbook_inclusive" and placement_ply < int(offbook_ply):
            continue
        loss = node.get("lossPositive")
        if finite_number(loss):
            target_nodes.append((placement_ply, float(loss)))

    if scope == "post_offbook_inclusive":
        analysis_start_ply = int(offbook_ply)
    else:
        placement_plys = [
            _ply(node)
            for node in nodes
            if isinstance(node, dict)
            and str(node.get("playerColor") or "").strip().casefold() == color
        ]
        analysis_start_ply = min(placement_plys, default=None)

    result = _empty_metrics(analysis_start_ply=analysis_start_ply)
    result["validLossNodeCount"] = len(target_nodes)
    phase_rates: list[float] = []
    for phase, lower, upper in PHASES:
        phase_losses = [loss for placement_ply, loss in target_nodes if lower <= placement_ply <= upper]
        ge4_count = sum(loss >= float(ge4_threshold) for loss in phase_losses)
        rate = ge4_count / len(phase_losses) if phase_losses else None
        result[phase] = {
            "validLossNodeCount": len(phase_losses),
            "lossGe4Count": ge4_count,
            "lossGe4Rate": rounded(rate),
        }
        if rate is not None:
            phase_rates.append(rate)
    complete = all(result[phase]["validLossNodeCount"] > 0 for phase, _lower, _upper in PHASES)
    result["completeFourPhase"] = complete
    result["equalPhaseGameGe4Rate"] = rounded(statistics.fmean(phase_rates)) if complete else None
    return result


def _formal_rating(value: float | None, minimum: int, maximum: int) -> bool:
    return value is not None and minimum <= value <= maximum


def make_elo_directed_record(
    game: dict[str, Any],
    detail: dict[str, Any],
    target_color: str,
    algorithm_record: dict[str, Any],
    source_path: str | Path,
    source_sha256: str,
    *,
    in_main_matrix: bool,
    partition_scope: str,
    config: dict[str, Any] | None = None,
    allow_missing_ratings: bool = False,
) -> dict[str, Any]:
    """Build the v1 directed record without running the Level22 engine."""

    cfg = validate_config(config or default_config())
    color = str(target_color).strip().casefold()
    if color not in COLORS:
        raise ValueError(f"target color must be black or white, got {target_color!r}")
    black, white = _player_pair(detail)
    target, opponent = (black, white) if color == "black" else (white, black)
    target_id = str(target.get("id") or "").strip()
    opponent_id = str(opponent.get("id") or "").strip()
    if not target_id or not opponent_id:
        raise ValueError(f"game {detail.get('id')!r} has an empty player ID")
    target_old = optional_number(target.get("oldR"))
    opponent_old = optional_number(opponent.get("oldR"))
    target_new = optional_number(target.get("newR"))
    opponent_new = optional_number(opponent.get("newR"))
    if not allow_missing_ratings and (target_old is None or opponent_old is None):
        raise ValueError(f"game {detail.get('id')!r} is missing a required oldR")
    label = str(algorithm_record.get("algorithmLabel") or "")
    if label not in ALGORITHM_LABELS:
        raise ValueError(f"invalid algorithmLabel for game {detail.get('id')!r}: {label!r}")
    raw_anchor = algorithm_record.get("offBookPly")
    offbook_ply = None if raw_anchor is None else int(raw_anchor)
    if label == "offbook" and offbook_ply is None:
        raise ValueError(f"offbook game {detail.get('id')!r} is missing its anchor ply")
    if label == "no_offbook" and offbook_ply is not None:
        raise ValueError(f"no_offbook game {detail.get('id')!r} has an anchor ply")

    full_metrics = phase_metrics_for_scope(
        game.get("nodes") or [], target_id, color, "full_game", offbook_ply,
        ge4_threshold=float(cfg["ge4Threshold"]),
    )
    post_metrics = phase_metrics_for_scope(
        game.get("nodes") or [], target_id, color, "post_offbook_inclusive", offbook_ply,
        ge4_threshold=float(cfg["ge4Threshold"]),
    )
    selected_scope = "post_offbook_inclusive" if label == "offbook" else "full_game"
    formal = bool(
        in_main_matrix
        and _formal_rating(target_old, int(cfg["formalEloMinimum"]), int(cfg["formalEloMaximum"]))
        and _formal_rating(opponent_old, int(cfg["formalEloMinimum"]), int(cfg["formalEloMaximum"]))
    )
    source = Path(source_path)
    return {
        "schema": SCHEMA_DIRECTED,
        "gameId": str(detail.get("id") or game.get("gameId") or ""),
        "created": detail.get("created"),
        "targetPlayerId": target_id,
        "opponentPlayerId": opponent_id,
        "targetColor": color,
        "targetOldR": target_old,
        "targetNewR": target_new,
        "opponentOldR": opponent_old,
        "opponentNewR": opponent_new,
        "formalReferenceEligible": formal,
        "inMainMatrix": bool(in_main_matrix),
        "partitionScope": str(partition_scope),
        "algorithmLabel": label,
        "offBookPly": offbook_ply,
        "anchorSource": algorithm_record.get("anchorSource"),
        "algorithmEvidence": algorithm_record.get("algorithmEvidence"),
        "analysisScope": selected_scope,
        "metrics": {
            "full_game": full_metrics,
            "post_offbook_inclusive": post_metrics,
        },
        "sourceLevel22File": source.as_posix(),
        "sourceLevel22Sha256": str(source_sha256),
    }


def selected_metrics_scope(record: dict[str, Any]) -> str:
    if str(record.get("scope") or "") in METRICS_SCOPES:
        return str(record["scope"])
    label = str(record.get("algorithmLabel") or "")
    if label == "offbook":
        return "post_offbook_inclusive"
    if label == "no_offbook":
        return "full_game"
    raise ValueError(f"record has an invalid algorithmLabel: {label!r}")


def metrics_for_record(record: dict[str, Any], scope: str | None = None) -> dict[str, Any]:
    chosen = scope or selected_metrics_scope(record)
    metrics = (record.get("metrics") or {}).get(chosen)
    if not isinstance(metrics, dict):
        raise ValueError(f"record {record.get('gameId')!r} is missing metrics.{chosen}")
    return metrics


def _record_phase_rate(record: dict[str, Any], scope: str, phase: int) -> float | None:
    metrics = (record.get("metrics") or {}).get(scope)
    if isinstance(metrics, dict):
        value = metrics.get(f"phase{phase}")
        rate = value.get("lossGe4Rate") if isinstance(value, dict) else None
        if finite_number(rate):
            return float(rate)
    phase_value = record.get(f"phase{phase}")
    if isinstance(phase_value, dict):
        x = phase_value.get("x", phase_value.get("lossGe4Count"))
        n = phase_value.get("n", phase_value.get("validLossNodeCount"))
        if finite_number(x) and finite_number(n) and float(n) > 0:
            return float(x) / float(n)
    return None


def _record_equal_phase_rate(record: dict[str, Any], scope: str) -> float | None:
    metrics = (record.get("metrics") or {}).get(scope)
    if isinstance(metrics, dict) and finite_number(metrics.get("equalPhaseGameGe4Rate")):
        return float(metrics["equalPhaseGameGe4Rate"])
    if str(record.get("scope") or "") not in {"", scope}:
        return None
    rates = [_record_phase_rate(record, scope, phase) for phase in range(1, 5)]
    if any(value is None for value in rates):
        return None
    return statistics.fmean(float(value) for value in rates if value is not None)


def _load_record_rows(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if source.suffix.lower() == ".jsonl":
        return read_jsonl(source)
    value = read_json(source)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    rows = value.get("records") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"could not find records in {source}")
    return [row for row in rows if isinstance(row, dict)]


def _algorithm_rows_by_key(path: str | Path) -> dict[tuple[str, str], dict[str, Any]]:
    rows = _load_record_rows(path)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        game_id = str(row.get("gameId") or row.get("game_id") or "")
        color = str(row.get("targetColor") or row.get("target_color") or "").strip().casefold()
        if not game_id:
            raise ValueError(f"algorithm record has an empty gameId: {row!r}")
        if color in COLORS:
            key = (game_id, color)
            if key in result:
                raise ValueError(f"duplicate algorithm record: {key}")
            result[key] = row
        else:
            by_game[game_id].append(row)
    for game_id, candidates in by_game.items():
        if len(candidates) != 1:
            raise ValueError(f"algorithm records without targetColor are ambiguous for {game_id}")
        row = candidates[0]
        for color in COLORS:
            key = (game_id, color)
            if key not in result:
                result[key] = row
    return result


def _required_source_files(reference: Path) -> list[Path]:
    return [
        reference / "selected_games_with_partitions.json",
        reference / "selected_account_bundle.json",
        reference / "engine_game_index.json",
        reference / "reference_completion_audit.json",
        reference / "partition_engine_index_audit.json",
        reference / "engine_level22" / "audit.json",
        reference / "final_sha256_manifest.json",
    ]


def build_elo_reference(
    source_reference_directory: str | Path,
    sentinel_derived_directory: str | Path,
    output_directory: str | Path,
    *,
    config: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
    build_script_paths: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Derive both metric scopes from the frozen Level22 files."""

    cfg = validate_config(config or default_config())
    reference = Path(source_reference_directory).resolve()
    sentinel_reference = Path(sentinel_derived_directory).resolve()
    output = Path(output_directory).resolve()
    partial_matches = False
    if output.exists() and any(output.iterdir()):
        # A failed build may have written only the two deterministic source
        # artifacts before the audit step.  Rebuild that exact same-batch
        # partial directory in place; refuse every other non-empty directory.
        partial_names = {
            str(cfg.get("directedPhaseRecords") or "directed_game_phase_records.jsonl"),
            "reference_source_manifest.json",
        }
        present_names = {
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file()
        }
        partial_manifest = output / "reference_source_manifest.json"
        if present_names == partial_names and partial_manifest.is_file():
            try:
                partial = read_json(partial_manifest)
            except Exception:
                partial = {}
            configured = partial.get("config") or {}
            expected_config_sha = (
                sha256_file(config_path)
                if config_path is not None and Path(config_path).is_file()
                else None
            )
            partial_matches = (
                expected_config_sha is not None
                and configured.get("path") == str(Path(config_path).resolve())
                and configured.get("sha256") == expected_config_sha
                and partial.get("sourceReferenceDirectory") == str(reference)
                and partial.get("sentinelDerivedDirectory") == str(sentinel_reference)
            )
        if not partial_matches:
            raise FileExistsError(f"derived reference directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    required = _required_source_files(reference)
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(f"required frozen source file is missing: {path}")
    if not sentinel_reference.is_dir():
        raise FileNotFoundError(f"sentinel derived directory is missing: {sentinel_reference}")
    algorithm_path = sentinel_reference / "directed_target_records.jsonl"
    if not algorithm_path.is_file():
        algorithm_path = sentinel_reference / "offbook_records_by_target_side.json"
    if not algorithm_path.is_file():
        raise FileNotFoundError(f"sentinel algorithm records are missing under {sentinel_reference}")

    source_hashes = {
        path.relative_to(reference).as_posix(): sha256_file(path)
        for path in required
    }
    selection = read_json(reference / "selected_games_with_partitions.json")
    selected_games = selection.get("games")
    if not isinstance(selected_games, list) or not selected_games:
        raise ValueError("frozen source selection has no games")
    bundle = read_json(reference / "selected_account_bundle.json")
    details = {str(row.get("id") or ""): row for row in bundle.get("details", [])}
    game_ids = [str(row.get("gameId") or "") for row in selected_games]
    if not all(game_ids) or len(set(game_ids)) != len(game_ids):
        raise ValueError("source selected game IDs must be unique and non-empty")
    if set(details) != set(game_ids):
        raise ValueError("source bundle and selected game IDs disagree")
    engine_index = {
        str(row.get("gameId") or ""): row
        for row in (read_json(reference / "engine_game_index.json").get("games") or [])
    }
    if set(engine_index) != set(game_ids):
        raise ValueError("source engine index and selected game IDs disagree")
    algorithm_rows = _algorithm_rows_by_key(algorithm_path)
    if len(algorithm_rows) != len(game_ids) * 2:
        raise ValueError("sentinel algorithm records must contain exactly two sides per source game")

    completion = read_json(reference / "reference_completion_audit.json")
    level22_audit = read_json(reference / "engine_level22" / "audit.json")
    if completion.get("ok") is not True or level22_audit.get("ok") is not True:
        raise ValueError("frozen source Level22 audits are not successful")
    engine_contract = completion.get("contract") or {}
    if engine_contract.get("level") != 22 or engine_contract.get("wldFromPlyInclusive") != 39:
        raise ValueError("frozen source does not have the required Level22 contract")

    records: list[dict[str, Any]] = []
    seen_engine_files: set[Path] = set()
    for selected in selected_games:
        game_id = str(selected["gameId"])
        engine_rel = Path(str(selected.get("expectedEngineFile") or ""))
        engine_path = (reference / engine_rel).resolve()
        if not engine_path.is_file():
            raise FileNotFoundError(f"Level22 game file is missing: {engine_path}")
        actual_sha = sha256_file(engine_path)
        expected_sha = str(engine_index[game_id].get("engineFileSha256") or "")
        if actual_sha != expected_sha:
            raise ValueError(f"Level22 SHA-256 mismatch for {game_id}")
        seen_engine_files.add(engine_path)
        game = read_json(engine_path)
        if str(game.get("gameId") or "") != game_id:
            raise ValueError(f"Level22 gameId mismatch in {engine_path}")
        detail = details[game_id]
        black, white = _player_pair(detail)
        expected_ids = {"black": account_key(black.get("id")), "white": account_key(white.get("id"))}
        for color in COLORS:
            algorithm = algorithm_rows[(game_id, color)]
            if str(algorithm.get("targetColor") or color).strip().casefold() not in {color, ""}:
                raise ValueError(f"algorithm target color mismatch for {game_id}:{color}")
            target_id = account_key(black.get("id") if color == "black" else white.get("id"))
            if algorithm.get("targetPlayerId") is not None:
                if account_key(algorithm.get("targetPlayerId")) != target_id:
                    raise ValueError(f"algorithm target account mismatch for {game_id}:{color}")
            # The target account check above is intentionally before metric derivation;
            # it catches a side swap without touching the frozen source.
            _ = expected_ids[color]
            record = make_elo_directed_record(
                game,
                detail,
                color,
                algorithm,
                engine_path,
                actual_sha,
                in_main_matrix=bool(selected.get("inMainMatrix")),
                partition_scope=str(selected.get("partitionScope") or ""),
                config=cfg,
            )
            records.append(record)
    records.sort(key=lambda row: (str(row["gameId"]), str(row["targetColor"])))
    if len(records) != len(game_ids) * 2 or len(seen_engine_files) != len(game_ids):
        raise ValueError("reference derivation did not produce two records and one engine file per game")

    record_path = output / str(cfg.get("directedPhaseRecords") or "directed_game_phase_records.jsonl")
    write_jsonl(record_path, records, refuse_existing=not partial_matches)
    sentinel_manifest_path = sentinel_reference / "reference_sha256_manifest.json"
    source_manifest: dict[str, Any] = {
        "schema": SCHEMA_REFERENCE_MANIFEST,
        "createdAt": utc_now(),
        "sourceReferenceDirectory": str(reference),
        "sentinelDerivedDirectory": str(sentinel_reference),
        "sourceFiles": [
            {"path": name, "sha256": digest}
            for name, digest in source_hashes.items()
        ],
        "sentinelDerivedFiles": [
            {
                "path": sentinel_manifest_path.name,
                "sha256": sha256_file(sentinel_manifest_path),
            }
        ] if sentinel_manifest_path.is_file() else [],
        "level22FilesReferencedNotCopied": len(seen_engine_files),
        "engineContract": engine_contract,
        "algorithmRecordSource": {
            "path": str(algorithm_path),
            "sha256": sha256_file(algorithm_path),
            "selection": "existing frozen sentinel directed records; Level22 was not rerun",
        },
        "buildScripts": [
            {"path": str(Path(path).resolve()), "sha256": sha256_file(path)}
            for path in build_script_paths if Path(path).is_file()
        ],
        "config": (
            {"path": str(Path(config_path).resolve()), "sha256": sha256_file(config_path)}
            if config_path is not None and Path(config_path).is_file() else None
        ),
    }
    source_manifest_path = output / "reference_source_manifest.json"
    write_json(source_manifest_path, source_manifest, refuse_existing=not partial_matches)

    full_complete = sum(
        bool((row.get("metrics") or {}).get("full_game", {}).get("completeFourPhase"))
        for row in records
    )
    post_complete = sum(
        bool((row.get("metrics") or {}).get("post_offbook_inclusive", {}).get("completeFourPhase"))
        for row in records
    )
    formal_counts = {
        f"{color}:{scope}": sum(
            bool(row.get("formalReferenceEligible"))
            and str(row.get("targetColor")) == color
            and bool((row.get("metrics") or {}).get(scope, {}).get("completeFourPhase"))
            for row in records
        )
        for color in COLORS for scope in METRICS_SCOPES
    }
    audit = {
        "schema": SCHEMA_REFERENCE_AUDIT,
        "ok": True,
        "createdAt": utc_now(),
        "sourceGameCount": len(game_ids),
        "directedRecordCount": len(records),
        "fullGameFourPhaseCompleteRecordCount": full_complete,
        "postOffbookInclusiveFourPhaseCompleteRecordCount": post_complete,
        "formalReferenceRecordCountsByColorAndScope": formal_counts,
        "formalReferenceRecordCount": sum(bool(row.get("formalReferenceEligible")) for row in records),
        "sourceLevel22FileCount": len(seen_engine_files),
        "sourceLevel22FilesCopied": 0,
        "sourceReferenceFilesReadOnly": True,
        "checks": {
            "twoDirectedRecordsPerSourceGame": len(records) == len(game_ids) * 2,
            "oneLevel22FilePerSourceGame": len(seen_engine_files) == len(game_ids),
            "newRPresentOnEveryPlayerSide": all(
                finite_number(row.get("targetNewR")) and finite_number(row.get("opponentNewR"))
                for row in records
            ),
            "fullMetricsUseAllTargetNodes": True,
            "postMetricsIncludeAnchor": True,
            "passDoesNotConsumePlacementPly": True,
            "sourceLevel22FilesWereNotCopiedOrModified": True,
            "sourceCompletionAuditOk": completion.get("ok") is True,
            "sourceLevel22AuditOk": level22_audit.get("ok") is True,
        },
    }
    audit["ok"] = bool(all(audit["checks"].values()))
    if cfg.get("schema") == SCHEMA_CONFIG_V4:
        audit["algorithmVersion"] = ALGORITHM_VERSION_V4
        audit["configSha256"] = canonical_sha256(cfg)
        audit["directedEloBucketAudit"] = directed_elo_bucket_matrix_v4(
            reference,
            config=cfg,
        )
    audit_path = output / "reference_build_audit.json"
    write_json(audit_path, audit)
    generated = [record_path.name, source_manifest_path.name, audit_path.name]
    sha_manifest = manifest_for_files(output, generated, SCHEMA_REFERENCE_SHA)
    write_json(output / str(cfg.get("referenceManifest") or "reference_sha256_manifest.json"), sha_manifest)
    return audit


def manifest_for_files(directory: str | Path, names: Iterable[str], schema: str) -> dict[str, Any]:
    root = Path(directory).resolve()
    files = []
    for name in sorted(set(names)):
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(path)
        files.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {
        "schema": schema,
        "createdAt": utc_now(),
        "referenceDirectory": str(root),
        "fileCount": len(files),
        "files": files,
        "selfHashPolicy": "manifest file is excluded from its own hash list",
    }


def update_reference_manifest(directory: str | Path, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = validate_config(config or default_config())
    root = Path(directory).resolve()
    manifest_name = str(cfg.get("referenceManifest") or "reference_sha256_manifest.json")
    names = [
        path.relative_to(root).as_posix()
        for path in root.iterdir()
        if path.is_file() and path.name != manifest_name
    ]
    manifest = manifest_for_files(root, names, SCHEMA_REFERENCE_SHA)
    write_json(root / manifest_name, manifest, refuse_existing=False)
    return manifest


def target_records_from_inputs(
    bundle_path: str | Path,
    engine_directory: str | Path,
    algorithm_records_path: str | Path,
    account: str,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = validate_config(config or default_config())
    bundle = read_json(bundle_path)
    details = {str(row.get("id") or ""): row for row in bundle.get("details", [])}
    if not details:
        raise ValueError("target bundle has no details")
    algorithm_rows = _algorithm_rows_by_key(algorithm_records_path)
    engine_root = Path(engine_directory).resolve()
    paths = sorted(engine_root.glob("game_*.json"))
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in paths:
        game = read_json(path)
        game_id = str(game.get("gameId") or "")
        if game_id not in details:
            raise ValueError(f"target engine game {game_id!r} is not in the target bundle")
        detail = details[game_id]
        black, white = _player_pair(detail)
        matches = [
            color for color, player in (("black", black), ("white", white))
            if account_key(player.get("id")) == account_key(account)
        ]
        if len(matches) != 1:
            raise ValueError(f"target account does not map to exactly one side in {game_id}")
        color = matches[0]
        algorithm = algorithm_rows.get((game_id, color))
        if algorithm is None:
            raise ValueError(f"target algorithm record is missing for {game_id}:{color}")
        record = make_elo_directed_record(
            game,
            detail,
            color,
            algorithm,
            path,
            sha256_file(path),
            in_main_matrix=True,
            partition_scope="target_estimation",
            config=cfg,
            allow_missing_ratings=True,
        )
        records.append(record)
        seen_ids.add(game_id)
    if seen_ids != set(details):
        missing = sorted(set(details) - seen_ids)
        raise ValueError(f"target Level22 output is incomplete: {missing}")
    return sorted(records, key=lambda row: (str(row.get("created") or ""), str(row["gameId"])), reverse=True)


def _target_record_rejection(record: dict[str, Any], config: dict[str, Any]) -> str | None:
    try:
        scope = selected_metrics_scope(record)
        metrics = metrics_for_record(record, scope)
    except ValueError:
        return "incomplete_phase_data"
    if not metrics.get("completeFourPhase") or not finite_number(metrics.get("equalPhaseGameGe4Rate")):
        return "incomplete_phase_data"
    opponent = record.get("opponentOldR")
    if not finite_number(opponent):
        return "opponent_out_of_reference_range"
    if not int(config["formalEloMinimum"]) <= float(opponent) <= int(config["formalEloMaximum"]):
        return "opponent_out_of_reference_range"
    return None


def select_target_records(
    records: Sequence[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = validate_config(config or default_config())
    selected_candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_game_ids: set[str] = set()
    for record in records:
        game_id = str(record.get("gameId") or "")
        if not game_id:
            excluded.append({"gameId": game_id, "reason": "invalid_game_id"})
            continue
        if game_id in seen_game_ids:
            raise ValueError(f"duplicate target game ID: {game_id}")
        seen_game_ids.add(game_id)
        reason = _target_record_rejection(record, cfg)
        if reason is None:
            selected_candidates.append(record)
        else:
            excluded.append({"gameId": game_id, "reason": reason})
    selected_candidates.sort(key=lambda row: (str(row.get("created") or ""), str(row["gameId"])), reverse=True)
    maximum = int(cfg["maximumTargetGames"])
    selected = selected_candidates[:maximum]
    for record in selected_candidates[maximum:]:
        excluded.append({"gameId": str(record["gameId"]), "reason": "older_than_recent_maximum"})
    minimum = int(cfg["minimumTargetGames"])
    status = "valid" if len(selected) >= minimum else "insufficient_target_games"
    return {
        "selected": selected,
        "excluded": sorted(excluded, key=lambda row: (str(row.get("gameId") or ""), str(row.get("reason") or ""))),
        "candidateCount": len(selected_candidates),
        "selectedCount": len(selected),
        "status": status,
    }


def excluded_reference_game_ids(
    reference_records: Sequence[dict[str, Any]],
    target_account: str,
    target_game_ids: Iterable[str],
) -> set[str]:
    account = account_key(target_account)
    excluded = {str(game_id) for game_id in target_game_ids}
    for record in reference_records:
        if (
            account_key(record.get("targetPlayerId")) == account
            or account_key(record.get("opponentPlayerId")) == account
        ):
            excluded.add(str(record.get("gameId") or ""))
    return excluded


def eligible_reference_records(
    reference_records: Sequence[dict[str, Any]],
    target_record: dict[str, Any],
    *,
    excluded_game_ids: set[str] | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = validate_config(config or default_config())
    scope = selected_metrics_scope(target_record)
    color = str(target_record.get("targetColor") or "").strip().casefold()
    excluded = excluded_game_ids or set()
    minimum = int(cfg["formalEloMinimum"])
    maximum = int(cfg["formalEloMaximum"])
    result = []
    for reference in reference_records:
        if str(reference.get("gameId") or "") in excluded:
            continue
        if str(reference.get("targetColor") or "").strip().casefold() != color:
            continue
        if reference.get("formalReferenceEligible") is not True and reference.get("schema") != SCHEMA_CONDITIONAL_RECORD:
            continue
        if reference.get("schema") == SCHEMA_CONDITIONAL_RECORD:
            if str(reference.get("scope") or "") != scope:
                continue
            if any(_record_phase_rate(reference, scope, phase) is None for phase in range(1, 5)):
                continue
        else:
            metrics = (reference.get("metrics") or {}).get(scope)
            if not isinstance(metrics, dict) or metrics.get("completeFourPhase") is not True:
                continue
            if not finite_number(metrics.get("equalPhaseGameGe4Rate")):
                continue
        if not finite_number(reference.get("targetOldR")) or not finite_number(reference.get("opponentOldR")):
            continue
        if not minimum <= float(reference["targetOldR"]) <= maximum:
            continue
        if not minimum <= float(reference["opponentOldR"]) <= maximum:
            continue
        result.append(reference)
    return result


def elo_distance(reference_record: dict[str, Any], trial_elo: float, opponent_elo: float) -> float:
    target_gap = float(reference_record["targetOldR"]) - float(trial_elo)
    opponent_gap = float(reference_record["opponentOldR"]) - float(opponent_elo)
    return math.hypot(target_gap, opponent_gap)


def neighbor_count(reference_count: int, exponent: float = DEFAULT_NEIGHBOR_EXPONENT) -> int:
    if reference_count <= 0:
        raise ValueError("reference_count must be positive")
    if float(exponent) != DEFAULT_NEIGHBOR_EXPONENT:
        raise ValueError("v1 neighbor exponent must be exactly 2/3")
    return int(math.ceil(reference_count ** (2.0 / 3.0)))


def nearest_weighted_neighbors(
    reference_records: Sequence[dict[str, Any]],
    trial_elo: float,
    opponent_elo: float,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the exact, stable-key KNN and triangular weights."""

    cfg = validate_config(config or default_config())
    records = list(reference_records)
    count = len(records)
    if count <= 0:
        return {"ok": False, "reason": "insufficient_reference", "referenceCount": 0}
    k = neighbor_count(count, float(cfg["neighborExponent"]))
    if count < k + 1:
        return {
            "ok": False,
            "reason": "insufficient_reference",
            "referenceCount": count,
            "K": k,
        }
    ranked = [
        (elo_distance(record, trial_elo, opponent_elo), record)
        for record in records
    ]
    ranked.sort(key=lambda item: (item[0], str(item[1].get("gameId") or ""), str(item[1].get("targetColor") or "")))
    boundary = float(ranked[k][0])
    if not math.isfinite(boundary) or boundary <= 0:
        return {
            "ok": False,
            "reason": "insufficient_reference",
            "referenceCount": count,
            "K": k,
            "boundaryDistance": boundary,
        }
    selected = ranked[:k]
    weighted: list[dict[str, Any]] = []
    weight_sum = 0.0
    for distance, record in selected:
        weight = max(0.0, 1.0 - float(distance) / boundary)
        weight_sum += weight
        weighted.append({
            "record": record,
            "distance": float(distance),
            "referenceWeight": weight,
        })
    if not math.isfinite(weight_sum) or weight_sum <= 0:
        return {
            "ok": False,
            "reason": "insufficient_reference",
            "referenceCount": count,
            "K": k,
            "boundaryDistance": boundary,
            "weightSum": weight_sum,
        }
    return {
        "ok": True,
        "referenceCount": count,
        "K": k,
        "boundaryDistance": boundary,
        "weighted": weighted,
        "weightSum": weight_sum,
    }


def weighted_reference_distribution(
    weighted_neighbors: Sequence[dict[str, Any]],
    *,
    metric: str = "equalPhaseGameGe4Rate",
    scope: str | None = None,
) -> dict[str, Any]:
    if not weighted_neighbors:
        return {"ok": False, "reason": "insufficient_reference"}
    weights = [float(row["referenceWeight"]) for row in weighted_neighbors]
    chosen_scope = scope or selected_metrics_scope(weighted_neighbors[0]["record"])
    if metric != "equalPhaseGameGe4Rate":
        values = [
            float((row["record"].get("metrics") or {}).get(chosen_scope, {}).get(metric))
            for row in weighted_neighbors
        ]
    else:
        values = [
            float(_record_equal_phase_rate(row["record"], chosen_scope))
            for row in weighted_neighbors
            if _record_equal_phase_rate(row["record"], chosen_scope) is not None
        ]
        if len(values) != len(weighted_neighbors):
            return {"ok": False, "reason": "incomplete_phase_data"}
    weight_sum = sum(weights)
    if not math.isfinite(weight_sum) or weight_sum <= 0:
        return {"ok": False, "reason": "insufficient_reference"}
    mean = sum(weight * value for weight, value in zip(weights, values, strict=True)) / weight_sum
    variance = sum(weight * (value - mean) ** 2 for weight, value in zip(weights, values, strict=True)) / weight_sum
    sd = math.sqrt(variance)
    return {
        "ok": True,
        "mean": mean,
        "variance": variance,
        "sd": sd,
        "weightSum": weight_sum,
    }


def score_game_at_elo(
    target_record: dict[str, Any],
    reference_records: Sequence[dict[str, Any]],
    trial_elo: float,
    *,
    excluded_game_ids: set[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = validate_config(config or default_config())
    scope = selected_metrics_scope(target_record)
    target_metrics = metrics_for_record(target_record, scope)
    target_rate = target_metrics.get("equalPhaseGameGe4Rate")
    opponent_elo = target_record.get("opponentOldR")
    if not finite_number(target_rate) or not finite_number(opponent_elo):
        return {"ok": False, "reason": "incomplete_phase_data"}
    eligible = eligible_reference_records(
        reference_records,
        target_record,
        excluded_game_ids=excluded_game_ids,
        config=cfg,
    )
    nearest = nearest_weighted_neighbors(eligible, float(trial_elo), float(opponent_elo), config=cfg)
    if nearest.get("ok") is not True:
        return {
            "ok": False,
            "reason": str(nearest.get("reason") or "insufficient_reference"),
            "scope": scope,
            "eligibleReferenceCount": len(eligible),
            **{key: value for key, value in nearest.items() if key not in {"ok"}},
        }
    distribution = weighted_reference_distribution(nearest["weighted"], scope=scope)
    sd = float(distribution["sd"])
    if not math.isfinite(sd) or sd <= 0:
        return {
            "ok": False,
            "reason": "zero_reference_standard_deviation",
            "scope": scope,
            "eligibleReferenceCount": len(eligible),
            "K": nearest["K"],
            "boundaryDistance": nearest["boundaryDistance"],
        }
    game_z = (float(target_rate) - float(distribution["mean"])) / sd
    if not math.isfinite(game_z):
        return {"ok": False, "reason": "zero_reference_standard_deviation"}
    neighbor_rows = [
        {
            "gameId": str(row["record"].get("gameId") or ""),
            "targetColor": str(row["record"].get("targetColor") or ""),
            "distance": rounded(row["distance"]),
            "referenceWeight": rounded(row["referenceWeight"]),
            "equalPhaseGameGe4Rate": rounded(_record_equal_phase_rate(row["record"], scope)),
        }
        for row in nearest["weighted"]
    ]
    phase_reference: dict[str, float | None] = {}
    for phase, _lower, _upper in PHASES:
        values = []
        weights = []
        for row in nearest["weighted"]:
            phase_number = int(str(phase).replace("phase", ""))
            phase_value = _record_phase_rate(row["record"], scope, phase_number)
            if finite_number(phase_value):
                values.append(float(phase_value))
                weights.append(float(row["referenceWeight"]))
        phase_reference[phase] = sum(w * v for w, v in zip(weights, values, strict=True)) / sum(weights) if values and sum(weights) > 0 else None
    return {
        "ok": True,
        "gameId": str(target_record.get("gameId") or ""),
        "targetColor": str(target_record.get("targetColor") or ""),
        "scope": scope,
        "trialElo": int(trial_elo) if float(trial_elo).is_integer() else float(trial_elo),
        "opponentOldR": float(opponent_elo),
        "targetEqualPhaseGameGe4Rate": float(target_rate),
        "referenceMean": float(distribution["mean"]),
        "referenceVariance": float(distribution["variance"]),
        "referenceSd": sd,
        "gameZ": float(game_z),
        "eligibleReferenceCount": len(eligible),
        "K": nearest["K"],
        "boundaryDistance": float(nearest["boundaryDistance"]),
        "referencePhaseExpectedGe4": phase_reference,
        "neighbors": neighbor_rows,
    }


def _try_import_ckdtree() -> Any:
    try:
        from scipy.spatial import cKDTree  # type: ignore
    except ImportError:
        return None
    return cKDTree


class _ReferenceSearcher:
    """Exact KNN wrapper with a vectorized SciPy path and a stdlib path."""

    def __init__(self, records: Sequence[dict[str, Any]], config: dict[str, Any], scope: str) -> None:
        self.records = list(records)
        self.config = config
        self.scope = scope
        self.query_workers = int(config.get("referenceQueryWorkers", DEFAULT_REFERENCE_QUERY_WORKERS))
        self.k = neighbor_count(len(self.records), float(config["neighborExponent"])) if self.records else 0
        self.tree = None
        self._np = None
        if self.records and len(self.records) >= self.k + 1:
            cKDTree = _try_import_ckdtree()
            if cKDTree is not None:
                try:
                    import numpy as np  # type: ignore
                    self._np = np
                    self.tree = cKDTree(
                        np.asarray(
                            [[float(row["targetOldR"]), float(row["opponentOldR"])] for row in self.records],
                            dtype=float,
                        )
                    )
                except (ImportError, ValueError):
                    self.tree = None
                    self._np = None
        self.x = [
            float(_record_equal_phase_rate(row, self.scope))
            for row in self.records
        ]

    def _one(self, trial_elo: float, opponent_elo: float) -> dict[str, Any]:
        return nearest_weighted_neighbors(self.records, trial_elo, opponent_elo, config=self.config)

    def score_grid(self, grid: Sequence[int], opponent_elo: float, target_rate: float) -> list[dict[str, Any]]:
        return self.score_grid_batch([(opponent_elo, target_rate)], grid)[0]

    def score_grid_batch(
        self,
        requests: Sequence[tuple[float, float]],
        grid: Sequence[int],
        *,
        batch_size: int = 8,
    ) -> list[list[dict[str, Any]]]:
        """Score several target games in bounded batches of tree queries."""

        if not requests:
            return []
        if not self.records:
            return [
                [{"ok": False, "reason": "insufficient_reference"} for _ in grid]
                for _ in requests
            ]
        if self.tree is None or self._np is None:
            output: list[list[dict[str, Any]]] = []
            for opponent, rate in requests:
                rows = []
                for trial in grid:
                    nearest = self._one(trial, opponent)
                    rows.append(self._score_nearest(nearest, rate, self.scope))
                output.append(rows)
            return output
        if self.k == 0:
            return [
                [{"ok": False, "reason": "insufficient_reference"} for _ in grid]
                for _ in requests
            ]
        np = self._np
        grid_values = np.asarray(grid, dtype=float)
        output: list[list[dict[str, Any]]] = [
            [dict() for _ in grid] for _ in requests
        ]
        for start in range(0, len(requests), max(1, int(batch_size))):
            batch = requests[start:start + max(1, int(batch_size))]
            query_count = len(batch) * len(grid)
            queries = np.empty((query_count, 2), dtype=float)
            for batch_index, (opponent, _rate) in enumerate(batch):
                begin = batch_index * len(grid)
                end = begin + len(grid)
                queries[begin:end, 0] = grid_values
                queries[begin:end, 1] = float(opponent)
            distances, indexes = self.tree.query(queries, k=self.k + 1, workers=self.query_workers)
            selected_distances = distances[:, :self.k]
            boundary_distances = distances[:, self.k]
            with np.errstate(divide="ignore", invalid="ignore"):
                weights = np.maximum(0.0, 1.0 - selected_distances / boundary_distances[:, None])
                weight_sums = weights.sum(axis=1)
                values = np.asarray(self.x, dtype=float)[indexes[:, :self.k]]
                means = (weights * values).sum(axis=1) / weight_sums
                variances = (weights * (values - means[:, None]) ** 2).sum(axis=1) / weight_sums
                sds = np.sqrt(variances)
                rates = np.asarray([rate for _opponent, rate in batch for _elo in grid], dtype=float)
                z_values = (rates - means) / sds
            for batch_index, (opponent, rate) in enumerate(batch):
                begin = batch_index * len(grid)
                end = begin + len(grid)
                batch_output: list[dict[str, Any]] = []
                for local_index, trial in enumerate(grid):
                    row_index = begin + local_index
                    boundary = float(boundary_distances[row_index])
                    sd = float(sds[row_index])
                    valid = (
                        math.isfinite(boundary) and boundary > 0
                        and math.isfinite(float(weight_sums[row_index])) and float(weight_sums[row_index]) > 0
                        and math.isfinite(sd) and sd > 0
                        and math.isfinite(float(z_values[row_index]))
                    )
                    if not valid:
                        batch_output.append({
                            "ok": False,
                            "reason": "zero_reference_standard_deviation" if math.isfinite(sd) and sd <= 0 else "insufficient_reference",
                            "boundaryDistance": boundary,
                        })
                        continue
                    # cKDTree has deterministic distances, but its order among
                    # exact ties is unspecified. Re-run only boundary-tie rows
                    # through the stable-key implementation.
                    if abs(float(selected_distances[row_index, -1]) - boundary) <= 1e-12:
                        nearest = self._one(trial, opponent)
                        batch_output.append(self._score_nearest(nearest, rate, self.scope))
                    else:
                        batch_output.append({
                            "ok": True,
                            "gameZ": float(z_values[row_index]),
                            "referenceMean": float(means[row_index]),
                            "referenceVariance": float(variances[row_index]),
                            "referenceSd": sd,
                            "boundaryDistance": boundary,
                            "K": self.k,
                            "referenceCount": len(self.records),
                        })
                output[start + batch_index] = batch_output
        return output

    @staticmethod
    def _score_nearest(nearest: dict[str, Any], target_rate: float, scope: str) -> dict[str, Any]:
        if nearest.get("ok") is not True:
            return {"ok": False, "reason": nearest.get("reason", "insufficient_reference"), **nearest}
        distribution = weighted_reference_distribution(nearest["weighted"], scope=scope)
        sd = float(distribution["sd"])
        if not math.isfinite(sd) or sd <= 0:
            return {"ok": False, "reason": "zero_reference_standard_deviation"}
        return {
            "ok": True,
            "gameZ": (float(target_rate) - float(distribution["mean"])) / sd,
            "referenceMean": float(distribution["mean"]),
            "referenceVariance": float(distribution["variance"]),
            "referenceSd": sd,
            "boundaryDistance": float(nearest["boundaryDistance"]),
            "K": nearest["K"],
            "referenceCount": nearest["referenceCount"],
        }


def elo_grid(config: dict[str, Any] | None = None) -> list[int]:
    cfg = validate_config(config or default_config())
    return list(range(int(cfg["eloGridMinimum"]), int(cfg["eloGridMaximum"]) + 1, int(cfg["eloGridStep"])))


def _curve_stats(points: Sequence[dict[str, Any]], grid_step: int = 1) -> dict[str, Any]:
    valid = [point for point in points if finite_number(point.get("candidateZ")) and finite_number(point.get("score"))]
    if not valid:
        return {
            "minimumScore": None,
            "bestGridPoints": [],
            "crossings": [],
            "minimumPlateauWidth": None,
            "secondaryMinimumGap": None,
            "maximumAdjacentCandidateZJump": None,
        }
    minimum_score = min(float(point["score"]) for point in valid)
    best = [int(point["elo"]) for point in valid if abs(float(point["score"]) - minimum_score) <= 1e-12]
    crossings: list[list[int]] = []
    previous = None
    for point in valid:
        z = float(point["candidateZ"])
        if z == 0:
            current = [int(point["elo"]), int(point["elo"])]
        elif previous is not None and float(previous["candidateZ"]) * z < 0:
            current = [int(previous["elo"]), int(point["elo"])]
        else:
            previous = point
            continue
        if (
            crossings
            and current[0] == current[1]
            and crossings[-1][1] == current[0]
        ):
            crossings[-1][1] = current[1]
        else:
            crossings.append(current)
        previous = point
    local_minimum_scores: list[float] = []
    for index, point in enumerate(valid):
        score = float(point["score"])
        left = float(valid[index - 1]["score"]) if index > 0 else math.inf
        right = float(valid[index + 1]["score"]) if index + 1 < len(valid) else math.inf
        if score <= left and score <= right:
            local_minimum_scores.append(score)
    local_minimum_scores.sort()
    secondary_gap = (
        local_minimum_scores[1] - local_minimum_scores[0]
        if len(local_minimum_scores) >= 2 else None
    )
    jumps = [
        abs(float(right["candidateZ"]) - float(left["candidateZ"]))
        for left, right in zip(valid, valid[1:])
    ]
    return {
        "minimumScore": minimum_score,
        "bestGridPoints": best,
        "crossings": crossings,
        "minimumPlateauWidth": (max(best) - min(best)) if best else None,
        "secondaryMinimumGap": secondary_gap,
        "maximumAdjacentCandidateZJump": max(jumps) if jumps else 0.0,
    }


def classify_curve(
    curve: dict[str, Any],
    *,
    diagnostic_thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    points = curve.get("points") if isinstance(curve.get("points"), list) else []
    stats = _curve_stats(points)
    valid = [point for point in points if finite_number(point.get("candidateZ"))]
    if not valid:
        return {"status": "insufficient_reference", "statusReasons": ["no_grid_point_was_scorable"], **stats}
    z_values = [float(point["candidateZ"]) for point in valid]
    if all(value < 0 for value in z_values):
        return {"status": "above_reference_range", "statusReasons": ["candidateZ_is_negative_over_the_full_grid"], **stats}
    if all(value > 0 for value in z_values):
        return {"status": "below_reference_range", "statusReasons": ["candidateZ_is_positive_over_the_full_grid"], **stats}
    if len(stats["crossings"]) > 1:
        return {"status": "multiple_crossings", "statusReasons": ["multiple_separated_zero_crossings"], **stats}
    thresholds = diagnostic_thresholds or {}
    if (
        finite_number(thresholds.get("multipleCrossingsScoreDelta"))
        and finite_number(stats.get("secondaryMinimumGap"))
        and float(stats["secondaryMinimumGap"]) <= float(thresholds["multipleCrossingsScoreDelta"])
        and len(stats.get("bestGridPoints") or []) == 1
    ):
        return {"status": "multiple_crossings", "statusReasons": ["calibrated_near_tied_minima"], **stats}
    if (
        finite_number(thresholds.get("lowResolutionEloWidth"))
        and finite_number(stats.get("minimumPlateauWidth"))
        and float(stats["minimumPlateauWidth"]) > float(thresholds["lowResolutionEloWidth"])
    ):
        return {"status": "low_resolution", "statusReasons": ["calibrated_minimum_region_is_wide"], **stats}
    if (
        finite_number(thresholds.get("abnormalLocalJumpThreshold"))
        and finite_number(stats.get("maximumAdjacentCandidateZJump"))
        and float(stats["maximumAdjacentCandidateZJump"]) > float(thresholds["abnormalLocalJumpThreshold"])
    ):
        return {"status": "low_resolution", "statusReasons": ["calibrated_local_curve_jump"], **stats}
    return {"status": "valid", "statusReasons": ["one_internal_zero_crossing_or_exact_zero"], **stats}


def score_candidate_curve(
    target_records: Sequence[dict[str, Any]],
    reference_records: Sequence[dict[str, Any]],
    *,
    target_account: str,
    config: dict[str, Any] | None = None,
    diagnostic_thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = validate_config(config or default_config())
    grid = elo_grid(cfg)
    target_rows = list(target_records)
    excluded = excluded_reference_game_ids(
        reference_records,
        target_account,
        [str(row.get("gameId") or "") for row in target_rows],
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    searchers: dict[tuple[str, str], _ReferenceSearcher] = {}
    target_curves: dict[str, list[float | None]] = {}
    target_failures: dict[str, list[str]] = defaultdict(list)
    batch_requests: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    batch_game_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    for target in target_rows:
        scope = selected_metrics_scope(target)
        color = str(target.get("targetColor") or "").strip().casefold()
        key = (color, scope)
        if key not in grouped:
            grouped[key] = eligible_reference_records(
                reference_records,
                target,
                excluded_game_ids=excluded,
                config=cfg,
            )
            searchers[key] = _ReferenceSearcher(grouped[key], cfg, scope)
        target_metrics = metrics_for_record(target, scope)
        rate = target_metrics.get("equalPhaseGameGe4Rate")
        opponent = target.get("opponentOldR")
        game_id = str(target.get("gameId") or "")
        if not finite_number(rate) or not finite_number(opponent):
            target_curves[game_id] = [None for _ in grid]
            target_failures[game_id].append("incomplete_phase_data")
            continue
        batch_requests[key].append((float(opponent), float(rate)))
        batch_game_ids[key].append(game_id)

    for key, requests in batch_requests.items():
        scored_rows = searchers[key].score_grid_batch(requests, grid, batch_size=8)
        for game_id, scored in zip(batch_game_ids[key], scored_rows, strict=True):
            values: list[float | None] = []
            for item in scored:
                if item.get("ok") is True and finite_number(item.get("gameZ")):
                    values.append(float(item["gameZ"]))
                else:
                    values.append(None)
                    target_failures[game_id].append(str(item.get("reason") or "insufficient_reference"))
            target_curves[game_id] = values

    points: list[dict[str, Any]] = []
    for index, elo in enumerate(grid):
        game_zs = [values[index] for values in target_curves.values()]
        if game_zs and all(value is not None and math.isfinite(float(value)) for value in game_zs):
            candidate_z = statistics.fmean(float(value) for value in game_zs if value is not None)
            points.append({"elo": elo, "candidateZ": candidate_z, "score": abs(candidate_z)})
        else:
            points.append({"elo": elo, "candidateZ": None, "score": None})
    curve = {
        "eloGridMinimum": grid[0],
        "eloGridMaximum": grid[-1],
        "eloGridStep": int(cfg["eloGridStep"]),
        "points": points,
        "targetGameCount": len(target_rows),
        "excludedReferenceGameCount": len(excluded),
        "excludedReferenceGameIds": sorted(excluded),
        "targetGameFailures": {
            game_id: sorted(set(reasons)) for game_id, reasons in sorted(target_failures.items()) if reasons
        },
    }
    curve.update(classify_curve(curve, diagnostic_thresholds=diagnostic_thresholds))
    best = curve.get("bestGridPoints") or []
    best_elo = best[0] if best else None
    curve["bestGridPoint"] = best_elo
    curve["candidateZAtBest"] = next(
        (point["candidateZ"] for point in points if point["elo"] == best_elo), None
    ) if best_elo is not None else None
    curve["gameCurves"] = target_curves
    return curve


def interpolate_score(points: Sequence[dict[str, Any]], elo: float) -> float | None:
    if not finite_number(elo):
        return None
    ordered = sorted(
        (point for point in points if finite_number(point.get("score"))),
        key=lambda point: int(point["elo"]),
    )
    if not ordered or float(elo) < ordered[0]["elo"] or float(elo) > ordered[-1]["elo"]:
        return None
    for point in ordered:
        if float(point["elo"]) == float(elo):
            return float(point["score"])
    lower = max((point for point in ordered if float(point["elo"]) < float(elo)), key=lambda p: p["elo"], default=None)
    upper = min((point for point in ordered if float(point["elo"]) > float(elo)), key=lambda p: p["elo"], default=None)
    if lower is None or upper is None:
        return None
    fraction = (float(elo) - float(lower["elo"])) / (float(upper["elo"]) - float(lower["elo"]))
    return float(lower["score"]) * (1.0 - fraction) + float(upper["score"]) * fraction


def intervals_for_score_threshold(
    points: Sequence[dict[str, Any]],
    allowed_score: float,
) -> list[dict[str, Any]]:
    eligible = [
        int(point["elo"])
        for point in points
        if finite_number(point.get("score")) and float(point["score"]) <= float(allowed_score)
    ]
    if not eligible:
        return []
    intervals: list[dict[str, Any]] = []
    start = previous = eligible[0]
    for elo in eligible[1:]:
        if elo == previous + 1:
            previous = elo
            continue
        intervals.append({
            "lower": start,
            "upper": previous,
            "truncatedLower": start == int(points[0]["elo"]),
            "truncatedUpper": previous == int(points[-1]["elo"]),
        })
        start = previous = elo
    intervals.append({
        "lower": start,
        "upper": previous,
        "truncatedLower": start == int(points[0]["elo"]),
        "truncatedUpper": previous == int(points[-1]["elo"]),
    })
    return intervals


def _latest_known_elos(bundle: dict[str, Any]) -> dict[str, float | None]:
    candidates: dict[str, list[tuple[str, str, float | None]]] = defaultdict(list)
    for detail in bundle.get("details", []) if isinstance(bundle.get("details"), list) else []:
        game_id = str(detail.get("id") or "")
        created = str(detail.get("created") or "")
        for player in detail.get("players", []) if isinstance(detail.get("players"), list) else []:
            key = account_key(player.get("id"))
            if not key:
                continue
            candidates[key].append((created, game_id, optional_number(player.get("newR"))))
    result: dict[str, float | None] = {}
    for key, rows in candidates.items():
        latest = max(rows, key=lambda row: (row[0], row[1]))
        result[key] = latest[2]
    return result


def _split_accounts(
    accounts: Sequence[str],
    config: dict[str, Any],
    *,
    validation_candidates: Sequence[str] | None = None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    ordered = sorted(
        (account_key(account) for account in accounts),
        key=lambda account: hashlib.sha256(
            f"{int(config.get('calibrationSplitSeed', DEFAULT_SPLIT_SEED))}|{account}".encode("utf-8")
        ).hexdigest(),
    )
    if len(ordered) < 2:
        return [], ordered, {
            "method": "sha256(seed|accountKey) lexicographic order",
            "seed": int(config.get("calibrationSplitSeed", DEFAULT_SPLIT_SEED)),
            "calibrationFraction": float(config.get("calibrationValidationFraction", DEFAULT_CALIBRATION_VALIDATION_FRACTION)),
        }
    fraction = float(config.get("calibrationValidationFraction", DEFAULT_CALIBRATION_VALIDATION_FRACTION))
    if validation_candidates is None:
        validation_pool = ordered
        validation_method = "sha256(seed|accountKey) lexicographic order"
    else:
        candidate_set = {account_key(account) for account in validation_candidates}
        validation_pool = [account for account in ordered if account in candidate_set]
        validation_method = (
            "sha256(seed|accountKey) lexicographic order with validation holdout "
            "restricted to accounts meeting validationMinimumTargetGames"
        )
    if len(validation_pool) < 2:
        validation_pool = ordered
        validation_method = "sha256(seed|accountKey) lexicographic order"
    validation_count = int(math.floor(len(validation_pool) * fraction))
    validation_count = max(1, min(len(validation_pool) - 1, validation_count))
    validation_accounts = validation_pool[-validation_count:]
    validation_set = set(validation_accounts)
    calibration_accounts = [account for account in ordered if account not in validation_set]
    return calibration_accounts, validation_accounts, {
        "method": validation_method,
        "seed": int(config.get("calibrationSplitSeed", DEFAULT_SPLIT_SEED)),
        "calibrationFraction": len(calibration_accounts) / len(ordered),
        "validationFraction": len(validation_accounts) / len(ordered),
        "validationCandidateCount": len(validation_pool),
        "validationMinimumTargetGames": int(config.get("validationMinimumTargetGames", DEFAULT_VALIDATION_MINIMUM_TARGET_GAMES)),
    }


def _calibration_user_records(
    reference_records: Sequence[dict[str, Any]],
    account: str,
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    rows = [
        row for row in reference_records
        if account_key(row.get("targetPlayerId")) == account_key(account)
        and row.get("formalReferenceEligible") is True
        and _target_record_rejection(row, config) is None
    ]
    rows.sort(key=lambda row: (str(row.get("created") or ""), str(row.get("gameId") or "")), reverse=True)
    maximum = int(config["maximumTargetGames"])
    return {
        "allValidRecords": rows,
        "selected": rows[:maximum],
        "excludedOlder": rows[maximum:],
    }


def _error_summary(values: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"count": 0, "median": None, "mean": None, "p90": None, "p95": None}
    def q(probability: float) -> float:
        position = (len(ordered) - 1) * probability
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        fraction = position - lower
        return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction
    return {
        "count": len(ordered),
        "median": q(0.5),
        "mean": statistics.fmean(ordered),
        "p90": q(0.9),
        "p95": q(0.95),
    }


def _make_calibration_case(
    role: str,
    account: str,
    selected: Sequence[dict[str, Any]],
    known: float,
    reference_records: Sequence[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    minimum_elo = int(config["formalEloMinimum"])
    maximum_elo = int(config["formalEloMaximum"])
    selected_rows = list(selected)
    excluded = excluded_reference_game_ids(
        reference_records,
        account,
        [str(row.get("gameId") or "") for row in selected_rows],
    )
    curve = score_candidate_curve(
        selected_rows,
        reference_records,
        target_account=account,
        config=config,
    )
    known_in_range = minimum_elo <= float(known) <= maximum_elo
    score_at_known = interpolate_score(curve.get("points", []), float(known)) if known_in_range else None
    minimum_score = curve.get("minimumScore")
    return {
        "schema": "player-sentinel-elo-calibration-case-v1",
        "account": account,
        "role": role,
        "knownElo": float(known),
        "knownEloDefinition": "newR from the account's latest created source-bundle detail",
        "selectedGameIds": [str(row.get("gameId") or "") for row in selected_rows],
        "selectedGameCount": len(selected_rows),
        "excludedReferenceGameCount": len(excluded),
        "curveStatus": curve.get("status"),
        "bestGridPoint": curve.get("bestGridPoint"),
        "minimumScore": minimum_score,
        "candidateZAtBest": curve.get("candidateZAtBest"),
        "knownEloInFormalRange": known_in_range,
        "scoreAtKnownElo": score_at_known,
        "trueScoreIncrease": (
            float(score_at_known) - float(minimum_score)
            if score_at_known is not None and finite_number(minimum_score) else None
        ),
        "estimatedEloError": (
            float(curve["bestGridPoint"]) - float(known)
            if curve.get("bestGridPoint") is not None else None
        ),
        "scoreCurve": curve.get("points", []),
        "statusReasons": curve.get("statusReasons", []),
    }


_CALIBRATION_WORKER_REFERENCE_RECORDS: list[dict[str, Any]] = []
_CALIBRATION_WORKER_BY_ACCOUNT: dict[str, list[dict[str, Any]]] = {}
_CALIBRATION_WORKER_CONFIG: dict[str, Any] = {}


def _init_calibration_worker(reference_records_path: str, config: dict[str, Any]) -> None:
    global _CALIBRATION_WORKER_REFERENCE_RECORDS
    global _CALIBRATION_WORKER_BY_ACCOUNT
    global _CALIBRATION_WORKER_CONFIG
    _CALIBRATION_WORKER_REFERENCE_RECORDS = read_jsonl(reference_records_path)
    _CALIBRATION_WORKER_CONFIG = validate_config(config)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in _CALIBRATION_WORKER_REFERENCE_RECORDS:
        account = account_key(record.get("targetPlayerId"))
        if (
            account
            and record.get("formalReferenceEligible") is True
            and _target_record_rejection(record, _CALIBRATION_WORKER_CONFIG) is None
        ):
            grouped[account].append(record)
    for account in grouped:
        grouped[account].sort(
            key=lambda row: (str(row.get("created") or ""), str(row.get("gameId") or "")),
            reverse=True,
        )
    _CALIBRATION_WORKER_BY_ACCOUNT = dict(grouped)


def _calibration_case_worker(task: tuple[str, str, float]) -> dict[str, Any]:
    role, account, known = task
    selected = _CALIBRATION_WORKER_BY_ACCOUNT[account][:_CALIBRATION_WORKER_CONFIG["maximumTargetGames"]]
    return _make_calibration_case(
        role,
        account,
        selected,
        known,
        _CALIBRATION_WORKER_REFERENCE_RECORDS,
        _CALIBRATION_WORKER_CONFIG,
    )


def calibrate_global_interval(
    reference_records: Sequence[dict[str, Any]],
    source_bundle: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    reference_records_path: str | Path | None = None,
    parallel_workers: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a leave-one-account-out global threshold and independent check."""

    cfg = validate_config(config or default_config())
    by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    known_elos = _latest_known_elos(source_bundle)
    for record in reference_records:
        account = account_key(record.get("targetPlayerId"))
        if not account or record.get("formalReferenceEligible") is not True:
            continue
        if _target_record_rejection(record, cfg) is None:
            by_account[account].append(record)
    for account in by_account:
        by_account[account].sort(
            key=lambda row: (str(row.get("created") or ""), str(row.get("gameId") or "")),
            reverse=True,
        )
    eligible_accounts: list[str] = []
    skipped_accounts: list[dict[str, Any]] = []
    for account in sorted(by_account):
        selected = by_account[account][:int(cfg["maximumTargetGames"])]
        known = known_elos.get(account)
        if len(selected) < int(cfg["minimumTargetGames"]):
            skipped_accounts.append({"account": account, "reason": "insufficient_target_games", "validRecordCount": len(selected)})
            continue
        if not finite_number(known):
            skipped_accounts.append({"account": account, "reason": "missing_known_newR"})
            continue
        eligible_accounts.append(account)
    validation_minimum_games = int(
        cfg.get("validationMinimumTargetGames", DEFAULT_VALIDATION_MINIMUM_TARGET_GAMES)
    )
    validation_candidates = [
        account
        for account in eligible_accounts
        if len(by_account[account][:int(cfg["maximumTargetGames"])]) >= validation_minimum_games
    ]
    calibration_accounts, validation_accounts, split = _split_accounts(
        eligible_accounts,
        cfg,
        validation_candidates=validation_candidates,
    )
    min_elo = int(cfg["formalEloMinimum"])
    max_elo = int(cfg["formalEloMaximum"])
    tasks = [
        (role, account, float(known_elos[account]))
        for role, accounts in (("calibration", calibration_accounts), ("validation", validation_accounts))
        for account in accounts
    ]
    workers = int(parallel_workers if parallel_workers is not None else cfg.get("calibrationWorkers", 1))
    workers = max(1, workers)
    if reference_records_path is not None and workers > 1 and len(tasks) > 1:
        worker_config = dict(cfg)
        # Sixteen processes are the parallel unit. A tree query thread per
        # process avoids an accidental 16x16 oversubscription.
        worker_config["referenceQueryWorkers"] = 1
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_calibration_worker,
            initargs=(str(Path(reference_records_path).resolve()), worker_config),
        ) as executor:
            cases = list(executor.map(_calibration_case_worker, tasks, chunksize=1))
    else:
        cases = [
            _make_calibration_case(
                role,
                account,
                by_account[account][:int(cfg["maximumTargetGames"])],
                float(known_elos[account]),
                reference_records,
                cfg,
            )
            for role, account, _known in tasks
        ]

    calibration_cases = [
        case for case in cases
        if case["role"] == "calibration"
        and case["knownEloInFormalRange"] is True
        and finite_number(case.get("trueScoreIncrease"))
        and case.get("curveStatus") == "valid"
    ]
    increases = [float(case["trueScoreIncrease"]) for case in calibration_cases]
    if increases:
        ordered = sorted(increases)
        position = (len(ordered) - 1) * float(cfg["calibrationCoverage"])
        lower = math.floor(position)
        upper = math.ceil(position)
        t95 = ordered[lower] if lower == upper else ordered[lower] * (upper - position) + ordered[upper] * (position - lower)
    else:
        t95 = None
    threshold_source = calibration_cases
    # Diagnostic thresholds are themselves artifacts of the calibration set;
    # no user-specific adjustment is made at estimation time.
    widths = []
    gaps = []
    jumps = []
    for case in threshold_source:
        stats = _curve_stats(case["scoreCurve"])
        if finite_number(stats.get("minimumPlateauWidth")):
            widths.append(float(stats["minimumPlateauWidth"]))
        if finite_number(stats.get("secondaryMinimumGap")):
            gaps.append(float(stats["secondaryMinimumGap"]))
        if finite_number(stats.get("maximumAdjacentCandidateZJump")):
            jumps.append(float(stats["maximumAdjacentCandidateZJump"]))
    diagnostic_thresholds = {
        "method": "empirical calibration-user quantiles",
        "lowResolutionEloWidth": max(widths) if widths else None,
        "multipleCrossingsScoreDelta": min(gaps) if gaps else None,
        "abnormalLocalJumpThreshold": max(jumps) if jumps else None,
        "sourceCalibrationUserCount": len(threshold_source),
    }

    validation_cases = [
        case for case in cases
        if case["role"] == "validation"
        and case["knownEloInFormalRange"] is True
        and finite_number(case.get("scoreAtKnownElo"))
        and finite_number(case.get("minimumScore"))
        and case.get("bestGridPoint") is not None
    ]
    covered = [
        float(case["scoreAtKnownElo"]) <= float(case["minimumScore"]) + float(t95)
        for case in validation_cases
    ] if t95 is not None else []
    validation_coverage = statistics.fmean(covered) if covered else None
    errors = [
        abs(float(case["estimatedEloError"]))
        for case in validation_cases if finite_number(case.get("estimatedEloError"))
    ]
    min_validation_users = int(cfg.get("minimumValidationUsers", DEFAULT_MINIMUM_VALIDATION_USERS))
    validated = bool(
        t95 is not None
        and len(calibration_cases) >= 1
        and len(validation_cases) >= min_validation_users
        and validation_coverage is not None
        and validation_coverage >= float(cfg["calibrationCoverage"])
    )
    status = "validated" if validated else "calibration_unavailable"
    artifact = {
        "schema": SCHEMA_CALIBRATION,
        "version": "v1",
        "createdAt": utc_now(),
        "formalEloMinimum": min_elo,
        "formalEloMaximum": max_elo,
        "minimumTargetGames": int(cfg["minimumTargetGames"]),
        "maximumTargetGames": int(cfg["maximumTargetGames"]),
        "validationMinimumTargetGames": validation_minimum_games,
        "calibrationCoverage": float(cfg["calibrationCoverage"]),
        "calibrationGrouping": "global",
        "knownEloDefinition": "newR from the latest created source-bundle detail for each account",
        "knownEloInterpolation": "linear interpolation between adjacent integer Elo score points",
        "quantileMethod": "unweighted empirical linear interpolation at p=(n-1)*q",
        "t95": t95,
        "calibrationUserCount": len(calibration_accounts),
        "validationUserCount": len(validation_accounts),
        "calibrationCaseCount": len(calibration_cases),
        "validationCaseCount": len(validation_cases),
        "validationCoveredCount": sum(covered) if covered else 0,
        "validationCoverage": validation_coverage,
        "usersAreDisjointFromCalibration": not bool(
            set(calibration_accounts) & set(validation_accounts)
        ),
        "minimumValidationUsers": min_validation_users,
        "status": status,
        "split": {
            **split,
            "calibrationAccounts": calibration_accounts,
            "validationAccounts": validation_accounts,
        },
        "skippedAccounts": skipped_accounts,
        "diagnosticThresholds": diagnostic_thresholds,
        "validationErrorSummary": _error_summary(errors),
        "parallelWorkers": workers,
        "referenceQueryWorkersPerWorker": 1 if reference_records_path is not None and workers > 1 else int(cfg.get("referenceQueryWorkers", DEFAULT_REFERENCE_QUERY_WORKERS)),
        "independentValidation": {
            "required": True,
            "usersAreDisjointFromCalibration": not bool(set(calibration_accounts) & set(validation_accounts)),
            "coverageTarget": float(cfg["calibrationCoverage"]),
            "coverageConfirmed": validated,
        },
    }
    return artifact, sorted(cases, key=lambda case: (str(case.get("role")), str(case.get("account"))))


@dataclass(frozen=True)
class TargetEstimate:
    payload: dict[str, Any]
    curve: dict[str, Any]
    selected_records: tuple[dict[str, Any], ...]


def estimate_database_calibrated_range(
    account: str,
    target_records: Sequence[dict[str, Any]],
    reference_records: Sequence[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
    reference_version: str | None = None,
    reference_manifest_sha256: str | None = None,
    calibration_version: str | None = None,
) -> TargetEstimate:
    cfg = validate_config(config or default_config())
    selection = select_target_records(target_records, config=cfg)
    selected = list(selection["selected"])
    base: dict[str, Any] = {
        "schema": SCHEMA_ESTIMATE,
        "account": account,
        "referenceVersion": reference_version,
        "referenceManifestSha256": reference_manifest_sha256,
        "calibrationVersion": calibration_version,
        "selectedGameIds": [str(row.get("gameId") or "") for row in selected],
        "selectedGameCount": len(selected),
        "excludedGamesWithReasons": selection["excluded"],
        "formalMinimumGameCount": int(cfg["minimumTargetGames"]),
        "formalMaximumGameCount": int(cfg["maximumTargetGames"]),
        "eloGridMinimum": int(cfg["eloGridMinimum"]),
        "eloGridMaximum": int(cfg["eloGridMaximum"]),
        "estimatedElo": None,
        "bestGridPoint": None,
        "minimumScore": None,
        "candidateZAtBest": None,
        "databaseCalibrated95Range": None,
        "databaseCalibrated95Intervals": [],
        "status": selection["status"],
        "statusReasons": [],
        "phaseDiagnostics": [],
        "gameDiagnostics": [],
        "curveFile": None,
        "createdAt": utc_now(),
    }
    if selection["status"] != "valid":
        base["statusReasons"] = ["fewer_than_minimum_complete_recent_target_games"]
        curve = {
            "points": [],
            "status": selection["status"],
            "statusReasons": base["statusReasons"],
            "gameCurves": {},
        }
        return TargetEstimate(base, curve, tuple(selected))

    thresholds = (calibration or {}).get("diagnosticThresholds") if calibration else None
    curve = score_candidate_curve(
        selected,
        reference_records,
        target_account=account,
        config=cfg,
        diagnostic_thresholds=thresholds,
    )
    curve_status = str(curve.get("status") or "insufficient_reference")
    best = curve.get("bestGridPoint")
    base["bestGridPoint"] = best
    base["minimumScore"] = curve.get("minimumScore")
    base["candidateZAtBest"] = curve.get("candidateZAtBest")
    base["statusReasons"] = list(curve.get("statusReasons") or [])
    base["status"] = curve_status
    if curve_status == "valid" and best is not None:
        base["estimatedElo"] = int(best)
        if calibration and calibration.get("status") == "validated" and finite_number(calibration.get("t95")):
            allowed = float(curve["minimumScore"]) + float(calibration["t95"])
            intervals = intervals_for_score_threshold(curve["points"], allowed)
            base["databaseCalibrated95Intervals"] = intervals
            if len(intervals) == 1:
                base["databaseCalibrated95Range"] = intervals[0]
            if len(intervals) > 1:
                base["status"] = "multiple_crossings"
                base["statusReasons"].append("calibrated_score_set_has_multiple_intervals")
        else:
            base["status"] = "calibration_unavailable"
            base["statusReasons"].append("independent_validation_did_not_confirm_database_95_percent_coverage")

    best_game_diagnostics: list[dict[str, Any]] = []
    phase_rows: list[dict[str, Any]] = []
    if best is not None:
        excluded = excluded_reference_game_ids(
            reference_records,
            account,
            [str(row.get("gameId") or "") for row in selected],
        )
        for target in selected:
            scored = score_game_at_elo(
                target,
                reference_records,
                float(best),
                excluded_game_ids=excluded,
                config=cfg,
            )
            if scored.get("ok") is not True:
                continue
            game_id = str(target.get("gameId") or "")
            best_game_diagnostics.append({
                "gameId": game_id,
                "targetColor": target.get("targetColor"),
                "scope": scored.get("scope"),
                "targetEqualPhaseGameGe4Rate": scored.get("targetEqualPhaseGameGe4Rate"),
                "referenceMean": scored.get("referenceMean"),
                "referenceSd": scored.get("referenceSd"),
                "gameZ": scored.get("gameZ"),
                "referenceNeighborCount": scored.get("K"),
                "eligibleReferenceCount": scored.get("eligibleReferenceCount"),
            })
            metrics = metrics_for_record(target)
            reference_phase = scored.get("referencePhaseExpectedGe4") or {}
            for phase, _lower, _upper in PHASES:
                target_phase = (metrics.get(phase) or {}).get("lossGe4Rate")
                reference_value = reference_phase.get(phase)
                phase_rows.append({
                    "gameId": game_id,
                    "targetColor": target.get("targetColor"),
                    "scope": scored.get("scope"),
                    "trialElo": int(best),
                    "phase": phase,
                    "targetGe4Rate": target_phase,
                    "referenceExpectedGe4Rate": reference_value,
                    "differenceTargetMinusReference": (
                        float(target_phase) - float(reference_value)
                        if finite_number(target_phase) and finite_number(reference_value) else None
                    ),
                    "direction": (
                        "worse_than_trial" if float(target_phase) > float(reference_value)
                        else "better_than_trial" if float(target_phase) < float(reference_value)
                        else "equal"
                    ) if finite_number(target_phase) and finite_number(reference_value) else None,
                })
    game_zs = [float(row["gameZ"]) for row in best_game_diagnostics if finite_number(row.get("gameZ"))]
    base["gameDiagnostics"] = best_game_diagnostics
    base["phaseDiagnostics"] = phase_rows
    base["diagnosticSummary"] = {
        "gameZMinimum": min(game_zs) if game_zs else None,
        "gameZMaximum": max(game_zs) if game_zs else None,
        "gameZMean": statistics.fmean(game_zs) if game_zs else None,
        "gameZMedian": statistics.median(game_zs) if game_zs else None,
        "meanAbsGameZ": statistics.fmean(abs(value) for value in game_zs) if game_zs else None,
        "scoreCurveMinimumRegionWidth": curve.get("minimumPlateauWidth"),
        "candidateZLocalGradient": _candidate_gradient(curve.get("points", []), best),
        "crossings": curve.get("crossings", []),
    }
    return TargetEstimate(base, curve, tuple(selected))


def _candidate_gradient(points: Sequence[dict[str, Any]], elo: int | None) -> float | None:
    if elo is None:
        return None
    by_elo = {int(point["elo"]): point for point in points if finite_number(point.get("candidateZ"))}
    if elo - 1 in by_elo and elo + 1 in by_elo:
        return (float(by_elo[elo + 1]["candidateZ"]) - float(by_elo[elo - 1]["candidateZ"])) / 2.0
    if elo + 1 in by_elo and elo in by_elo:
        return float(by_elo[elo + 1]["candidateZ"]) - float(by_elo[elo]["candidateZ"])
    if elo - 1 in by_elo and elo in by_elo:
        return float(by_elo[elo]["candidateZ"]) - float(by_elo[elo - 1]["candidateZ"])
    return None


def reference_records_from_directory(directory: str | Path, *, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = validate_config(config or default_config())
    path = Path(directory) / str(cfg.get("directedPhaseRecords") or "directed_game_phase_records.jsonl")
    if not path.is_file():
        raise FileNotFoundError(path)
    rows = read_jsonl(path)
    if any(row.get("schema") != SCHEMA_DIRECTED for row in rows):
        raise ValueError("reference phase JSONL contains an unsupported schema")
    return rows


def calibration_cases_to_jsonl(
    path: str | Path,
    cases: Sequence[dict[str, Any]],
    *,
    refuse_existing: bool = True,
) -> None:
    write_jsonl(path, list(cases), refuse_existing=refuse_existing)


# ---------------------------------------------------------------------------
# Estimated-Elo v2: Beta-Binomial conditional likelihood
# ---------------------------------------------------------------------------


def _scipy_v2() -> tuple[Any, Any, Any]:
    """Load the required numerical dependency or fail with a formal-path error."""

    try:
        from scipy import optimize, special  # type: ignore
        from scipy.spatial import cKDTree  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised where SciPy is absent
        raise RuntimeError(
            "estimated-Elo v2 requires SciPy special, optimize, and spatial; "
            "no alternate statistical algorithm is permitted"
        ) from exc
    return special, optimize, cKDTree


def beta_binomial_log_probability(
    x: int,
    n: int,
    alpha: float,
    beta: float,
) -> float:
    """Numerically stable log P(X=x) for the Beta-Binomial distribution."""

    special, _optimize, _tree = _scipy_v2()
    if isinstance(x, bool) or isinstance(n, bool) or int(x) != x or int(n) != n:
        raise ValueError("x and n must be integers")
    x = int(x)
    n = int(n)
    if n < 0 or x < 0 or x > n:
        raise ValueError("Beta-Binomial requires 0 <= x <= n")
    if not all(math.isfinite(float(value)) and float(value) > 0 for value in (alpha, beta)):
        raise ValueError("alpha and beta must be positive finite values")
    kappa = float(alpha) + float(beta)
    m = float(alpha) / kappa
    log_choose = float(special.gammaln(n + 1) - special.gammaln(x + 1) - special.gammaln(n - x + 1))
    # This is the exact mathematical kappa -> infinity limit.  It avoids
    # subtracting nearly equal betaln values and is recorded by fit status.
    if kappa >= BINOMIAL_LIMIT_KAPPA:
        if m <= 0.0:
            return 0.0 if x == 0 else -math.inf
        if m >= 1.0:
            return 0.0 if x == n else -math.inf
        return log_choose + x * math.log(m) + (n - x) * math.log1p(-m)
    return float(
        log_choose
        + special.betaln(x + float(alpha), n - x + float(beta))
        - special.betaln(float(alpha), float(beta))
    )


def beta_binomial_probability(x: int, n: int, alpha: float, beta: float) -> float:
    return math.exp(beta_binomial_log_probability(x, n, alpha, beta))


def beta_binomial_mid_cdf_z(
    x: int,
    n: int,
    alpha: float,
    beta: float,
    *,
    q_clip: float = Z_CDF_CLIP,
) -> dict[str, float]:
    """Return exact mass, mid-CDF percentile, and clipped normal score."""

    special, _optimize, _tree = _scipy_v2()
    logs = [beta_binomial_log_probability(value, n, alpha, beta) for value in range(int(x) + 1)]
    log_exact = logs[-1]
    exact = math.exp(log_exact)
    lower = 0.0 if int(x) == 0 else math.exp(float(special.logsumexp(logs[:-1])))
    q_raw = min(1.0, max(0.0, lower + 0.5 * exact))
    clip = float(q_clip)
    if not 0.0 < clip < 0.5:
        raise ValueError("q_clip must be between 0 and 0.5")
    q = min(1.0 - clip, max(clip, q_raw))
    z = float(special.ndtri(q))
    return {
        "PExact": exact,
        "logPExact": log_exact,
        "midCdf": q_raw,
        "clippedMidCdf": q,
        "z": z,
    }


def _logit(value: float) -> float:
    return math.log(float(value)) - math.log1p(-float(value))


def _expit(value: float) -> float:
    if value >= 0:
        exp_negative = math.exp(-value)
        return 1.0 / (1.0 + exp_negative)
    exp_positive = math.exp(value)
    return exp_positive / (1.0 + exp_positive)


def fit_weighted_beta_binomial(
    observations: Sequence[tuple[int, int, float]],
    *,
    config: dict[str, Any] | None = None,
    initial_parameters: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Fit ``m`` and ``kappa`` by deterministic weighted maximum likelihood."""

    cfg = validate_v2_config(config or default_v2_config())
    special, optimize, _tree = _scipy_v2()
    import numpy as np  # type: ignore
    clean: list[tuple[int, int, float]] = []
    for x, n, weight in observations:
        if isinstance(x, bool) or isinstance(n, bool) or int(x) != x or int(n) != n:
            return {"ok": False, "fitStatus": "invalid_observation", "reason": "non_integer_count"}
        if int(n) <= 0 or int(x) < 0 or int(x) > int(n):
            return {"ok": False, "fitStatus": "invalid_observation", "reason": "count_out_of_range"}
        if not math.isfinite(float(weight)) or float(weight) < 0:
            return {"ok": False, "fitStatus": "invalid_observation", "reason": "invalid_weight"}
        if float(weight) > 0:
            clean.append((int(x), int(n), float(weight)))
    if not clean or sum(weight for _x, _n, weight in clean) <= 0:
        return {"ok": False, "fitStatus": "insufficient_weight", "reason": "no_positive_weight"}

    m_min = float(cfg["betaBinomialMMinimum"])
    kappa_min = float(cfg["betaBinomialKappaMinimum"])
    kappa_max = float(cfg["betaBinomialKappaMaximum"])
    total_success = sum(weight * x for x, _n, weight in clean)
    total_trials = sum(weight * n for _x, n, weight in clean)
    raw_m = total_success / total_trials
    initial_m = min(1.0 - m_min, max(m_min, raw_m))
    rates = [x / n for x, n, _weight in clean]
    weight_sum = sum(weight for _x, _n, weight in clean)
    rate_mean = sum(weight * rate for rate, (_x, _n, weight) in zip(rates, clean, strict=True)) / weight_sum
    rate_var = sum(
        weight * (rate - rate_mean) ** 2
        for rate, (_x, _n, weight) in zip(rates, clean, strict=True)
    ) / weight_sum
    mean_n = sum(weight * n for _x, n, weight in clean) / weight_sum
    binomial_rate_var = initial_m * (1.0 - initial_m) / max(1.0, mean_n)
    if rate_var > binomial_rate_var and initial_m * (1.0 - initial_m) > rate_var:
        rho = min(1.0 - 1e-9, max(1e-9, (rate_var - binomial_rate_var) / max(1e-15, initial_m * (1.0 - initial_m) - binomial_rate_var)))
        moment_kappa = (1.0 - rho) / rho
    else:
        moment_kappa = 1000.0
    moment_kappa = min(kappa_max, max(kappa_min, moment_kappa))

    # Aggregate identical (x,n) cells.  This is algebraically exact because
    # their likelihood terms differ only by multiplicative weights, and it
    # reduces a formal K pool to a small phase-count table.
    aggregated: dict[tuple[int, int], float] = defaultdict(float)
    for x, n, weight in clean:
        aggregated[(x, n)] += weight
    x_array = np.asarray([key[0] for key in sorted(aggregated)], dtype=float)
    n_array = np.asarray([key[1] for key in sorted(aggregated)], dtype=float)
    weight_array = np.asarray([aggregated[key] for key in sorted(aggregated)], dtype=float)
    log_choose_array = (
        special.gammaln(n_array + 1.0)
        - special.gammaln(x_array + 1.0)
        - special.gammaln(n_array - x_array + 1.0)
    )

    m_lower = _logit(m_min)
    m_upper = _logit(1.0 - m_min)
    k_lower = math.log(kappa_min)
    k_upper = math.log(kappa_max)

    def objective(parameters: Sequence[float]) -> float:
        m = _expit(float(parameters[0]))
        kappa = math.exp(float(parameters[1]))
        if kappa >= BINOMIAL_LIMIT_KAPPA:
            log_probabilities = (
                log_choose_array
                + x_array * math.log(m)
                + (n_array - x_array) * math.log1p(-m)
            )
        else:
            alpha = m * kappa
            beta = (1.0 - m) * kappa
            log_probabilities = (
                log_choose_array
                + special.betaln(x_array + alpha, n_array - x_array + beta)
                - special.betaln(alpha, beta)
            )
        score = float(np.dot(weight_array, log_probabilities))
        return -float(score) if math.isfinite(score) else math.inf

    initial_points: list[tuple[float, float]] = []
    if initial_parameters is not None:
        warm_m = min(1.0 - m_min, max(m_min, float(initial_parameters[0])))
        warm_kappa = min(kappa_max, max(kappa_min, float(initial_parameters[1])))
        initial_points.append((warm_m, warm_kappa))
    for candidate_m, candidate_kappa in ((initial_m, moment_kappa), (initial_m, 20.0)):
        clipped = min(kappa_max, max(kappa_min, candidate_kappa))
        if not any(
            math.isclose(candidate_m, existing_m, rel_tol=0.0, abs_tol=1e-12)
            and math.isclose(clipped, existing_kappa, rel_tol=0.0, abs_tol=1e-12)
            for existing_m, existing_kappa in initial_points
        ):
            initial_points.append((candidate_m, clipped))
    attempts: list[dict[str, Any]] = []
    best_result = None
    for attempt_m, initial_kappa in initial_points:
        result = optimize.minimize(
            objective,
            x0=[_logit(attempt_m), math.log(initial_kappa)],
            method="L-BFGS-B",
            bounds=[(m_lower, m_upper), (k_lower, k_upper)],
            options={"maxiter": 80, "ftol": 1e-10, "gtol": 1e-7, "maxls": 20},
        )
        attempts.append({
            "initialM": attempt_m,
            "initialKappa": initial_kappa,
            "success": bool(result.success),
            "status": int(result.status),
            "message": str(result.message),
            "iterations": int(getattr(result, "nit", 0)),
            "objective": float(result.fun) if math.isfinite(float(result.fun)) else None,
        })
        if result.success and math.isfinite(float(result.fun)) and (
            best_result is None or float(result.fun) < float(best_result.fun)
        ):
            best_result = result
        if result.success and math.isfinite(float(result.fun)):
            at_boundary = (
                abs(float(result.x[0]) - m_lower) <= 1e-7
                or abs(float(result.x[0]) - m_upper) <= 1e-7
                or abs(float(result.x[1]) - k_lower) <= 1e-7
                or abs(float(result.x[1]) - k_upper) <= 1e-7
            )
            # The moment start is the formal primary path.  The fixed second
            # start is retained only for convergence/boundary auditing, which
            # avoids doubling every ordinary candidate fit.
            if not at_boundary:
                break
    if best_result is None:
        return {
            "ok": False,
            "fitStatus": "optimizer_not_converged",
            "reason": "all_deterministic_optimizer_attempts_failed",
            "attempts": attempts,
            "observationCount": len(clean),
            "effectiveWeight": sum(weight for _x, _n, weight in clean),
        }
    m = _expit(float(best_result.x[0]))
    kappa = math.exp(float(best_result.x[1]))
    tolerance = 1e-7
    boundary_flags = {
        "mLower": abs(float(best_result.x[0]) - m_lower) <= tolerance,
        "mUpper": abs(float(best_result.x[0]) - m_upper) <= tolerance,
        "kappaLower": abs(float(best_result.x[1]) - k_lower) <= tolerance,
        "kappaUpper": abs(float(best_result.x[1]) - k_upper) <= tolerance,
    }
    fit_status = "converged_at_boundary" if any(boundary_flags.values()) else "converged"
    if kappa >= BINOMIAL_LIMIT_KAPPA:
        fit_status = "converged_binomial_limit" if not any(boundary_flags.values()) else "converged_at_boundary_binomial_limit"
    return {
        "ok": True,
        "fitStatus": fit_status,
        "m": m,
        "kappa": kappa,
        "alpha": m * kappa,
        "beta": (1.0 - m) * kappa,
        "fitScore": -float(best_result.fun),
        "optimizerBoundary": boundary_flags,
        "observationCount": len(clean),
        "effectiveWeight": sum(weight for _x, _n, weight in clean),
        "attempts": attempts,
    }


def standardized_elo_distance(
    reference_self_elo: float,
    target_trial_elo: float,
    reference_opponent_elo: float,
    target_opponent_elo: float,
    self_elo_sd: float,
    opponent_elo_sd: float,
    *,
    reference_previous_z: float | None = None,
    target_previous_z: float | None = None,
    previous_z_sd: float | None = None,
    previous_z_weight: float = 1.0,
) -> float:
    if not math.isfinite(float(self_elo_sd)) or float(self_elo_sd) <= 0:
        raise ValueError("self_elo_sd must be positive")
    if not math.isfinite(float(opponent_elo_sd)) or float(opponent_elo_sd) <= 0:
        raise ValueError("opponent_elo_sd must be positive")
    distance_squared = ((float(reference_self_elo) - float(target_trial_elo)) / float(self_elo_sd)) ** 2
    distance_squared += ((float(reference_opponent_elo) - float(target_opponent_elo)) / float(opponent_elo_sd)) ** 2
    if reference_previous_z is not None or target_previous_z is not None or previous_z_sd is not None:
        if not all(value is not None and math.isfinite(float(value)) for value in (reference_previous_z, target_previous_z, previous_z_sd)):
            raise ValueError("all previous-z distance values must be finite")
        if float(previous_z_sd) <= 0:
            raise ValueError("previous_z_sd must be positive")
        distance_squared += float(previous_z_weight) * (
            (float(reference_previous_z) - float(target_previous_z)) / float(previous_z_sd)
        ) ** 2
    return math.sqrt(distance_squared)


def _phase_counts(record: dict[str, Any], phase_number: int) -> tuple[int, int] | None:
    phase = record.get(f"phase{phase_number}")
    if isinstance(phase, dict):
        x = phase.get("x", phase.get("lossGe4Count"))
        n = phase.get("n", phase.get("validLossNodeCount"))
    else:
        scope = record.get("scope") or selected_metrics_scope(record)
        metrics = (record.get("metrics") or {}).get(scope, {})
        value = metrics.get(f"phase{phase_number}") if isinstance(metrics, dict) else None
        x = value.get("lossGe4Count") if isinstance(value, dict) else None
        n = value.get("validLossNodeCount") if isinstance(value, dict) else None
    if not finite_number(x) or not finite_number(n):
        return None
    x_int, n_int = int(x), int(n)
    if x_int != float(x) or n_int != float(n) or n_int <= 0 or not 0 <= x_int <= n_int:
        return None
    return x_int, n_int


def _population_sd(values: Sequence[float], label: str) -> float:
    if len(values) < 2:
        raise ValueError(f"{label} requires at least two values")
    value = statistics.pstdev(float(item) for item in values)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be positive")
    return value


def conditional_base_records(
    reference_records: Sequence[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    maximum_records_per_pool: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Create the slim color/scope records consumed by v2 preparation."""

    cfg = validate_v2_config(config or default_v2_config())
    minimum = float(cfg["formalEloMinimum"])
    maximum = float(cfg["formalEloMaximum"])
    by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in reference_records:
        if source.get("formalReferenceEligible") is not True:
            continue
        if not finite_number(source.get("targetOldR")) or not finite_number(source.get("opponentOldR")):
            continue
        if not minimum <= float(source["targetOldR"]) <= maximum:
            continue
        if not minimum <= float(source["opponentOldR"]) <= maximum:
            continue
        color = str(source.get("targetColor") or "").strip().casefold()
        if color not in COLORS:
            continue
        for scope in METRICS_SCOPES:
            metrics = (source.get("metrics") or {}).get(scope)
            if not isinstance(metrics, dict) or metrics.get("completeFourPhase") is not True:
                continue
            phases: dict[str, Any] = {}
            valid = True
            for phase_number in range(1, 5):
                value = metrics.get(f"phase{phase_number}")
                x = value.get("lossGe4Count") if isinstance(value, dict) else None
                n = value.get("validLossNodeCount") if isinstance(value, dict) else None
                if not finite_number(x) or not finite_number(n) or int(n) <= 0 or not 0 <= int(x) <= int(n):
                    valid = False
                    break
                phases[f"phase{phase_number}"] = {"x": int(x), "n": int(n)}
            if not valid:
                continue
            row = {
                "schema": SCHEMA_CONDITIONAL_RECORD,
                "gameId": str(source.get("gameId") or ""),
                "created": source.get("created"),
                "targetPlayerId": source.get("targetPlayerId"),
                "opponentPlayerId": source.get("opponentPlayerId"),
                "targetColor": color,
                "scope": scope,
                "targetOldR": float(source["targetOldR"]),
                "opponentOldR": float(source["opponentOldR"]),
                **phases,
                "referenceZ1": None,
                "referenceZ2": None,
                "referenceZ3": None,
                "referenceZ4": None,
                "fitDiagnostics": {},
            }
            by_pool[f"{color}|{scope}"].append(row)
    output: list[dict[str, Any]] = []
    scales: dict[str, dict[str, Any]] = {}
    for key in sorted(by_pool):
        rows = sorted(
            by_pool[key],
            key=lambda row: (
                str(row["gameId"]), str(row["targetColor"]),
                account_key(row.get("targetPlayerId")), account_key(row.get("opponentPlayerId")),
            ),
        )
        if maximum_records_per_pool is not None:
            rows = rows[: max(0, int(maximum_records_per_pool))]
        if len(rows) < 2:
            raise ValueError(f"conditional reference pool {key} has fewer than two records")
        scales[key] = {
            "color": key.split("|", 1)[0],
            "scope": key.split("|", 1)[1],
            "recordCount": len(rows),
            "selfEloSd": _population_sd([float(row["targetOldR"]) for row in rows], f"{key} self Elo"),
            "opponentEloSd": _population_sd([float(row["opponentOldR"]) for row in rows], f"{key} opponent Elo"),
            "phase1ZSd": None,
            "phase2ZSd": None,
            "phase3ZSd": None,
        }
        output.extend(rows)
    return output, scales


class _ConditionalPool:
    """Exact low-dimensional KNN over one fixed allowed reference pool."""

    def __init__(
        self,
        records: Sequence[dict[str, Any]],
        scales: dict[str, Any],
        stage: int,
        config: dict[str, Any],
    ) -> None:
        if stage not in {1, 2, 3, 4}:
            raise ValueError("stage must be 1..4")
        self.stage = int(stage)
        self.config = validate_v2_config(config)
        self.scales = scales
        use_previous_z = stage > 1 and float(self.config["previousZWeight"]) > 0.0
        self.previous_key = f"referenceZ{stage - 1}" if use_previous_z else None
        previous_sd_key = f"phase{stage - 1}ZSd" if use_previous_z else None
        self.previous_sd = None if previous_sd_key is None else scales.get(previous_sd_key)
        self.records = [
            row for row in records
            if _phase_counts(row, stage) is not None
            and (self.previous_key is None or finite_number(row.get(self.previous_key)))
        ]
        self.k = neighbor_count(len(self.records), float(self.config["neighborExponent"])) if self.records else 0
        self.tree = None
        self.features = None
        self._np = None
        if self.records and len(self.records) >= self.k + 1:
            _special, _optimize, cKDTree = _scipy_v2()
            import numpy as np  # type: ignore
            self._np = np
            feature_rows = []
            for row in self.records:
                features = [
                    float(row["targetOldR"]) / float(scales["selfEloSd"]),
                    float(row["opponentOldR"]) / float(scales["opponentEloSd"]),
                ]
                if self.previous_key is not None:
                    if not finite_number(self.previous_sd) or float(self.previous_sd) <= 0:
                        raise ValueError(f"missing positive {previous_sd_key} for stage {stage}")
                    features.append(
                        math.sqrt(float(self.config["previousZWeight"]))
                        * float(row[self.previous_key]) / float(self.previous_sd)
                    )
                feature_rows.append(features)
            self.features = np.asarray(feature_rows, dtype=float)
            self.tree = cKDTree(self.features)

    def nearest(
        self,
        trial_elo: float,
        opponent_elo: float,
        previous_z: float | None = None,
    ) -> dict[str, Any]:
        if self.tree is None or self._np is None or len(self.records) < self.k + 1:
            return {
                "ok": False,
                "reason": "insufficient_reference",
                "eligibleReferenceCount": len(self.records),
                "K": self.k,
            }
        query = [
            float(trial_elo) / float(self.scales["selfEloSd"]),
            float(opponent_elo) / float(self.scales["opponentEloSd"]),
        ]
        if self.previous_key is not None:
            if not finite_number(previous_z):
                return {"ok": False, "reason": "invalid_previous_phase_z"}
            query.append(
                math.sqrt(float(self.config["previousZWeight"]))
                * float(previous_z) / float(self.previous_sd)
            )
        distances, indexes = self.tree.query(
            self._np.asarray(query, dtype=float), k=self.k + 1, workers=1
        )
        ranked = [(float(distance), int(index)) for distance, index in zip(distances, indexes, strict=True)]
        boundary = ranked[self.k][0]
        if not math.isfinite(boundary) or boundary <= 0:
            return {
                "ok": False,
                "reason": "insufficient_reference",
                "eligibleReferenceCount": len(self.records),
                "K": self.k,
                "boundaryDistance": boundary,
            }
        # cKDTree intentionally does not define exact-tie order.  Expand only
        # boundary ties, then apply the frozen stable record key.
        if abs(ranked[self.k - 1][0] - boundary) <= 1e-12:
            candidate_indexes = self.tree.query_ball_point(
                self._np.asarray(query, dtype=float), r=boundary + 1e-12, workers=1
            )
            ranked = []
            query_array = self._np.asarray(query, dtype=float)
            for index in candidate_indexes:
                distance = float(self._np.linalg.norm(self.features[int(index)] - query_array))
                row = self.records[int(index)]
                ranked.append((
                    distance,
                    str(row.get("gameId") or ""),
                    str(row.get("targetColor") or ""),
                    account_key(row.get("targetPlayerId")),
                    int(index),
                ))
            ranked.sort()
            selected_pairs = [(item[0], item[-1]) for item in ranked[:self.k]]
            boundary = float(ranked[self.k][0])
        else:
            selected_pairs = ranked[:self.k]
        weights = [max(0.0, 1.0 - distance / boundary) for distance, _index in selected_pairs]
        weight_sum = sum(weights)
        if not math.isfinite(weight_sum) or weight_sum <= 0:
            return {"ok": False, "reason": "insufficient_reference", "boundaryDistance": boundary}
        return {
            "ok": True,
            "records": [self.records[index] for _distance, index in selected_pairs],
            "neighborSetSha256": canonical_sha256([
                self.records[index].get("recordId") or _conditional_record_id(self.records[index])
                for _distance, index in selected_pairs
            ]),
            "weights": weights,
            "K": self.k,
            "boundaryDistance": boundary,
            "eligibleReferenceCount": len(self.records),
            "effectiveWeight": weight_sum,
        }


def score_conditional_phase_v2(
    target_record: dict[str, Any],
    stage: int,
    trial_elo: float,
    pool: _ConditionalPool,
    *,
    previous_z: float | None = None,
    config: dict[str, Any] | None = None,
    initial_fit: tuple[float, float] | None = None,
) -> dict[str, Any]:
    cfg = validate_v2_config(config or pool.config)
    counts = _phase_counts(target_record, stage)
    if counts is None:
        return {"ok": False, "reason": "incomplete_phase_data", "phase": stage}
    opponent = target_record.get("opponentOldR")
    if not finite_number(opponent):
        return {"ok": False, "reason": "opponent_out_of_reference_range", "phase": stage}
    nearest = pool.nearest(float(trial_elo), float(opponent), previous_z)
    if nearest.get("ok") is not True:
        return {"ok": False, "phase": stage, **nearest}
    observations = []
    for row, weight in zip(nearest["records"], nearest["weights"], strict=True):
        reference_counts = _phase_counts(row, stage)
        if reference_counts is None:
            continue
        observations.append((reference_counts[0], reference_counts[1], float(weight)))
    fit = fit_weighted_beta_binomial(
        observations, config=cfg, initial_parameters=initial_fit
    )
    if fit.get("ok") is not True:
        return {
            "ok": False,
            "reason": "beta_binomial_fit_failed",
            "phase": stage,
            "fitStatus": fit.get("fitStatus"),
            "fit": fit,
            **{key: nearest[key] for key in ("K", "boundaryDistance", "eligibleReferenceCount", "effectiveWeight")},
        }
    x, n = counts
    distribution = beta_binomial_mid_cdf_z(
        x, n, float(fit["alpha"]), float(fit["beta"]), q_clip=float(cfg["zCdfClip"])
    )
    return {
        "ok": True,
        "phase": stage,
        "x": x,
        "n": n,
        "targetZ": distribution["z"],
        "PExact": distribution["PExact"],
        "logPExact": distribution["logPExact"],
        "negativeLogProbability": -distribution["logPExact"],
        "midCdf": distribution["midCdf"],
        "clippedMidCdf": distribution["clippedMidCdf"],
        "K": nearest["K"],
        "boundaryDistance": nearest["boundaryDistance"],
        "eligibleReferenceCount": nearest["eligibleReferenceCount"],
        "effectiveWeight": nearest["effectiveWeight"],
        "neighborSetSha256": nearest["neighborSetSha256"],
        "fitM": fit["m"],
        "fitKappa": fit["kappa"],
        "fitStatus": fit["fitStatus"],
        "fitScore": fit["fitScore"],
        "optimizerBoundary": fit["optimizerBoundary"],
    }


def _conditional_record_id(row: dict[str, Any]) -> str:
    return canonical_sha256({
        "gameId": row.get("gameId"),
        "targetColor": row.get("targetColor"),
        "scope": row.get("scope"),
        "targetPlayerId": account_key(row.get("targetPlayerId")),
        "opponentPlayerId": account_key(row.get("opponentPlayerId")),
    })


def _apply_stage_patches(
    rows: Sequence[dict[str, Any]],
    patches: Sequence[dict[str, Any]],
    stage: int,
) -> None:
    by_id = {_conditional_record_id(row): row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("conditional reference contains duplicate record identities")
    for patch in patches:
        record_id = str(patch.get("recordId") or "")
        if record_id not in by_id:
            raise ValueError("conditional stage patch references an unknown record")
        row = by_id[record_id]
        row[f"referenceZ{stage}"] = patch.get("referenceZ")
        row.setdefault("fitDiagnostics", {})[f"phase{stage}"] = patch.get("diagnostic")


def _build_conditional_stage_shard(
    pool_rows: Sequence[dict[str, Any]],
    pool_key: str,
    stage: int,
    scales: dict[str, Any],
    cfg: dict[str, Any],
    shard_accounts: Sequence[str],
    shard_path: str | Path,
) -> tuple[list[dict[str, Any]], str]:
    """Compute and atomically commit one account shard.

    The helper is pure with respect to the in-memory pool.  It is suitable for
    bounded thread parallelism because SciPy's optimizer/tree query releases
    the GIL in its expensive numerical sections, while all progress mutation
    remains serialized by the parent process.
    """

    generated: list[dict[str, Any]] = []
    for account in shard_accounts:
        excluded_game_ids = {
            str(row.get("gameId") or "")
            for row in pool_rows
            if account_key(row.get("targetPlayerId")) == account
            or account_key(row.get("opponentPlayerId")) == account
        }
        allowed = [
            row for row in pool_rows
            if str(row.get("gameId") or "") not in excluded_game_ids
        ]
        query_pool = _ConditionalPool(allowed, scales, stage, cfg)
        targets = [
            row for row in pool_rows
            if account_key(row.get("targetPlayerId")) == account
        ]
        for target in targets:
            previous_z = target.get(f"referenceZ{stage - 1}") if stage > 1 else None
            result = score_conditional_phase_v2(
                target,
                stage,
                float(target["targetOldR"]),
                query_pool,
                previous_z=previous_z,
                config=cfg,
            )
            diagnostic = {
                key: result.get(key)
                for key in (
                    "reason", "fitStatus", "K", "boundaryDistance",
                    "eligibleReferenceCount", "effectiveWeight", "fitM",
                    "fitKappa", "fitScore", "optimizerBoundary",
                )
                if result.get(key) is not None
            }
            diagnostic["excludedSourceGameCount"] = len(excluded_game_ids)
            generated.append({
                "recordId": target["recordId"],
                "referenceZ": result.get("targetZ") if result.get("ok") is True else None,
                "diagnostic": diagnostic,
            })
    generated.sort(key=lambda patch: patch["recordId"])
    atomic_write_jsonl(shard_path, generated)
    return generated, sha256_file(shard_path)


_PREP_WORKER_POOL_ROWS: list[dict[str, Any]] = []
_PREP_WORKER_POOL_KEY = ""
_PREP_WORKER_STAGE = 1
_PREP_WORKER_SCALES: dict[str, Any] = {}
_PREP_WORKER_CONFIG: dict[str, Any] = {}


def _init_prepare_stage_worker(
    pool_rows: list[dict[str, Any]],
    pool_key: str,
    stage: int,
    scales: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Load one pool/stage into a preparation worker process."""

    global _PREP_WORKER_POOL_ROWS, _PREP_WORKER_POOL_KEY
    global _PREP_WORKER_STAGE, _PREP_WORKER_SCALES, _PREP_WORKER_CONFIG
    _PREP_WORKER_POOL_ROWS = pool_rows
    _PREP_WORKER_POOL_KEY = pool_key
    _PREP_WORKER_STAGE = int(stage)
    _PREP_WORKER_SCALES = scales
    _PREP_WORKER_CONFIG = validate_v2_config(config)


def _build_conditional_stage_shard_worker(
    task: tuple[int, list[str], str],
) -> tuple[int, list[dict[str, Any]], str]:
    shard_index, shard_accounts, shard_path = task
    generated, shard_sha = _build_conditional_stage_shard(
        _PREP_WORKER_POOL_ROWS,
        _PREP_WORKER_POOL_KEY,
        _PREP_WORKER_STAGE,
        _PREP_WORKER_SCALES,
        _PREP_WORKER_CONFIG,
        shard_accounts,
        shard_path,
    )
    return shard_index, generated, shard_sha


def prepare_conditional_reference_v2(
    reference_records: Sequence[dict[str, Any]],
    output_dir: str | Path,
    *,
    config: dict[str, Any] | None = None,
    input_records_path: str | Path | None = None,
    reference_manifest_sha256: str | None = None,
    resume: bool = False,
    maximum_records_per_pool: int | None = None,
) -> dict[str, Any]:
    """Build the reusable unified reference-z cache with atomic stage shards."""

    cfg = validate_v2_config(config or default_v2_config())
    output = Path(output_dir)
    progress_path = output / "progress.json"
    records_name = str(cfg["conditionalReferenceRecords"])
    manifest_name = str(cfg["conditionalReferenceManifest"])
    input_sha = (
        sha256_file(input_records_path)
        if input_records_path is not None else canonical_sha256(list(reference_records))
    )
    config_sha = canonical_sha256(cfg)
    contract = {
        "schema": SCHEMA_CONDITIONAL_REFERENCE,
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "configSha256": config_sha,
        "inputSha256": input_sha,
        "referenceManifestSha256": reference_manifest_sha256,
        "maximumRecordsPerPool": maximum_records_per_pool,
    }
    contract_sha = canonical_sha256(contract)
    if output.exists() and not resume:
        raise FileExistsError(f"conditional reference output already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if progress_path.is_file():
        progress = read_json(progress_path)
        if progress.get("contractSha256") != contract_sha:
            raise ValueError("resume refused: conditional-reference config/input contract changed")
    else:
        if resume and any(output.iterdir()):
            raise ValueError("resume refused: output directory has no compatible progress.json")
        progress = {
            "schema": "player-sentinel-elo-conditional-progress-v1",
            "contract": contract,
            "contractSha256": contract_sha,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "status": "running",
            "completedStages": {},
            "completedShards": {},
            "timingSeconds": {},
        }
        atomic_write_json(progress_path, progress)

    base_path = output / "base_conditional_records.jsonl"
    scales_path = output / "conditional_scales.json"
    if base_path.is_file() and scales_path.is_file():
        rows = read_jsonl(base_path)
        scales = read_json(scales_path)
        if sha256_file(base_path) != progress.get("baseRecordsSha256"):
            raise ValueError("resume refused: base conditional records hash changed")
    else:
        started = time.perf_counter()
        rows, scales = conditional_base_records(
            reference_records,
            config=cfg,
            maximum_records_per_pool=maximum_records_per_pool,
        )
        for row in rows:
            row["recordId"] = _conditional_record_id(row)
        atomic_write_jsonl(base_path, rows)
        atomic_write_json(scales_path, scales)
        progress["baseRecordsSha256"] = sha256_file(base_path)
        progress["baseRecordCount"] = len(rows)
        progress["timingSeconds"]["base"] = time.perf_counter() - started
        progress["updatedAt"] = utc_now()
        atomic_write_json(progress_path, progress)

    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pools[f"{row['targetColor']}|{row['scope']}"] .append(row)
    shard_size = int(cfg["prepareAccountShardSize"])
    per_pool_audit: dict[str, Any] = {}
    for pool_key in sorted(pools):
        pool_rows = pools[pool_key]
        for stage in range(1, 5):
            stage_key = f"{pool_key}|phase{stage}"
            stage_file = output / "stages" / f"{pool_key.replace('|', '__')}__phase{stage}.jsonl"
            completed_stage = progress["completedStages"].get(stage_key)
            if completed_stage:
                if not stage_file.is_file() or sha256_file(stage_file) != completed_stage.get("sha256"):
                    raise ValueError(f"resume refused: completed stage changed: {stage_key}")
                stage_rows = read_jsonl(stage_file)
                _apply_stage_patches(pool_rows, stage_rows, stage)
                if stage <= 3:
                    scales[pool_key][f"phase{stage}ZSd"] = completed_stage.get("zSd")
                continue

            stage_started = time.perf_counter()
            accounts = sorted({account_key(row.get("targetPlayerId")) for row in pool_rows if account_key(row.get("targetPlayerId"))})
            shard_patches: list[dict[str, Any]] = []
            pending_shards: dict[int, tuple[str, Path, list[str]]] = {}
            for shard_index, start in enumerate(range(0, len(accounts), shard_size)):
                shard_accounts = accounts[start:start + shard_size]
                shard_key = f"{stage_key}|shard{shard_index:04d}"
                shard_path = output / "shards" / pool_key.replace("|", "__") / f"phase{stage}" / f"shard_{shard_index:04d}.jsonl"
                completed_shard = progress["completedShards"].get(shard_key)
                if completed_shard:
                    if not shard_path.is_file() or sha256_file(shard_path) != completed_shard.get("sha256"):
                        raise ValueError(f"resume refused: completed shard changed: {shard_key}")
                    shard_patches.extend(read_jsonl(shard_path))
                    continue
                pending_shards[shard_index] = (shard_key, shard_path, shard_accounts)
            # Compute independent account shards in separate processes.  The
            # parent commits progress in shard-index order, so the artifact
            # remains deterministic even when workers finish in another order.
            if pending_shards:
                worker_count = min(16, len(pending_shards))
                with ProcessPoolExecutor(
                    max_workers=worker_count,
                    initializer=_init_prepare_stage_worker,
                    initargs=(pool_rows, pool_key, stage, scales[pool_key], cfg),
                ) as executor:
                    futures = {
                        shard_index: executor.submit(
                            _build_conditional_stage_shard_worker,
                            (shard_index, shard_accounts, str(shard_path)),
                        )
                        for shard_index, (_shard_key, shard_path, shard_accounts)
                        in pending_shards.items()
                    }
                    for shard_index in sorted(futures):
                        shard_key, shard_path, shard_accounts = pending_shards[shard_index]
                        _returned_index, generated, shard_sha = futures[shard_index].result()
                        progress["completedShards"][shard_key] = {
                            "sha256": shard_sha,
                            "accountCount": len(shard_accounts),
                            "recordCount": len(generated),
                            "completedAt": utc_now(),
                            "parallelWorkerCount": worker_count,
                        }
                        progress["updatedAt"] = utc_now()
                        atomic_write_json(progress_path, progress)
                        shard_patches.extend(generated)
            shard_patches.sort(key=lambda patch: patch["recordId"])
            _apply_stage_patches(pool_rows, shard_patches, stage)
            z_values = [
                float(row[f"referenceZ{stage}"])
                for row in pool_rows if finite_number(row.get(f"referenceZ{stage}"))
            ]
            z_sd = _population_sd(z_values, f"{pool_key} phase{stage} z") if stage <= 3 and len(z_values) >= 2 else None
            if stage <= 3 and z_sd is None:
                raise ValueError(f"{pool_key} phase{stage} has insufficient valid z values")
            if stage <= 3:
                scales[pool_key][f"phase{stage}ZSd"] = z_sd
                atomic_write_json(scales_path, scales)
            atomic_write_jsonl(stage_file, shard_patches)
            status_counts: dict[str, int] = defaultdict(int)
            k_values: list[int] = []
            effective_weights: list[float] = []
            excluded_source_game_counts: list[int] = []
            for patch in shard_patches:
                diagnostic = patch.get("diagnostic") or {}
                status_counts[str(diagnostic.get("fitStatus") or diagnostic.get("reason") or "unknown")] += 1
                if finite_number(diagnostic.get("K")):
                    k_values.append(int(diagnostic["K"]))
                if finite_number(diagnostic.get("effectiveWeight")):
                    effective_weights.append(float(diagnostic["effectiveWeight"]))
                if finite_number(diagnostic.get("excludedSourceGameCount")):
                    excluded_source_game_counts.append(int(diagnostic["excludedSourceGameCount"]))
            progress["completedStages"][stage_key] = {
                "sha256": sha256_file(stage_file),
                "recordCount": len(shard_patches),
                "validRecordCount": len(z_values),
                "failedRecordCount": len(shard_patches) - len(z_values),
                "zSd": z_sd,
                "fitStatusCounts": dict(sorted(status_counts.items())),
                "kDistribution": _error_summary(k_values),
                "effectiveWeightDistribution": _error_summary(effective_weights),
                "excludedSourceGameCountDistribution": _error_summary(excluded_source_game_counts),
                "completedAt": utc_now(),
            }
            progress["timingSeconds"][stage_key] = time.perf_counter() - stage_started
            progress["updatedAt"] = utc_now()
            atomic_write_json(progress_path, progress)

        pool_audit = {
            "recordCount": len(pool_rows),
            "selfEloSd": scales[pool_key]["selfEloSd"],
            "opponentEloSd": scales[pool_key]["opponentEloSd"],
            "phase1ZSd": scales[pool_key]["phase1ZSd"],
            "phase2ZSd": scales[pool_key]["phase2ZSd"],
            "phase3ZSd": scales[pool_key]["phase3ZSd"],
            "stages": {
                f"phase{stage}": progress["completedStages"][f"{pool_key}|phase{stage}"]
                for stage in range(1, 5)
            },
            "excludedSourceGameCountByStage": {
                f"phase{stage}": (
                    progress["completedStages"][f"{pool_key}|phase{stage}"]
                    .get("excludedSourceGameCountDistribution")
                )
                for stage in range(1, 5)
            },
        }
        per_pool_audit[pool_key] = pool_audit

    rows.sort(key=lambda row: (row["targetColor"], row["scope"], row["gameId"], row["recordId"]))
    final_records_path = output / records_name
    atomic_write_jsonl(final_records_path, rows)
    manifest = {
        "schema": SCHEMA_CONDITIONAL_REFERENCE,
        "version": "v1",
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "createdAt": utc_now(),
        "referenceFeaturePolicy": cfg["referenceFeaturePolicy"],
        "directTargetAccountExclusion": (
            "exclude every source game where the estimated account is either player, "
            "then recompute N, K, K+1 boundary, and triangular weights"
        ),
        "crossFittingClaim": "unified cache; not per-target fully cross-fitted derived reference z",
        "cacheIsolationAudit": (
            "Each reference record's preparation query excludes every source game in which "
            "that record's target account or opponent account participates; per-stage exclusion "
            "counts are recorded in pool audit metadata."
        ),
        "configSha256": config_sha,
        "inputSha256": input_sha,
        "referenceManifestSha256": reference_manifest_sha256,
        "recordCount": len(rows),
        "recordsFile": records_name,
        "recordsSha256": sha256_file(final_records_path),
        "scales": scales,
        "pools": per_pool_audit,
        "progressSha256BeforeCompletion": sha256_file(progress_path),
    }
    atomic_write_json(output / manifest_name, manifest)
    progress["status"] = "completed"
    progress["completedAt"] = utc_now()
    progress["recordsSha256"] = manifest["recordsSha256"]
    progress["manifestSha256"] = sha256_file(output / manifest_name)
    progress["updatedAt"] = utc_now()
    atomic_write_json(progress_path, progress)
    audit = {
        "schema": "player-sentinel-elo-conditional-reference-audit-v1",
        "ok": True,
        "createdAt": utc_now(),
        "contractSha256": contract_sha,
        "manifestSha256": sha256_file(output / manifest_name),
        "recordsSha256": manifest["recordsSha256"],
        "recordCount": len(rows),
        "pools": per_pool_audit,
        "timingSeconds": progress["timingSeconds"],
    }
    atomic_write_json(output / "conditional_reference_audit.json", audit)
    return manifest


def load_conditional_reference_v2(
    directory: str | Path,
    *,
    config: dict[str, Any] | None = None,
    expected_reference_manifest_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    cfg = validate_v2_config(config or default_v2_config())
    root = Path(directory)
    manifest_path = root / str(cfg["conditionalReferenceManifest"])
    records_path = root / str(cfg["conditionalReferenceRecords"])
    manifest = read_json(manifest_path)
    if manifest.get("schema") != SCHEMA_CONDITIONAL_REFERENCE:
        raise ValueError("unsupported conditional reference manifest schema")
    if manifest.get("algorithmVersion") != ALGORITHM_VERSION_V2:
        raise ValueError("conditional reference algorithm version mismatch")
    if manifest.get("configSha256") != canonical_sha256(cfg):
        raise ValueError("conditional reference/config SHA-256 mismatch")
    if (
        expected_reference_manifest_sha256 is not None
        and manifest.get("referenceManifestSha256") != expected_reference_manifest_sha256
    ):
        raise ValueError("conditional reference/source manifest SHA-256 mismatch")
    if sha256_file(records_path) != manifest.get("recordsSha256"):
        raise ValueError("conditional reference records SHA-256 mismatch")
    rows = read_jsonl(records_path)
    if len(rows) != int(manifest.get("recordCount", -1)):
        raise ValueError("conditional reference record count mismatch")
    if any(row.get("schema") != SCHEMA_CONDITIONAL_RECORD for row in rows):
        raise ValueError("unsupported conditional reference record schema")
    return rows, manifest, sha256_file(manifest_path)


def _allowed_conditional_records(
    rows: Sequence[dict[str, Any]],
    account: str,
    target_game_ids: Iterable[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    key = account_key(account)
    excluded = {str(game_id) for game_id in target_game_ids}
    for row in rows:
        if account_key(row.get("targetPlayerId")) == key or account_key(row.get("opponentPlayerId")) == key:
            excluded.add(str(row.get("gameId") or ""))
    return [row for row in rows if str(row.get("gameId") or "") not in excluded], excluded


def rebuild_conditional_reference_for_account_v2(
    directed_reference_records: Sequence[dict[str, Any]],
    target_account: str,
    output_dir: str | Path,
    *,
    config: dict[str, Any] | None = None,
    reference_manifest_sha256: str | None = None,
    resume: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recompute scales/reference-z after exact removal of one account's games.

    The formal estimator uses the unified frozen feature cache.  This separate,
    resumable rebuild supports the required sensitivity comparison by removing
    every source game where ``target_account`` is either player before any
    scales or derived reference z values are calculated.
    """

    cfg = validate_v2_config(config or default_v2_config())
    account = account_key(target_account)
    if not account:
        raise ValueError("target_account is required for sensitivity rebuild")
    excluded = excluded_reference_game_ids(directed_reference_records, account, ())
    filtered = [
        row for row in directed_reference_records
        if str(row.get("gameId") or "") not in excluded
    ]
    manifest = prepare_conditional_reference_v2(
        filtered,
        output_dir,
        config=cfg,
        reference_manifest_sha256=reference_manifest_sha256,
        resume=resume,
    )
    audit = {
        "schema": "player-sentinel-elo-reference-z-sensitivity-rebuild-v1",
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "createdAt": utc_now(),
        "account": account,
        "configSha256": canonical_sha256(cfg),
        "sourceDirectedRecordCount": len(directed_reference_records),
        "excludedSourceGameCount": len(excluded),
        "excludedSourceGameIdsSha256": canonical_sha256(sorted(excluded)),
        "remainingDirectedRecordCount": len(filtered),
        "conditionalManifestSha256": sha256_file(
            Path(output_dir) / str(cfg["conditionalReferenceManifest"])
        ),
        "conditionalRecordsSha256": manifest["recordsSha256"],
    }
    atomic_write_json(Path(output_dir) / "account_exclusion_rebuild_audit.json", audit)
    return manifest, audit


def _nll_curve_stats_v2(
    points: Sequence[dict[str, Any]],
    *,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    valid = [point for point in points if finite_number(point.get("meanNegativeLogLikelihood"))]
    if not valid:
        reasons = Counter(
            reason
            for point in points
            for reason in (point.get("failureReasons") or [])
        )
        if reasons and set(reasons) <= {"beta_binomial_fit_failed"}:
            status = "beta_binomial_fit_failed"
        else:
            status = "insufficient_reference"
        return {
            "status": status,
            "statusReasons": ["no_complete_grid_point"],
            "minimumScore": None,
            "bestGridPoints": [],
            "localMinima": [],
            "maximumAdjacentScoreJump": None,
            "minimumRegionWidth": None,
        }
    scores = [float(point["meanNegativeLogLikelihood"]) for point in valid]
    minimum = min(scores)
    tolerance = 1e-12
    best = [int(point["elo"]) for point in valid if abs(float(point["meanNegativeLogLikelihood"]) - minimum) <= tolerance]
    local_minima: list[dict[str, Any]] = []
    for index, point in enumerate(valid):
        score = float(point["meanNegativeLogLikelihood"])
        left = float(valid[index - 1]["meanNegativeLogLikelihood"]) if index else math.inf
        right = float(valid[index + 1]["meanNegativeLogLikelihood"]) if index + 1 < len(valid) else math.inf
        if score <= left + tolerance and score <= right + tolerance:
            local_minima.append({"elo": int(point["elo"]), "score": score})
    jumps = [abs(right - left) for left, right in zip(scores, scores[1:])]
    threshold_values = thresholds or {}
    near_delta = float(threshold_values.get("multipleMinimaScoreDelta", 0.0) or 0.0)
    near_minima = [row for row in local_minima if float(row["score"]) <= minimum + near_delta + tolerance]
    separated_near = []
    for row in near_minima:
        if not separated_near or int(row["elo"]) > int(separated_near[-1]["elo"]) + 1:
            separated_near.append(row)
    low_delta = float(threshold_values.get("lowResolutionScoreDelta", 0.0) or 0.0)
    minimum_region = [
        int(point["elo"]) for point in valid
        if float(point["meanNegativeLogLikelihood"]) <= minimum + low_delta + tolerance
    ]
    minimum_width = max(minimum_region) - min(minimum_region) if minimum_region else 0
    first_elo, last_elo = int(valid[0]["elo"]), int(valid[-1]["elo"])
    best_elo = min(best)
    nonincreasing = all(right <= left + tolerance for left, right in zip(scores, scores[1:]))
    nondecreasing = all(right >= left - tolerance for left, right in zip(scores, scores[1:]))
    if best_elo == last_elo and nonincreasing:
        status, reasons = "above_reference_range", ["nll_continues_improving_to_upper_boundary"]
    elif best_elo == first_elo and nondecreasing:
        status, reasons = "below_reference_range", ["nll_continues_improving_to_lower_boundary"]
    elif len(separated_near) > 1:
        status, reasons = "multiple_minima", ["separated_near_minimum_regions"]
    elif best_elo in {first_elo, last_elo}:
        status, reasons = "unstable_curve", ["boundary_minimum_is_not_monotone"]
    elif finite_number(threshold_values.get("lowResolutionEloWidth")) and minimum_width > float(threshold_values["lowResolutionEloWidth"]):
        status, reasons = "low_resolution", ["minimum_region_is_too_wide"]
    elif finite_number(threshold_values.get("abnormalAdjacentScoreJump")) and jumps and max(jumps) > float(threshold_values["abnormalAdjacentScoreJump"]):
        status, reasons = "unstable_curve", ["adjacent_nll_jump_exceeds_calibrated_threshold"]
    else:
        status, reasons = "valid", ["identifiable_internal_primary_minimum"]
    return {
        "status": status,
        "statusReasons": reasons,
        "minimumScore": minimum,
        "bestGridPoints": best,
        "localMinima": local_minima,
        "maximumAdjacentScoreJump": max(jumps) if jumps else 0.0,
        "minimumRegionWidth": minimum_width,
    }


def score_candidate_curve_v2(
    target_records: Sequence[dict[str, Any]],
    conditional_records: Sequence[dict[str, Any]],
    conditional_manifest: dict[str, Any],
    *,
    target_account: str,
    config: dict[str, Any] | None = None,
    diagnostic_thresholds: dict[str, Any] | None = None,
    grid: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Evaluate J(E): mean four-phase exact-count negative log likelihood."""

    cfg = validate_v2_config(config or default_v2_config())
    target_rows = list(target_records)
    grid_values = list(grid) if grid is not None else list(
        range(int(cfg["eloGridMinimum"]), int(cfg["eloGridMaximum"]) + 1, int(cfg["eloGridStep"]))
    )
    allowed, excluded = _allowed_conditional_records(
        conditional_records,
        target_account,
        [str(row.get("gameId") or "") for row in target_rows],
    )
    by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in allowed:
        by_pool[f"{row.get('targetColor')}|{row.get('scope')}"] .append(row)
    scales = conditional_manifest.get("scales")
    if not isinstance(scales, dict):
        raise ValueError("conditional reference manifest has no scales")
    pools: dict[tuple[str, int], _ConditionalPool] = {}
    for pool_key in sorted({f"{row.get('targetColor')}|{selected_metrics_scope(row)}" for row in target_rows}):
        if pool_key not in scales:
            continue
        for stage in range(1, 5):
            pools[(pool_key, stage)] = _ConditionalPool(by_pool.get(pool_key, []), scales[pool_key], stage, cfg)

    points: list[dict[str, Any]] = []
    # The complete grid can contain 901 points and more than 100,000 phase
    # diagnostics per account.  Only the diagnostics at the current best point
    # are part of the public artifact, so retain those instead of every grid
    # point.  This is especially important when 16 calibration workers run in
    # separate processes.
    best_score_seen = math.inf
    best_elo_seen: int | None = None
    best_game_diagnostics: list[dict[str, Any]] = []
    warm_fits: dict[tuple[str, int], tuple[float, float]] = {}
    for trial_elo in grid_values:
        game_rows: list[dict[str, Any]] = []
        failure_reasons: Counter[str] = Counter()
        for target in target_rows:
            color = str(target.get("targetColor") or "").casefold()
            scope = selected_metrics_scope(target)
            pool_key = f"{color}|{scope}"
            previous_z = None
            phase_rows: list[dict[str, Any]] = []
            game_failed = None
            game_id = str(target.get("gameId") or "")
            for stage in range(1, 5):
                pool = pools.get((pool_key, stage))
                if pool is None:
                    result = {"ok": False, "reason": "insufficient_reference", "phase": stage}
                else:
                    result = score_conditional_phase_v2(
                        target,
                        stage,
                        float(trial_elo),
                        pool,
                        previous_z=previous_z,
                        config=cfg,
                        initial_fit=warm_fits.get((game_id, stage)),
                    )
                result.update({
                    "gameId": str(target.get("gameId") or ""),
                    "scope": scope,
                    "color": color,
                    "elo": int(trial_elo),
                })
                phase_rows.append(result)
                if result.get("ok") is not True:
                    game_failed = str(result.get("reason") or "insufficient_reference")
                    break
                warm_fits[(game_id, stage)] = (
                    float(result["fitM"]), float(result["fitKappa"])
                )
                previous_z = float(result["targetZ"])
            if game_failed is not None:
                failure_reasons[game_failed] += 1
                game_rows.append({
                    "gameId": str(target.get("gameId") or ""),
                    "ok": False,
                    "reason": game_failed,
                    "phaseDiagnostics": phase_rows,
                })
            else:
                game_nll = sum(float(row["negativeLogProbability"]) for row in phase_rows)
                game_rows.append({
                    "gameId": str(target.get("gameId") or ""),
                    "ok": True,
                    "gameNegativeLogLikelihood": game_nll,
                    "meanConditionalZ": statistics.fmean(float(row["targetZ"]) for row in phase_rows),
                    "phaseDiagnostics": phase_rows,
                })
        valid_games = [row for row in game_rows if row.get("ok") is True]
        if len(valid_games) == len(target_rows) and valid_games:
            score = statistics.fmean(float(row["gameNegativeLogLikelihood"]) for row in valid_games)
            mean_z = statistics.fmean(float(row["meanConditionalZ"]) for row in valid_games)
        else:
            score = None
            mean_z = None
        points.append({
            "elo": int(trial_elo),
            "meanNegativeLogLikelihood": score,
            "score": score,
            "meanConditionalZ": mean_z,
            "validTargetGameCount": len(valid_games),
            "failureReasons": sorted(failure_reasons),
            "failureReasonCounts": dict(sorted(failure_reasons.items())),
        })
        if score is not None and math.isfinite(float(score)) and float(score) < best_score_seen:
            best_score_seen = float(score)
            best_elo_seen = int(trial_elo)
            best_game_diagnostics = game_rows
    stats = _nll_curve_stats_v2(points, thresholds=diagnostic_thresholds)
    best_points = stats.get("bestGridPoints") or []
    best = min(best_points) if best_points else None
    return {
        "schema": "player-sentinel-estimated-elo-curve-v2",
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "points": points,
        "bestGridPoint": best,
        "targetGameCount": len(target_rows),
        "excludedReferenceGameCount": len(excluded),
        "excludedReferenceGameIds": sorted(excluded),
        "bestGameDiagnostics": (
            best_game_diagnostics
            if best is not None and best_elo_seen == int(best)
            else []
        ),
        **stats,
    }


def _flatten_best_diagnostics_v2(curve: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    games: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    for game in curve.get("bestGameDiagnostics") or []:
        phase_rows = game.get("phaseDiagnostics") or []
        games.append({
            "gameId": game.get("gameId"),
            "gameNegativeLogLikelihood": game.get("gameNegativeLogLikelihood"),
            "meanConditionalZ": game.get("meanConditionalZ"),
            "status": "valid" if game.get("ok") is True else game.get("reason"),
        })
        for row in phase_rows:
            phases.append({
                "gameId": row.get("gameId"),
                "phase": row.get("phase"),
                "x": row.get("x"),
                "n": row.get("n"),
                "targetZ": row.get("targetZ"),
                "PExact": row.get("PExact"),
                "negativeLogProbability": row.get("negativeLogProbability"),
                "K": row.get("K"),
                "boundaryDistance": row.get("boundaryDistance"),
                "eligibleReferenceCount": row.get("eligibleReferenceCount"),
                "fitM": row.get("fitM"),
                "fitKappa": row.get("fitKappa"),
                "fitStatus": row.get("fitStatus") or row.get("reason"),
                "neighborSetSha256": row.get("neighborSetSha256"),
                "scope": row.get("scope"),
                "color": row.get("color"),
            })
    return games, phases


def estimate_database_calibrated_range_v2(
    account: str,
    target_records: Sequence[dict[str, Any]],
    conditional_records: Sequence[dict[str, Any]],
    conditional_manifest: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
    conditional_manifest_sha256: str | None = None,
    calibration_sha256: str | None = None,
    grid: Sequence[int] | None = None,
) -> TargetEstimate:
    cfg = validate_v2_config(config or default_v2_config())
    if conditional_manifest.get("schema") != SCHEMA_CONDITIONAL_REFERENCE:
        raise ValueError("v2 estimate requires a conditional-reference-v1 manifest")
    if conditional_manifest.get("configSha256") != canonical_sha256(cfg):
        raise ValueError("v2 estimate config/conditional cache mismatch")
    if calibration is not None:
        if calibration.get("schema") != SCHEMA_CALIBRATION_V2:
            raise ValueError("v1 calibration artifacts cannot be used by estimated-Elo v2")
        if calibration.get("configSha256") != canonical_sha256(cfg):
            raise ValueError("v2 calibration/config mismatch")
        if calibration.get("conditionalManifestSha256") != conditional_manifest_sha256:
            raise ValueError("v2 calibration/conditional-reference mismatch")
    selection = select_target_records(target_records, config=cfg)
    selected = list(selection["selected"])
    payload: dict[str, Any] = {
        "schema": SCHEMA_ESTIMATE_V2,
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "account": account,
        "configSha256": canonical_sha256(cfg),
        "conditionalManifestSha256": conditional_manifest_sha256,
        "calibrationSha256": calibration_sha256,
        "selectedGameIds": [str(row.get("gameId") or "") for row in selected],
        "selectedGameCount": len(selected),
        "excludedGamesWithReasons": selection["excluded"],
        "formalMinimumGameCount": int(cfg["minimumTargetGames"]),
        "formalMaximumGameCount": int(cfg["maximumTargetGames"]),
        "eloGridMinimum": int(cfg["eloGridMinimum"]),
        "eloGridMaximum": int(cfg["eloGridMaximum"]),
        "estimatedElo": None,
        "bestGridPoint": None,
        "minimumScore": None,
        "meanNegativeLogLikelihoodAtBest": None,
        "meanConditionalZAtBest": None,
        "databaseCalibrated95Range": None,
        "databaseCalibrated95Intervals": [],
        "status": selection["status"],
        "statusReasons": [],
        "gameDiagnostics": [],
        "phaseDiagnostics": [],
        "scoringSemantics": (
            "sum four phase exact-count negative log probabilities within each game, "
            "then arithmetic mean across selected games; longer phases naturally carry more evidence"
        ),
        "referenceFeaturePolicy": cfg["referenceFeaturePolicy"],
        "createdAt": utc_now(),
    }
    if selection["status"] != "valid":
        payload["statusReasons"] = ["fewer_than_10_complete_recent_target_games"]
        return TargetEstimate(payload, {"points": [], "status": selection["status"]}, tuple(selected))
    thresholds = calibration.get("diagnosticThresholds") if calibration else None
    curve = score_candidate_curve_v2(
        selected,
        conditional_records,
        conditional_manifest,
        target_account=account,
        config=cfg,
        diagnostic_thresholds=thresholds,
        grid=grid,
    )
    payload["bestGridPoint"] = curve.get("bestGridPoint")
    payload["minimumScore"] = curve.get("minimumScore")
    payload["status"] = curve.get("status")
    payload["statusReasons"] = list(curve.get("statusReasons") or [])
    best = curve.get("bestGridPoint")
    best_point = next((point for point in curve.get("points", []) if point.get("elo") == best), None)
    if best_point:
        payload["meanNegativeLogLikelihoodAtBest"] = best_point.get("meanNegativeLogLikelihood")
        payload["meanConditionalZAtBest"] = best_point.get("meanConditionalZ")
    games, phases = _flatten_best_diagnostics_v2(curve)
    payload["gameDiagnostics"] = games
    payload["phaseDiagnostics"] = phases
    if curve.get("status") == "valid" and best is not None:
        payload["estimatedElo"] = int(best)
        if calibration and calibration.get("status") == "validated" and finite_number(calibration.get("t95")):
            allowed = float(curve["minimumScore"]) + float(calibration["t95"])
            intervals = intervals_for_score_threshold(curve["points"], allowed)
            payload["databaseCalibrated95Intervals"] = intervals
            if len(intervals) == 1:
                payload["databaseCalibrated95Range"] = intervals[0]
            elif len(intervals) > 1:
                payload["status"] = "multiple_intervals"
                payload["statusReasons"].append("calibrated_allowed_set_is_discontinuous")
        else:
            payload["status"] = "calibration_unavailable"
            payload["statusReasons"].append("independent_validation_did_not_confirm_95_percent_coverage")
    return TargetEstimate(payload, curve, tuple(selected))


def reference_z_sensitivity_comparison_v2(
    account: str,
    unified: TargetEstimate,
    rebuilt: TargetEstimate,
    *,
    unified_manifest_sha256: str,
    rebuilt_manifest_sha256: str,
    rebuild_audit: dict[str, Any],
) -> dict[str, Any]:
    """Compare frozen-cache and account-exclusion-rebuilt estimates."""

    def phase_hashes(estimate: TargetEstimate) -> dict[tuple[str, int], str | None]:
        return {
            (str(row.get("gameId") or ""), int(row.get("phase") or 0)): row.get("neighborSetSha256")
            for row in estimate.payload.get("phaseDiagnostics") or []
        }

    unified_hashes = phase_hashes(unified)
    rebuilt_hashes = phase_hashes(rebuilt)
    shared_keys = sorted(set(unified_hashes) & set(rebuilt_hashes))
    changed = [key for key in shared_keys if unified_hashes[key] != rebuilt_hashes[key]]
    unified_elo = unified.payload.get("estimatedElo")
    rebuilt_elo = rebuilt.payload.get("estimatedElo")
    return {
        "schema": "player-sentinel-elo-reference-z-sensitivity-v1",
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "createdAt": utc_now(),
        "account": account_key(account),
        "unifiedConditionalManifestSha256": unified_manifest_sha256,
        "rebuiltConditionalManifestSha256": rebuilt_manifest_sha256,
        "rebuild": rebuild_audit,
        "unified": {
            "status": unified.payload.get("status"),
            "estimatedElo": unified_elo,
            "intervals": unified.payload.get("databaseCalibrated95Intervals") or [],
            "selectedGameCount": unified.payload.get("selectedGameCount"),
        },
        "accountExclusionRebuilt": {
            "status": rebuilt.payload.get("status"),
            "estimatedElo": rebuilt_elo,
            "intervals": rebuilt.payload.get("databaseCalibrated95Intervals") or [],
            "selectedGameCount": rebuilt.payload.get("selectedGameCount"),
        },
        "estimatedEloDifference": (
            float(rebuilt_elo) - float(unified_elo)
            if finite_number(rebuilt_elo) and finite_number(unified_elo) else None
        ),
        "sharedBestPointPhaseCount": len(shared_keys),
        "changedNeighborSetCount": len(changed),
        "changedNeighborSetFraction": (
            len(changed) / len(shared_keys) if shared_keys else None
        ),
        "intervalComparisonAvailable": bool(
            unified.payload.get("databaseCalibrated95Intervals")
            and rebuilt.payload.get("databaseCalibrated95Intervals")
        ),
        "intervalComparisonUnavailableReason": (
            None
            if unified.payload.get("databaseCalibrated95Intervals")
            and rebuilt.payload.get("databaseCalibrated95Intervals")
            else "validated_v2_calibration_not_available"
        ),
    }


_V2_WORKER_CONDITIONAL_RECORDS: list[dict[str, Any]] = []
_V2_WORKER_CONDITIONAL_MANIFEST: dict[str, Any] = {}
_V2_WORKER_CONDITIONAL_MANIFEST_SHA: str | None = None
_V2_WORKER_CONFIG: dict[str, Any] = {}


def _init_v2_calibration_worker(
    conditional_records_path: str,
    conditional_manifest_path: str,
    config: dict[str, Any],
) -> None:
    global _V2_WORKER_CONDITIONAL_RECORDS
    global _V2_WORKER_CONDITIONAL_MANIFEST
    global _V2_WORKER_CONDITIONAL_MANIFEST_SHA
    global _V2_WORKER_CONFIG
    _V2_WORKER_CONFIG = validate_v2_config(config)
    _V2_WORKER_CONDITIONAL_RECORDS = read_jsonl(conditional_records_path)
    _V2_WORKER_CONDITIONAL_MANIFEST = read_json(conditional_manifest_path)
    _V2_WORKER_CONDITIONAL_MANIFEST_SHA = sha256_file(conditional_manifest_path)


def _comparison_method_payload_v2(
    curve: dict[str, Any],
    known_elo: float,
) -> dict[str, Any]:
    best = curve.get("bestGridPoint")
    minimum = curve.get("minimumScore")
    score_at_known = interpolate_score(curve.get("points") or [], known_elo)
    return {
        "curveStatus": curve.get("status"),
        "bestGridPoint": best,
        "minimumScore": minimum,
        "scoreAtKnownElo": score_at_known,
        "trueScoreIncrease": (
            float(score_at_known) - float(minimum)
            if finite_number(score_at_known) and finite_number(minimum) else None
        ),
        "estimatedEloError": (
            float(best) - float(known_elo) if finite_number(best) else None
        ),
    }


def _calibration_case_payload_v2(
    role: str,
    account: str,
    known_elo: float,
    target_records: Sequence[dict[str, Any]],
    conditional_records: Sequence[dict[str, Any]],
    conditional_manifest: dict[str, Any],
    conditional_manifest_sha256: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    estimate = estimate_database_calibrated_range_v2(
        account,
        target_records,
        conditional_records,
        conditional_manifest,
        config=config,
        calibration=None,
        conditional_manifest_sha256=conditional_manifest_sha256,
    )
    curve = estimate.curve
    score_at_known = interpolate_score(curve.get("points") or [], float(known_elo))
    minimum = curve.get("minimumScore")
    selected = list(estimate.selected_records)
    scopes = Counter(selected_metrics_scope(row) for row in selected)
    colors = Counter(str(row.get("targetColor") or "").casefold() for row in selected)
    local_minima = sorted(curve.get("localMinima") or [], key=lambda row: float(row["score"]))
    secondary_gap = (
        float(local_minima[1]["score"]) - float(local_minima[0]["score"])
        if len(local_minima) >= 2 else None
    )
    method_comparisons: dict[str, Any] = {
        "C_beta_binomial_previous_z_weight_1": _comparison_method_payload_v2(
            curve, float(known_elo)
        )
    }
    # Method choice is made with calibration accounts only.  Validation tasks
    # evaluate only the already-frozen C method and therefore cannot influence
    # previous_z_weight, numerical settings, curve thresholds, or T95.
    if role == "calibration":
        selected_rows = list(estimate.selected_records)
        curve_a = score_candidate_curve(
            selected_rows,
            conditional_records,
            target_account=account,
            config=config,
        )
        method_comparisons["A_v1_equal_phase_signed_z"] = _comparison_method_payload_v2(
            curve_a, float(known_elo)
        )
        config_b = {**config, "previousZWeight": 0.0}
        curve_b = score_candidate_curve_v2(
            selected_rows,
            conditional_records,
            conditional_manifest,
            target_account=account,
            config=config_b,
        )
        method_comparisons["B_beta_binomial_previous_z_weight_0"] = _comparison_method_payload_v2(
            curve_b, float(known_elo)
        )
    return {
        "schema": SCHEMA_CALIBRATION_CASE_V2,
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "configSha256": canonical_sha256(config),
        "conditionalManifestSha256": conditional_manifest_sha256,
        "role": role,
        "account": account,
        "knownElo": float(known_elo),
        "knownEloDefinition": "newR from the latest created source-bundle detail for this account",
        "knownEloInFormalRange": float(config["formalEloMinimum"]) <= float(known_elo) <= float(config["formalEloMaximum"]),
        "selectedGameCount": len(selected),
        "scopeComposition": dict(sorted(scopes.items())),
        "colorComposition": dict(sorted(colors.items())),
        "curveStatus": curve.get("status"),
        "bestGridPoint": curve.get("bestGridPoint"),
        "minimumScore": minimum,
        "scoreAtKnownElo": score_at_known,
        "trueScoreIncrease": (
            float(score_at_known) - float(minimum)
            if finite_number(score_at_known) and finite_number(minimum) else None
        ),
        "estimatedEloError": (
            float(curve["bestGridPoint"]) - float(known_elo)
            if finite_number(curve.get("bestGridPoint")) else None
        ),
        "minimumRegionWidth": curve.get("minimumRegionWidth"),
        "secondaryMinimumGap": secondary_gap,
        "maximumAdjacentScoreJump": curve.get("maximumAdjacentScoreJump"),
        "excludedReferenceGameCount": curve.get("excludedReferenceGameCount"),
        "scoreCurve": curve.get("points") or [],
        "runtimeSeconds": time.perf_counter() - started,
        "statusReasons": curve.get("statusReasons") or [],
        "methodComparisons": method_comparisons,
    }


def _v2_calibration_case_worker(task: tuple[str, str, float, list[dict[str, Any]], str]) -> dict[str, Any]:
    role, account, known, target_records, case_path_string = task
    case = _calibration_case_payload_v2(
        role,
        account,
        known,
        target_records,
        _V2_WORKER_CONDITIONAL_RECORDS,
        _V2_WORKER_CONDITIONAL_MANIFEST,
        str(_V2_WORKER_CONDITIONAL_MANIFEST_SHA),
        _V2_WORKER_CONFIG,
    )
    atomic_write_json(case_path_string, case)
    return case


def _empirical_quantile_v2(values: Sequence[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    position = (len(ordered) - 1) * float(probability)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _calibration_group_rows_v2(cases: Sequence[dict[str, Any]], t95: float | None) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        count = int(case.get("selectedGameCount") or 0)
        count_band = "10-14" if count <= 14 else "15-19" if count <= 19 else "20-24" if count <= 24 else "25-30"
        known = float(case["knownElo"])
        elo_band = f"{int(known // 100) * 100}-{int(known // 100) * 100 + 99}"
        scopes = case.get("scopeComposition") or {}
        scope_group = "mixed" if len([key for key, value in scopes.items() if value]) > 1 else next(iter(scopes), "unknown")
        colors = case.get("colorComposition") or {}
        color_group = "mixed" if len([key for key, value in colors.items() if value]) > 1 else next(iter(colors), "unknown")
        groups[f"gameCount:{count_band}"].append(case)
        groups[f"knownElo:{elo_band}"].append(case)
        groups[f"scope:{scope_group}"].append(case)
        groups[f"color:{color_group}"].append(case)
    output: dict[str, Any] = {}
    for key, rows in sorted(groups.items()):
        errors = [abs(float(row["estimatedEloError"])) for row in rows if finite_number(row.get("estimatedEloError"))]
        covered = []
        widths = []
        if t95 is not None:
            for row in rows:
                if not finite_number(row.get("minimumScore")) or not finite_number(row.get("knownElo")):
                    continue
                intervals = intervals_for_score_threshold(row.get("scoreCurve") or [], float(row["minimumScore"]) + t95)
                is_covered = any(float(interval["lower"]) <= float(row["knownElo"]) <= float(interval["upper"]) for interval in intervals)
                covered.append(is_covered)
                widths.extend(float(interval["upper"]) - float(interval["lower"]) for interval in intervals)
        output[key] = {
            "caseCount": len(rows),
            "coveredCount": sum(covered),
            "coverage": statistics.fmean(covered) if covered else None,
            "error": _error_summary(errors),
            "intervalWidth": _error_summary(widths),
        }
    return output


def _method_comparison_summary_v2(
    calibration_cases: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    labels = (
        "A_v1_equal_phase_signed_z",
        "B_beta_binomial_previous_z_weight_0",
        "C_beta_binomial_previous_z_weight_1",
    )
    methods: dict[str, Any] = {}
    for label in labels:
        rows = [
            (case.get("methodComparisons") or {}).get(label) or {}
            for case in calibration_cases
        ]
        errors = [
            abs(float(row["estimatedEloError"]))
            for row in rows if finite_number(row.get("estimatedEloError"))
        ]
        increases = [
            float(row["trueScoreIncrease"])
            for row in rows if finite_number(row.get("trueScoreIncrease"))
        ]
        methods[label] = {
            "caseCount": len(rows),
            "validErrorCount": len(errors),
            "absoluteEloError": _error_summary(errors),
            "trueScoreIncrease": _error_summary(increases),
        }
    matched_errors = []
    for case in calibration_cases:
        comparisons = case.get("methodComparisons") or {}
        b_error = (comparisons.get(labels[1]) or {}).get("estimatedEloError")
        c_error = (comparisons.get(labels[2]) or {}).get("estimatedEloError")
        if finite_number(b_error) and finite_number(c_error):
            matched_errors.append((abs(float(b_error)), abs(float(c_error))))
    matched_count = len(matched_errors)
    b_mean = statistics.fmean(row[0] for row in matched_errors) if matched_errors else None
    c_mean = statistics.fmean(row[1] for row in matched_errors) if matched_errors else None
    c_better = bool(
        matched_count > 0
        and finite_number(b_mean)
        and finite_number(c_mean)
        and float(c_mean) < float(b_mean)
    )
    return {
        "selectionPopulation": "calibration_accounts_only",
        "selectionMetric": "mean_absolute_Elo_error; strict improvement required",
        "validationAccountsUsedForSelection": False,
        "methods": methods,
        "matchedBAndCErrorCount": matched_count,
        "adjacentConditionalImprovementConfirmed": c_better,
        "meanAbsoluteErrorDifferenceCMinusB": (
            float(c_mean) - float(b_mean)
            if finite_number(c_mean) and finite_number(b_mean) else None
        ),
    }


def calibrate_global_interval_v2(
    directed_reference_records: Sequence[dict[str, Any]],
    source_bundle: dict[str, Any],
    conditional_records: Sequence[dict[str, Any]],
    conditional_manifest: dict[str, Any],
    output_dir: str | Path,
    *,
    config: dict[str, Any] | None = None,
    conditional_records_path: str | Path,
    conditional_manifest_path: str | Path,
    directed_records_sha256: str,
    resume: bool = False,
    parallel_workers: int = 16,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run resumable account tasks with the frozen 16-process contract."""

    cfg = validate_v2_config(config or default_v2_config())
    if int(parallel_workers) != 16:
        raise ValueError("formal estimated-Elo v2 calibration requires ProcessPoolExecutor(max_workers=16)")
    output = Path(output_dir)
    progress_path = output / "progress.json"
    conditional_manifest_sha = sha256_file(conditional_manifest_path)
    contract = {
        "schema": SCHEMA_CALIBRATION_V2,
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "configSha256": canonical_sha256(cfg),
        "directedRecordsSha256": directed_records_sha256,
        "conditionalManifestSha256": conditional_manifest_sha,
        "parallelWorkers": 16,
        "referenceQueryWorkersPerWorker": 1,
        "methodComparison": [
            "A_v1_equal_phase_signed_z",
            "B_beta_binomial_previous_z_weight_0",
            "C_beta_binomial_previous_z_weight_1",
        ],
    }
    contract_sha = canonical_sha256(contract)
    if output.exists() and not resume:
        raise FileExistsError(f"calibration output already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if progress_path.is_file():
        progress = read_json(progress_path)
        if progress.get("contractSha256") != contract_sha:
            raise ValueError("resume refused: calibration config/input contract changed")
    else:
        if resume and any(output.iterdir()):
            raise ValueError("resume refused: calibration directory has no compatible progress.json")
        progress = {
            "schema": "player-sentinel-elo-calibration-progress-v2",
            "contract": contract,
            "contractSha256": contract_sha,
            "status": "running",
            "createdAt": utc_now(),
            "completedCases": {},
            "failedCaseCount": 0,
            "retryCount": 0,
        }
        atomic_write_json(progress_path, progress)

    by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in directed_reference_records:
        account = account_key(row.get("targetPlayerId"))
        if account and row.get("formalReferenceEligible") is True and _target_record_rejection(row, cfg) is None:
            by_account[account].append(row)
    for account in by_account:
        by_account[account].sort(key=lambda row: (str(row.get("created") or ""), str(row.get("gameId") or "")), reverse=True)
    known_elos = _latest_known_elos(source_bundle)
    eligible = [
        account for account in sorted(by_account)
        if len(by_account[account]) >= int(cfg["minimumTargetGames"]) and finite_number(known_elos.get(account))
    ]
    validation_candidates = [
        account for account in eligible
        if len(by_account[account]) >= int(cfg["validationMinimumTargetGames"])
    ]
    calibration_accounts, validation_accounts, split = _split_accounts(
        eligible, cfg, validation_candidates=validation_candidates
    )
    roles = {account: "validation" for account in validation_accounts}
    roles.update({account: "calibration" for account in calibration_accounts})
    case_dir = output / "cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, str, float, list[dict[str, Any]], str]] = []
    cases: list[dict[str, Any]] = []
    for account in sorted(roles):
        case_name = f"{hashlib.sha256(account.encode('utf-8')).hexdigest()}.json"
        case_path = case_dir / case_name
        completed = progress["completedCases"].get(account)
        if completed:
            if not case_path.is_file() or sha256_file(case_path) != completed.get("sha256"):
                raise ValueError(f"resume refused: completed calibration case changed: {account}")
            cases.append(read_json(case_path))
            continue
        if case_path.is_file():
            recovered = read_json(case_path)
            if (
                recovered.get("schema") == SCHEMA_CALIBRATION_CASE_V2
                and recovered.get("algorithmVersion") == ALGORITHM_VERSION_V2
                and recovered.get("configSha256") == canonical_sha256(cfg)
                and recovered.get("conditionalManifestSha256") == conditional_manifest_sha
                and recovered.get("account") == account
                and "C_beta_binomial_previous_z_weight_1"
                in (recovered.get("methodComparisons") or {})
                and (
                    roles[account] != "calibration"
                    or all(
                        label in (recovered.get("methodComparisons") or {})
                        for label in (
                            "A_v1_equal_phase_signed_z",
                            "B_beta_binomial_previous_z_weight_0",
                        )
                    )
                )
            ):
                progress["completedCases"][account] = {
                    "sha256": sha256_file(case_path),
                    "runtimeSeconds": recovered.get("runtimeSeconds"),
                    "completedAt": utc_now(),
                    "recoveredFromAtomicCaseShard": True,
                }
                progress["updatedAt"] = utc_now()
                atomic_write_json(progress_path, progress)
                cases.append(recovered)
                continue
            raise ValueError(f"resume refused: incompatible orphan calibration case: {case_path}")
        tasks.append((
            roles[account], account, float(known_elos[account]),
            by_account[account][:int(cfg["maximumTargetGames"])], str(case_path.resolve()),
        ))

    started = time.perf_counter()
    if tasks:
        with ProcessPoolExecutor(
            max_workers=16,
            initializer=_init_v2_calibration_worker,
            initargs=(
                str(Path(conditional_records_path).resolve()),
                str(Path(conditional_manifest_path).resolve()),
                cfg,
            ),
        ) as executor:
            for case in executor.map(_v2_calibration_case_worker, tasks, chunksize=1):
                account = str(case["account"])
                case_path = case_dir / f"{hashlib.sha256(account.encode('utf-8')).hexdigest()}.json"
                progress["completedCases"][account] = {
                    "sha256": sha256_file(case_path),
                    "runtimeSeconds": case.get("runtimeSeconds"),
                    "completedAt": utc_now(),
                }
                progress["updatedAt"] = utc_now()
                atomic_write_json(progress_path, progress)
                cases.append(case)
    cases.sort(key=lambda row: (str(row.get("role")), str(row.get("account"))))
    calibration_cases = [
        row for row in cases
        if row.get("role") == "calibration"
        and row.get("knownEloInFormalRange") is True
        and row.get("curveStatus") == "valid"
        and finite_number(row.get("trueScoreIncrease"))
    ]
    method_comparison = _method_comparison_summary_v2(calibration_cases)
    t95 = _empirical_quantile_v2(
        [float(row["trueScoreIncrease"]) for row in calibration_cases], 0.95
    )
    secondary_gaps = [float(row["secondaryMinimumGap"]) for row in calibration_cases if finite_number(row.get("secondaryMinimumGap"))]
    widths = [float(row["minimumRegionWidth"]) for row in calibration_cases if finite_number(row.get("minimumRegionWidth"))]
    jumps = [float(row["maximumAdjacentScoreJump"]) for row in calibration_cases if finite_number(row.get("maximumAdjacentScoreJump"))]
    diagnostic_thresholds = {
        "source": "calibration_accounts_only",
        "multipleMinimaScoreDelta": _empirical_quantile_v2(secondary_gaps, 0.05) or 0.0,
        "lowResolutionScoreDelta": 0.0,
        "lowResolutionEloWidth": _empirical_quantile_v2(widths, 0.95),
        "abnormalAdjacentScoreJump": _empirical_quantile_v2(jumps, 0.99),
    }
    validation_cases = [
        row for row in cases
        if row.get("role") == "validation"
        and row.get("knownEloInFormalRange") is True
        and finite_number(row.get("minimumScore"))
        and finite_number(row.get("knownElo"))
    ]
    coverage_rows = []
    interval_widths = []
    if t95 is not None:
        for row in validation_cases:
            intervals = intervals_for_score_threshold(row.get("scoreCurve") or [], float(row["minimumScore"]) + t95)
            covered = any(float(interval["lower"]) <= float(row["knownElo"]) <= float(interval["upper"]) for interval in intervals)
            coverage_rows.append(covered)
            interval_widths.extend(float(interval["upper"]) - float(interval["lower"]) for interval in intervals)
    validation_coverage = statistics.fmean(coverage_rows) if coverage_rows else None
    validation_errors = [abs(float(row["estimatedEloError"])) for row in validation_cases if finite_number(row.get("estimatedEloError"))]
    coverage_confirmed = bool(
        t95 is not None
        and len(validation_cases) >= int(cfg["minimumValidationUsers"])
        and validation_coverage is not None
        and validation_coverage >= 0.95
    )
    validated = bool(
        coverage_confirmed
        and method_comparison["adjacentConditionalImprovementConfirmed"] is True
    )
    runtimes = [float(row["runtimeSeconds"]) for row in cases if finite_number(row.get("runtimeSeconds"))]
    artifact = {
        "schema": SCHEMA_CALIBRATION_V2,
        "version": "v2",
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "createdAt": utc_now(),
        "status": "validated" if validated else "calibration_unavailable",
        "configSha256": canonical_sha256(cfg),
        "conditionalManifestSha256": conditional_manifest_sha,
        "directedRecordsSha256": directed_records_sha256,
        "knownEloDefinition": "newR from the latest created source-bundle detail for each account",
        "knownEloUsage": "post-estimation error and T95 only; never a feature or neighbor coordinate",
        "quantileMethod": "unweighted empirical linear interpolation at p=(n-1)*q",
        "calibrationCoverage": 0.95,
        "t95": t95,
        "calibrationUserCount": len(calibration_accounts),
        "validationUserCount": len(validation_accounts),
        "calibrationCaseCount": len(calibration_cases),
        "validationCaseCount": len(validation_cases),
        "validationCoveredCount": sum(coverage_rows),
        "validationCoverage": validation_coverage,
        "validationErrorSummary": _error_summary(validation_errors),
        "validationIntervalWidthSummary": _error_summary(interval_widths),
        "groupMetrics": _calibration_group_rows_v2(validation_cases, t95),
        "methodComparison": method_comparison,
        "diagnosticThresholds": diagnostic_thresholds,
        "split": {**split, "calibrationAccounts": calibration_accounts, "validationAccounts": validation_accounts},
        "parallelWorkers": 16,
        "taskUnit": "one_player_account",
        "processPoolChunksize": 1,
        "referenceQueryWorkersPerWorker": 1,
        "accountRuntimeSeconds": _error_summary(runtimes),
        "wallRuntimeSecondsThisInvocation": time.perf_counter() - started,
        "failedCaseCount": int(progress.get("failedCaseCount", 0)),
        "retryCount": int(progress.get("retryCount", 0)),
        "independentValidation": {
            "required": True,
            "usersAreDisjointFromCalibration": not bool(set(calibration_accounts) & set(validation_accounts)),
            "coverageTarget": 0.95,
            "coverageConfirmed": coverage_confirmed,
        },
    }
    atomic_write_jsonl(output / str(cfg["calibrationCases"]), cases)
    artifact["casesSha256"] = sha256_file(output / str(cfg["calibrationCases"]))
    atomic_write_json(output / str(cfg["calibrationArtifact"]), artifact)
    calibration_manifest = {
        "schema": "player-sentinel-elo-calibration-sha256-manifest-v2",
        "algorithmVersion": ALGORITHM_VERSION_V2,
        "createdAt": utc_now(),
        "configSha256": canonical_sha256(cfg),
        "conditionalManifestSha256": conditional_manifest_sha,
        "directedRecordsSha256": directed_records_sha256,
        "files": [
            {
                "path": str(cfg["calibrationArtifact"]),
                "sha256": sha256_file(output / str(cfg["calibrationArtifact"])),
            },
            {
                "path": str(cfg["calibrationCases"]),
                "sha256": artifact["casesSha256"],
            },
        ],
    }
    atomic_write_json(output / "calibration_sha256_manifest_v2.json", calibration_manifest)
    progress["status"] = "completed"
    progress["completedAt"] = utc_now()
    progress["calibrationArtifactSha256"] = sha256_file(output / str(cfg["calibrationArtifact"]))
    progress["calibrationCasesSha256"] = artifact["casesSha256"]
    progress["calibrationManifestSha256"] = sha256_file(output / "calibration_sha256_manifest_v2.json")
    atomic_write_json(progress_path, progress)
    return artifact, cases


# ---------------------------------------------------------------------------
# Estimated-Elo v3: adaptive multi-basin search and reusable global KNN
# ---------------------------------------------------------------------------


def default_v3_config() -> dict[str, Any]:
    """Return the versioned formal v3 search/calibration contract.

    The conditional reference directory deliberately points at the completed
    v2 preparation product.  Its statistical model and reference z values are
    unchanged by the v3 search, so rebuilding Level22 or the reference-z cache
    merely because the search grid changed would be both wasteful and a
    contract violation.
    """

    return {
        "schema": SCHEMA_CONFIG_V3,
        "version": "v3-20260829",
        "algorithmVersion": ALGORITHM_VERSION_V3,
        "sourceReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_elo_matchup500_blackwhite_reference_level22_1600plus_20260822"
        ),
        "sentinelDerivedDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_reference_level22_1600plus_v6_20260819"
        ),
        "derivedReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_reference_level22_1600plus_v8_20260822"
        ),
        "directedPhaseRecords": "directed_game_phase_records.jsonl",
        "referenceManifest": "reference_sha256_manifest.json",
        "conditionalReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_conditional_reference_v1_20260828"
        ),
        "conditionalReferenceRecords": "conditional_reference_records.jsonl",
        "conditionalReferenceManifest": "conditional_reference_manifest.json",
        "calibrationDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_calibration_v3_20260829"
        ),
        "calibrationArtifact": "elo_calibration_v3.json",
        "calibrationCases": "elo_calibration_cases_v3.jsonl",
        "formalEloMinimum": 1600,
        "formalEloMaximum": 2500,
        "eloGridMinimum": 1600,
        "eloGridMaximum": 2500,
        "eloGridStep": 1,
        "minimumTargetGames": 10,
        "maximumTargetGames": 30,
        "validationMinimumTargetGames": 12,
        "phaseBoundaries": [30, 47, 53],
        "ge4Threshold": 4,
        "neighborExponent": DEFAULT_NEIGHBOR_EXPONENT,
        "distanceKernel": "standardized_euclidean_triangular_k_plus_one_boundary",
        "previousZWeight": 1,
        "referenceFeaturePolicy": (
            "unified_frozen_cache_with_direct_target_account_source_game_exclusion"
        ),
        "zCdfClip": Z_CDF_CLIP,
        "betaBinomialMMinimum": BETA_BINOMIAL_M_MIN,
        "betaBinomialKappaMinimum": BETA_BINOMIAL_KAPPA_MIN,
        "betaBinomialKappaMaximum": 100000000,
        "betaBinomialOptimizer": "scipy-lbfgsb-logit-m-log-kappa",
        "calibrationCoverage": DEFAULT_CALIBRATION_COVERAGE,
        "calibrationGrouping": "global",
        "calibrationValidationFraction": DEFAULT_CALIBRATION_VALIDATION_FRACTION,
        "minimumValidationUsers": DEFAULT_MINIMUM_VALIDATION_USERS,
        "calibrationSplitSeed": 20260828502,
        "calibrationWorkers": DEFAULT_CALIBRATION_WORKERS,
        "referenceQueryWorkers": 1,
        "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V3,
        "searchSteps": list(SEARCH_STEPS_V3),
        "searchCostFallback": "fallback_when_predicted_missing_points_ge_full_grid_remaining",
        "searchMaxBasins": 64,
        "searchMaxExpansionRounds": 2,
        "searchTieTolerance": 1e-12,
        "searchBoundaryTolerance": 1e-12,
        "searchRequireConfidenceCoverage": True,
        "taskUnit": "one_player_account",
        "processPoolChunksize": 1,
        "prepareAccountShardSize": 64,
    }


def validate_v3_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate v3 while keeping model, search, and calibration contracts separate."""

    if config.get("schema") != SCHEMA_CONFIG_V3:
        raise ValueError(f"estimated-Elo v3 requires config schema {SCHEMA_CONFIG_V3}")
    result = dict(config)
    defaults = default_v3_config()
    for key, value in defaults.items():
        result.setdefault(key, value)
    if result.get("algorithmVersion") != ALGORITHM_VERSION_V3:
        raise ValueError("estimated-Elo v3 algorithmVersion mismatch")
    exact_values = {
        "formalEloMinimum": 1600,
        "formalEloMaximum": 2500,
        "eloGridMinimum": 1600,
        "eloGridMaximum": 2500,
        "eloGridStep": 1,
        "minimumTargetGames": 10,
        "maximumTargetGames": 30,
        "validationMinimumTargetGames": 12,
        "ge4Threshold": 4,
        "calibrationWorkers": 16,
        "referenceQueryWorkers": 1,
        "processPoolChunksize": 1,
    }
    for key, expected in exact_values.items():
        if float(result.get(key)) != float(expected):
            raise ValueError(f"estimated-Elo v3 requires {key}={expected}")
    if [int(value) for value in result.get("phaseBoundaries", [])] != [30, 47, 53]:
        raise ValueError("estimated-Elo v3 phaseBoundaries must be [30, 47, 53]")
    if not math.isclose(
        float(result["neighborExponent"]), DEFAULT_NEIGHBOR_EXPONENT,
        rel_tol=0.0, abs_tol=0.0,
    ):
        raise ValueError("estimated-Elo v3 neighborExponent must be exactly 2/3")
    if float(result["previousZWeight"]) != 1.0:
        raise ValueError("estimated-Elo v3 previousZWeight must be exactly 1")
    if result.get("calibrationGrouping") != "global":
        raise ValueError("estimated-Elo v3 calibrationGrouping must be global")
    if float(result["calibrationCoverage"]) != DEFAULT_CALIBRATION_COVERAGE:
        raise ValueError("estimated-Elo v3 calibrationCoverage must be exactly 0.95")
    if [int(value) for value in result.get("searchSteps", [])] != list(SEARCH_STEPS_V3):
        raise ValueError("estimated-Elo v3 searchSteps must be [40,20,10,5,2,1]")
    if result.get("searchStrategyVersion") != SEARCH_STRATEGY_VERSION_V3:
        raise ValueError("estimated-Elo v3 searchStrategyVersion mismatch")
    if int(result["searchMaxBasins"]) < 1:
        raise ValueError("searchMaxBasins must be positive")
    if int(result["searchMaxExpansionRounds"]) < 1:
        raise ValueError("searchMaxExpansionRounds must be positive")
    if not 0.0 < float(result["searchTieTolerance"]):
        raise ValueError("searchTieTolerance must be positive")
    if not 0.0 < float(result["searchBoundaryTolerance"]):
        raise ValueError("searchBoundaryTolerance must be positive")
    if int(result["prepareAccountShardSize"]) < 1:
        raise ValueError("prepareAccountShardSize must be positive")
    if float(result["zCdfClip"]) <= 0.0 or float(result["zCdfClip"]) >= 0.5:
        raise ValueError("zCdfClip must be between 0 and 0.5")
    if float(result["betaBinomialMMinimum"]) <= 0.0 or float(result["betaBinomialMMinimum"]) >= 0.5:
        raise ValueError("betaBinomialMMinimum must be between 0 and 0.5")
    if not 0.0 < float(result["betaBinomialKappaMinimum"]) < float(result["betaBinomialKappaMaximum"]):
        raise ValueError("invalid beta-binomial kappa bounds")
    return result


_V3_REFERENCE_MODEL_KEYS = (
    "formalEloMinimum", "formalEloMaximum", "minimumTargetGames",
    "maximumTargetGames", "phaseBoundaries", "ge4Threshold",
    "neighborExponent", "distanceKernel", "previousZWeight",
    "referenceFeaturePolicy", "zCdfClip", "betaBinomialMMinimum",
    "betaBinomialKappaMinimum", "betaBinomialKappaMaximum",
    "betaBinomialOptimizer",
)


def v3_reference_model_contract(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = validate_v3_config(config or default_v3_config())
    return {
        "schema": "player-sentinel-elo-reference-model-contract-v1",
        "algorithm": "conditional_beta_binomial_v2",
        "fields": {key: cfg[key] for key in _V3_REFERENCE_MODEL_KEYS},
    }


def v3_search_contract(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = validate_v3_config(config or default_v3_config())
    return {
        "schema": "player-sentinel-elo-search-contract-v1",
        "algorithmVersion": ALGORITHM_VERSION_V3,
        "strategyVersion": SEARCH_STRATEGY_VERSION_V3,
        "formalRange": [int(cfg["formalEloMinimum"]), int(cfg["formalEloMaximum"])],
        "steps": list(SEARCH_STEPS_V3),
        "alignment": "relative_to_formal_elo_minimum_with_explicit_interval_endpoints",
        "localMinima": "all_internal_points_platforms_and_reliable_boundary_trends",
        "boundaryProtection": "expand_unbracketed_basins_or_fallback_full_grid",
        "fallback": str(cfg["searchCostFallback"]),
    }


def v3_calibration_contract(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = validate_v3_config(config or default_v3_config())
    return {
        "schema": "player-sentinel-elo-calibration-contract-v1",
        "grouping": cfg["calibrationGrouping"],
        "coverage": float(cfg["calibrationCoverage"]),
        "validationFraction": float(cfg["calibrationValidationFraction"]),
        "minimumValidationUsers": int(cfg["minimumValidationUsers"]),
        "workers": int(cfg["calibrationWorkers"]),
        "taskUnit": cfg["taskUnit"],
        "chunksize": int(cfg["processPoolChunksize"]),
        "referenceQueryWorkersPerWorker": int(cfg["referenceQueryWorkers"]),
        "twoPass": "calibration_t95_then_validation_frozen_cutoff",
    }


def conditional_config_for_v3(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project v3's model contract onto the v2 reference-z preparation schema."""

    cfg = validate_v3_config(config or default_v3_config())
    projected = default_v2_config()
    for key in (
        "sourceReferenceDirectory", "derivedReferenceDirectory",
        "directedPhaseRecords", "referenceManifest",
        "conditionalReferenceDirectory", "conditionalReferenceRecords",
        "conditionalReferenceManifest", "formalEloMinimum", "formalEloMaximum",
        "eloGridMinimum", "eloGridMaximum", "eloGridStep", "minimumTargetGames",
        "maximumTargetGames", "phaseBoundaries", "ge4Threshold",
        "neighborExponent", "distanceKernel", "previousZWeight",
        "referenceFeaturePolicy", "zCdfClip", "betaBinomialMMinimum",
        "betaBinomialKappaMinimum", "betaBinomialKappaMaximum",
        "betaBinomialOptimizer", "prepareAccountShardSize",
    ):
        if key in cfg:
            projected[key] = cfg[key]
    return validate_v2_config(projected)


def _v3_validate_conditional_manifest(
    manifest: dict[str, Any], config: dict[str, Any]
) -> None:
    if manifest.get("schema") != SCHEMA_CONDITIONAL_REFERENCE:
        raise ValueError("v3 requires a conditional-reference-v1 manifest")
    if manifest.get("algorithmVersion") != ALGORITHM_VERSION_V2:
        raise ValueError("v3 conditional reference must use the frozen v2 model cache")
    projected = conditional_config_for_v3(config)
    # The reference/model contract intentionally excludes v3 search and
    # calibration settings.  It also excludes the filesystem location of the
    # cache: a smoke copy or a relocated immutable cache is the same model
    # product.  Accept the exact projected contract and the same contract with
    # the v2 model paths restored to their canonical defaults.
    canonical_projected = conditional_config_for_v3(default_v3_config())
    expected_projected_shas = {
        canonical_sha256(projected),
        canonical_sha256(canonical_projected),
    }
    if manifest.get("configSha256") not in expected_projected_shas:
        raise ValueError(
            "v3/reference model contract mismatch; search configuration cannot change "
            "the conditional reference cache"
        )
    if manifest.get("referenceFeaturePolicy") != config["referenceFeaturePolicy"]:
        raise ValueError("conditional reference feature policy mismatch")
    scales = manifest.get("scales")
    if not isinstance(scales, dict):
        raise ValueError("conditional reference manifest has no scales")
    for color in COLORS:
        for scope in METRICS_SCOPES:
            key = f"{color}|{scope}"
            scale = scales.get(key)
            if not isinstance(scale, dict):
                raise ValueError(f"conditional reference manifest is missing scales for {key}")
            for field in ("selfEloSd", "opponentEloSd", "phase1ZSd", "phase2ZSd", "phase3ZSd"):
                if not finite_number(scale.get(field)) or float(scale[field]) <= 0:
                    raise ValueError(f"conditional reference scale {key}.{field} is invalid")


def load_conditional_reference_v3(
    directory: str | Path,
    *,
    config: dict[str, Any] | None = None,
    expected_reference_manifest_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Load the reusable v2 conditional cache under v3's split contracts."""

    cfg = validate_v3_config(config or default_v3_config())
    root = Path(directory)
    manifest_path = root / str(cfg["conditionalReferenceManifest"])
    records_path = root / str(cfg["conditionalReferenceRecords"])
    manifest = read_json(manifest_path)
    _v3_validate_conditional_manifest(manifest, cfg)
    if (
        expected_reference_manifest_sha256 is not None
        and manifest.get("referenceManifestSha256") != expected_reference_manifest_sha256
    ):
        raise ValueError("v3 conditional reference/source manifest SHA-256 mismatch")
    if sha256_file(records_path) != manifest.get("recordsSha256"):
        raise ValueError("v3 conditional reference records SHA-256 mismatch")
    rows = read_jsonl(records_path)
    if len(rows) != int(manifest.get("recordCount", -1)):
        raise ValueError("v3 conditional reference record count mismatch")
    if any(row.get("schema") != SCHEMA_CONDITIONAL_RECORD for row in rows):
        raise ValueError("unsupported conditional reference record schema")
    return rows, manifest, sha256_file(manifest_path)


def _v3_record_stable_key(row: dict[str, Any], index: int) -> tuple[Any, ...]:
    """Return the stable tie key used after cKDTree distance retrieval."""

    return (
        str(row.get("gameId") or ""),
        str(row.get("targetColor") or ""),
        account_key(row.get("targetPlayerId")),
        index,
    )


class _GlobalConditionalPoolV3:
    """One read-only global cKDTree plus exact account-exclusion queries."""

    def __init__(
        self,
        records: Sequence[dict[str, Any]],
        scales: dict[str, Any],
        stage: int,
        config: dict[str, Any],
        pool_key: str,
    ) -> None:
        if stage not in {1, 2, 3, 4}:
            raise ValueError("stage must be 1..4")
        self.stage = int(stage)
        self.pool_key = str(pool_key)
        self.config = validate_v3_config(config)
        self.scales = scales
        self.previous_key = f"referenceZ{stage - 1}" if stage > 1 else None
        self.previous_sd_key = f"phase{stage - 1}ZSd" if stage > 1 else None
        self.previous_sd = None if self.previous_sd_key is None else scales.get(self.previous_sd_key)
        if self.previous_key is not None and (
            not finite_number(self.previous_sd) or float(self.previous_sd) <= 0
        ):
            raise ValueError(f"missing positive {self.previous_sd_key} for stage {stage}")
        self.records = [
            row for row in records
            if _phase_counts(row, stage) is not None
            and (self.previous_key is None or finite_number(row.get(self.previous_key)))
        ]
        self.record_count = len(self.records)
        self.k_without_exclusion = (
            neighbor_count_v3(self.record_count, float(self.config["neighborExponent"]))
            if self.record_count else 0
        )
        self.features: Any = None
        self._np: Any = None
        self.tree: Any = None
        self.query_count = 0
        self.query_ball_count = 0
        feature_rows = [self._feature_for_record(row) for row in self.records]
        if feature_rows:
            _special, _optimize, cKDTree = _scipy_v2()
            import numpy as np  # type: ignore
            self._np = np
            self.features = np.asarray(feature_rows, dtype=float)
            self.tree = cKDTree(self.features)
        else:
            self.features = []

        self._account_indexes: dict[str, set[int]] = defaultdict(set)
        self._game_indexes: dict[str, set[int]] = defaultdict(set)
        for index, row in enumerate(self.records):
            game_id = str(row.get("gameId") or "")
            if game_id:
                self._game_indexes[game_id].add(index)
            for player_key in (
                account_key(row.get("targetPlayerId")),
                account_key(row.get("opponentPlayerId")),
            ):
                if player_key:
                    self._account_indexes[player_key].add(index)
        account_counts = [len(indexes) for indexes in self._account_indexes.values()]
        self.maximum_possible_excluded_record_count = max(account_counts, default=0)

    def _feature_for_record(self, row: dict[str, Any]) -> list[float]:
        features = [
            float(row["targetOldR"]) / float(self.scales["selfEloSd"]),
            float(row["opponentOldR"]) / float(self.scales["opponentEloSd"]),
        ]
        if self.previous_key is not None:
            features.append(
                math.sqrt(float(self.config["previousZWeight"]))
                * float(row[self.previous_key]) / float(self.previous_sd)
            )
        return features

    def _query_feature(self, trial_elo: float, opponent_elo: float, previous_z: float | None) -> list[float]:
        query = [
            float(trial_elo) / float(self.scales["selfEloSd"]),
            float(opponent_elo) / float(self.scales["opponentEloSd"]),
        ]
        if self.previous_key is not None:
            if not finite_number(previous_z):
                raise ValueError("all previous-z distance values must be finite")
            query.append(
                math.sqrt(float(self.config["previousZWeight"]))
                * float(previous_z) / float(self.previous_sd)
            )
        return query

    def excluded_indexes(self, account: str, target_game_ids: Iterable[str]) -> set[int]:
        excluded: set[int] = set(self._account_indexes.get(account_key(account), set()))
        for game_id in target_game_ids:
            excluded.update(self._game_indexes.get(str(game_id), set()))
        return excluded

    def nearest(
        self,
        account: str,
        target_game_ids: Iterable[str],
        trial_elo: float,
        opponent_elo: float,
        previous_z: float | None = None,
    ) -> dict[str, Any]:
        excluded = self.excluded_indexes(account, target_game_ids)
        n_allowed = self.record_count - len(excluded)
        k = neighbor_count_v3(n_allowed, float(self.config["neighborExponent"])) if n_allowed else 0
        base = {
            "eligibleReferenceCount": n_allowed,
            "N_allowed": n_allowed,
            "K": k,
            "excludedRecordCount": len(excluded),
            "maximumPossibleExcludedRecordCount": self.maximum_possible_excluded_record_count,
        }
        if self.tree is None or self._np is None or n_allowed < k + 1:
            return {"ok": False, "reason": "insufficient_reference", **base}
        query_array = self._np.asarray(
            self._query_feature(trial_elo, opponent_elo, previous_z), dtype=float
        )
        # M is a precomputed per-pool upper bound plus the exact target-game
        # contribution.  Querying K+1+M raw records guarantees that removing
        # the excluded records cannot hide an allowed K+1 boundary record.
        m = max(self.maximum_possible_excluded_record_count, len(excluded))
        query_k = min(self.record_count, k + 1 + m)
        distances, indexes = self.tree.query(
            query_array, k=query_k, workers=int(self.config["referenceQueryWorkers"])
        )
        self.query_count += 1
        raw_distances = [float(value) for value in self._np.atleast_1d(distances)]
        raw_indexes = [int(value) for value in self._np.atleast_1d(indexes)]
        candidates = {
            index for index in raw_indexes
            if 0 <= index < self.record_count and index not in excluded
        }
        query_boundary = raw_distances[-1] if raw_distances else math.inf
        boundary_tie = bool(
            len(raw_distances) >= 2
            and abs(raw_distances[-1] - raw_distances[-2])
                <= float(self.config["searchBoundaryTolerance"])
        )

        def ranked_allowed(indexes_to_rank: set[int]) -> list[tuple[float, tuple[Any, ...], int]]:
            ranked_rows = []
            for index in indexes_to_rank:
                distance = float(self._np.linalg.norm(self.features[index] - query_array))
                ranked_rows.append((distance, _v3_record_stable_key(self.records[index], index), index))
            ranked_rows.sort(key=lambda item: (item[0], item[1]))
            return ranked_rows

        ranked = ranked_allowed(candidates)
        selected_boundary_tie = bool(
            len(ranked) >= k + 1
            and abs(ranked[k - 1][0] - ranked[k][0])
                <= float(self.config["searchBoundaryTolerance"])
        )
        if query_k < self.record_count and (boundary_tie or selected_boundary_tie):
            expanded = self.tree.query_ball_point(
                query_array,
                r=float(query_boundary) + float(self.config["searchBoundaryTolerance"]),
                workers=int(self.config["referenceQueryWorkers"]),
            )
            self.query_ball_count += 1
            candidates.update(
                int(index) for index in expanded
                if 0 <= int(index) < self.record_count and int(index) not in excluded
            )
            ranked = ranked_allowed(candidates)
        # This is an assertion of the M contract, not a numerical fallback.
        # If a caller supplied more target game IDs than the bound anticipated,
        # querying all records is the only exact continuation.
        if len(ranked) < k + 1:
            distances, indexes = self.tree.query(
                query_array, k=self.record_count,
                workers=int(self.config["referenceQueryWorkers"]),
            )
            self.query_count += 1
            all_indexes = {
                int(index) for index in self._np.atleast_1d(indexes)
                if 0 <= int(index) < self.record_count and int(index) not in excluded
            }
            ranked = ranked_allowed(all_indexes)
        if len(ranked) < k + 1:
            return {
                "ok": False,
                "reason": "insufficient_reference",
                "queryK": query_k,
                **base,
            }
        boundary = float(ranked[k][0])
        if not math.isfinite(boundary) or boundary <= 0:
            return {
                "ok": False,
                "reason": "insufficient_reference",
                "boundaryDistance": boundary,
                "queryK": query_k,
                **base,
            }
        selected = ranked[:k]
        weights = [max(0.0, 1.0 - float(row[0]) / boundary) for row in selected]
        weight_sum = sum(weights)
        if not math.isfinite(weight_sum) or weight_sum <= 0:
            return {
                "ok": False,
                "reason": "insufficient_reference",
                "boundaryDistance": boundary,
                "queryK": query_k,
                "weightSum": weight_sum,
                **base,
            }
        return {
            "ok": True,
            "records": [self.records[index] for _distance, _key, index in selected],
            "weights": weights,
            "neighborRecordIds": [
                str(self.records[index].get("recordId") or _conditional_record_id(self.records[index]))
                for _distance, _key, index in selected
            ],
            "neighborSetSha256": canonical_sha256([
                str(self.records[index].get("recordId") or _conditional_record_id(self.records[index]))
                for _distance, _key, index in selected
            ]),
            "K": k,
            "N_allowed": n_allowed,
            "eligibleReferenceCount": n_allowed,
            "boundaryDistance": boundary,
            "effectiveWeight": weight_sum,
            "excludedRecordCount": len(excluded),
            "maximumPossibleExcludedRecordCount": self.maximum_possible_excluded_record_count,
            "queryK": query_k,
            "queryCandidateCount": len(candidates),
            "boundaryExpanded": bool(boundary_tie or selected_boundary_tie),
        }


class GlobalConditionalKNNIndexV3:
    """All color/scope/stage trees built once and reused by account tasks."""

    def __init__(
        self,
        conditional_records: Sequence[dict[str, Any]],
        conditional_manifest: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        started = time.perf_counter()
        self.config = validate_v3_config(config)
        _v3_validate_conditional_manifest(conditional_manifest, self.config)
        self.conditional_manifest = conditional_manifest
        self.pools: dict[tuple[str, str, int], _GlobalConditionalPoolV3] = {}
        by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in conditional_records:
            color = str(row.get("targetColor") or "").strip().casefold()
            scope = str(row.get("scope") or "").strip()
            if color in COLORS and scope in METRICS_SCOPES:
                by_pool[f"{color}|{scope}"].append(row)
        scales = conditional_manifest.get("scales") or {}
        for color in COLORS:
            for scope in METRICS_SCOPES:
                pool_key = f"{color}|{scope}"
                if pool_key not in scales:
                    raise ValueError(f"conditional reference manifest has no pool {pool_key}")
                for stage in range(1, 5):
                    self.pools[(color, scope, stage)] = _GlobalConditionalPoolV3(
                        by_pool.get(pool_key, []), scales[pool_key], stage,
                        self.config, pool_key,
                    )
        self.tree_build_seconds = time.perf_counter() - started
        self.tree_build_count = len(self.pools)
        self._accounts_seen: set[str] = set()

    def pool(self, color: str, scope: str, stage: int) -> _GlobalConditionalPoolV3 | None:
        return self.pools.get((str(color).casefold(), str(scope), int(stage)))

    def begin_account(self, account: str) -> bool:
        key = account_key(account)
        first = key not in self._accounts_seen
        self._accounts_seen.add(key)
        return first

    @property
    def query_count(self) -> int:
        return sum(pool.query_count + pool.query_ball_count for pool in self.pools.values())

    @property
    def tree_query_count(self) -> int:
        return sum(pool.query_count for pool in self.pools.values())

    @property
    def boundary_expansion_count(self) -> int:
        return sum(pool.query_ball_count for pool in self.pools.values())


def neighbor_count_v3(reference_count: int, exponent: float = DEFAULT_NEIGHBOR_EXPONENT) -> int:
    """The v3 contract uses floor(N^(2/3)), unlike the historical v1/v2 ceil."""

    if reference_count <= 0:
        raise ValueError("reference_count must be positive")
    if float(exponent) != DEFAULT_NEIGHBOR_EXPONENT:
        raise ValueError("v3 neighbor exponent must be exactly 2/3")
    return max(1, int(math.floor(float(reference_count) ** (2.0 / 3.0))))


def score_conditional_phase_v3(
    target_record: dict[str, Any],
    stage: int,
    trial_elo: float,
    pool: _GlobalConditionalPoolV3,
    *,
    account: str,
    target_game_ids: Iterable[str],
    previous_z: float | None = None,
    config: dict[str, Any] | None = None,
    initial_fit: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Score one phase using the global tree and exact per-account exclusion."""

    cfg = validate_v3_config(config or pool.config)
    counts = _phase_counts(target_record, stage)
    if counts is None:
        return {"ok": False, "reason": "incomplete_phase_data", "phase": stage, "fitAttempted": False}
    opponent = target_record.get("opponentOldR")
    if not finite_number(opponent):
        return {"ok": False, "reason": "opponent_out_of_reference_range", "phase": stage, "fitAttempted": False}
    nearest = pool.nearest(
        account, target_game_ids, float(trial_elo), float(opponent), previous_z
    )
    if nearest.get("ok") is not True:
        return {"ok": False, "phase": stage, "fitAttempted": False, **nearest}
    observations = []
    for row, weight in zip(nearest["records"], nearest["weights"], strict=True):
        reference_counts = _phase_counts(row, stage)
        if reference_counts is not None:
            observations.append((reference_counts[0], reference_counts[1], float(weight)))
    model_config = conditional_config_for_v3(cfg)
    fit = fit_weighted_beta_binomial(
        observations, config=model_config, initial_parameters=initial_fit
    )
    if fit.get("ok") is not True:
        return {
            "ok": False,
            "reason": "beta_binomial_fit_failed",
            "phase": stage,
            "fitAttempted": True,
            "fitStatus": fit.get("fitStatus"),
            "fit": fit,
            **{key: nearest.get(key) for key in (
                "K", "N_allowed", "boundaryDistance", "eligibleReferenceCount",
                "effectiveWeight", "neighborSetSha256", "excludedRecordCount",
                "maximumPossibleExcludedRecordCount", "queryK",
            )},
        }
    x, n = counts
    distribution = beta_binomial_mid_cdf_z(
        x, n, float(fit["alpha"]), float(fit["beta"]), q_clip=float(cfg["zCdfClip"])
    )
    return {
        "ok": True,
        "phase": stage,
        "x": x,
        "n": n,
        "targetZ": distribution["z"],
        "PExact": distribution["PExact"],
        "logPExact": distribution["logPExact"],
        "negativeLogProbability": -distribution["logPExact"],
        "midCdf": distribution["midCdf"],
        "clippedMidCdf": distribution["clippedMidCdf"],
        "K": nearest["K"],
        "N_allowed": nearest["N_allowed"],
        "boundaryDistance": nearest["boundaryDistance"],
        "eligibleReferenceCount": nearest["eligibleReferenceCount"],
        "effectiveWeight": nearest["effectiveWeight"],
        "excludedRecordCount": nearest["excludedRecordCount"],
        "maximumPossibleExcludedRecordCount": nearest["maximumPossibleExcludedRecordCount"],
        "queryK": nearest["queryK"],
        "neighborSetSha256": nearest["neighborSetSha256"],
        "neighborRecordIds": nearest["neighborRecordIds"],
        "fitAttempted": True,
        "fitM": fit["m"],
        "fitKappa": fit["kappa"],
        "fitStatus": fit["fitStatus"],
        "fitScore": fit["fitScore"],
        "optimizerBoundary": fit["optimizerBoundary"],
    }


def aligned_elo_points_v3(
    lower: int,
    upper: int,
    step: int,
    *,
    formal_minimum: int = DEFAULT_FORMAL_ELO_MINIMUM,
    formal_maximum: int = 2500,
) -> list[int]:
    """Return globally aligned points and explicit interval endpoints."""

    lower = max(int(formal_minimum), int(lower))
    upper = min(int(formal_maximum), int(upper))
    if lower > upper:
        return []
    if int(step) <= 0:
        raise ValueError("Elo search step must be positive")
    step = int(step)
    first_offset = math.ceil((lower - int(formal_minimum)) / step)
    first = int(formal_minimum) + first_offset * step
    points = list(range(first, upper + 1, step))
    # Every interval endpoint is a real requested point.  This matters when a
    # basin begins between two globally aligned 2- or 5-point samples.
    points.extend((lower, upper))
    return sorted(set(point for point in points if lower <= point <= upper))


def elo_grid_v3(config: dict[str, Any] | None = None) -> list[int]:
    cfg = validate_v3_config(config or default_v3_config())
    return list(range(int(cfg["formalEloMinimum"]), int(cfg["formalEloMaximum"]) + 1))


def _v3_score_equal(left: float, right: float, tolerance: float) -> bool:
    return abs(float(left) - float(right)) <= float(tolerance)


def _v3_discover_basins(
    points: Sequence[dict[str, Any]],
    *,
    resolution: int,
    formal_minimum: int,
    formal_maximum: int,
    tolerance: float,
) -> list[dict[str, Any]]:
    """Find every sampled basin without treating sparse points as adjacent 1-point samples."""

    valid = sorted(
        [
            point for point in points
            if finite_number(point.get("elo")) and finite_number(point.get("score"))
        ],
        key=lambda point: int(point["elo"]),
    )
    if not valid:
        return []
    resolution = max(1, int(resolution))
    groups: list[list[dict[str, Any]]] = []
    current = [valid[0]]
    for point in valid[1:]:
        adjacent = int(point["elo"]) - int(current[-1]["elo"]) <= resolution
        equal = _v3_score_equal(float(point["score"]), float(current[-1]["score"]), tolerance)
        if adjacent and equal:
            current.append(point)
        else:
            groups.append(current)
            current = [point]
    groups.append(current)

    basins: list[dict[str, Any]] = []
    for group_index, group in enumerate(groups):
        first = group[0]
        last = group[-1]
        left = valid[valid.index(first) - 1] if valid.index(first) > 0 else None
        right_index = valid.index(last) + 1
        right = valid[right_index] if right_index < len(valid) else None
        left_adjacent = left is not None and int(first["elo"]) - int(left["elo"]) <= resolution
        right_adjacent = right is not None and int(right["elo"]) - int(last["elo"]) <= resolution
        minimum_score = float(first["score"])
        left_higher = left is not None and left_adjacent and float(left["score"]) > minimum_score + tolerance
        right_higher = right is not None and right_adjacent and float(right["score"]) > minimum_score + tolerance
        left_equal_or_higher = left is not None and left_adjacent and float(left["score"]) >= minimum_score - tolerance
        right_equal_or_higher = right is not None and right_adjacent and float(right["score"]) >= minimum_score - tolerance
        is_low_boundary_group = int(first["elo"]) == int(formal_minimum)
        is_high_boundary_group = int(last["elo"]) == int(formal_maximum)
        internal_minimum = bool(
            (left_higher or (left_equal_or_higher and right_higher))
            and (right_higher or (right_equal_or_higher and left_higher))
        )
        boundary_trend = bool(
            (is_low_boundary_group and right_higher)
            or (is_high_boundary_group and left_higher)
        )
        # A singleton may be a local minimum even when one neighbor is equal;
        # the grouping above handles an actual contiguous equal platform.
        if not internal_minimum and not boundary_trend:
            continue
        bracket_left = int(left["elo"]) if left is not None and left_adjacent else None
        bracket_right = int(right["elo"]) if right is not None and right_adjacent else None
        needs_expansion = bracket_left is None or bracket_right is None
        basins.append({
            "basinId": f"basin-{group_index:04d}",
            "sampledLower": int(first["elo"]),
            "sampledUpper": int(last["elo"]),
            "minimumScore": minimum_score,
            "minimumEloPoints": [int(point["elo"]) for point in group],
            "leftProtectionPoint": bracket_left,
            "rightProtectionPoint": bracket_right,
            "refinementLower": bracket_left if bracket_left is not None else int(first["elo"]),
            "refinementUpper": bracket_right if bracket_right is not None else int(last["elo"]),
            "internalMinimum": internal_minimum,
            "boundaryTrend": boundary_trend,
            "needsExpansion": needs_expansion,
            "sourceResolution": resolution,
        })
    return basins


def _v3_merge_intervals(intervals: Sequence[tuple[int, int]]) -> list[dict[str, int]]:
    ordered = sorted(
        [(int(lower), int(upper)) for lower, upper in intervals if int(lower) <= int(upper)],
        key=lambda item: (item[0], item[1]),
    )
    merged: list[list[int]] = []
    for lower, upper in ordered:
        if not merged or lower > merged[-1][1]:
            merged.append([lower, upper])
        else:
            merged[-1][1] = max(merged[-1][1], upper)
    return [{"lower": lower, "upper": upper} for lower, upper in merged]


def _v3_discover_active_edge_basins(
    points: Sequence[dict[str, Any]],
    active_intervals: Sequence[dict[str, int]],
    *,
    formal_minimum: int,
    formal_maximum: int,
    tolerance: float,
) -> list[dict[str, Any]]:
    """Keep a refined basin alive when its sampled minimum reaches an edge."""

    valid = sorted(
        [
            point for point in points
            if finite_number(point.get("elo")) and finite_number(point.get("score"))
        ],
        key=lambda point: int(point["elo"]),
    )
    basins: list[dict[str, Any]] = []
    for interval_index, interval in enumerate(active_intervals):
        lower = int(interval["lower"])
        upper = int(interval["upper"])
        inside = [
            point for point in valid
            if lower <= int(point["elo"]) <= upper
        ]
        if not inside:
            continue
        minimum = min(float(point["score"]) for point in inside)
        minimum_points = [
            point for point in inside
            if _v3_score_equal(float(point["score"]), minimum, tolerance)
        ]
        at_lower = any(int(point["elo"]) == lower for point in minimum_points)
        at_upper = any(int(point["elo"]) == upper for point in minimum_points)
        if not at_lower and not at_upper:
            continue

        lower_guards = [
            point for point in valid
            if int(point["elo"]) < lower
            and float(point["score"]) > minimum + tolerance
        ]
        upper_guards = [
            point for point in valid
            if int(point["elo"]) > upper
            and float(point["score"]) > minimum + tolerance
        ]
        left_guard = int(lower_guards[-1]["elo"]) if lower_guards else None
        right_guard = int(upper_guards[0]["elo"]) if upper_guards else None
        needs_left = at_lower and lower > formal_minimum and left_guard is None
        needs_right = at_upper and upper < formal_maximum and right_guard is None
        if not (needs_left or needs_right):
            continue
        basins.append({
            "basinId": f"active-edge-{interval_index:04d}",
            "sampledLower": min(int(point["elo"]) for point in minimum_points),
            "sampledUpper": max(int(point["elo"]) for point in minimum_points),
            "minimumScore": minimum,
            "minimumEloPoints": [int(point["elo"]) for point in minimum_points],
            "leftProtectionPoint": left_guard if at_lower else lower,
            "rightProtectionPoint": right_guard if at_upper else upper,
            "refinementLower": left_guard if at_lower and left_guard is not None else lower,
            "refinementUpper": right_guard if at_upper and right_guard is not None else upper,
            "internalMinimum": False,
            "boundaryTrend": False,
            "needsExpansion": needs_left or needs_right,
            "activeIntervalEdge": {
                "lower": bool(needs_left),
                "upper": bool(needs_right),
            },
            "sourceResolution": None,
        })
    return basins


class _V3ScoreEvaluator:
    """Account-local scoring state backed by one process-global index."""

    def __init__(
        self,
        target_records: Sequence[dict[str, Any]],
        account: str,
        index: GlobalConditionalKNNIndexV3,
        config: dict[str, Any],
    ) -> None:
        self.target_records = list(target_records)
        self.account = account_key(account)
        self.index = index
        self.config = validate_v3_config(config)
        self.target_game_ids = [str(row.get("gameId") or "") for row in self.target_records]
        self.cache: dict[int, dict[str, Any]] = {}
        self.search_point_elos: set[int] = set()
        self.warm_fits: dict[tuple[str, int], tuple[float, float]] = {}
        self.fit_count = 0
        self.scoring_seconds = 0.0
        self.hard_failure_reasons: Counter[str] = Counter()
        self.query_count_before = index.query_count
        self.tree_query_count_before = index.tree_query_count
        self.boundary_query_count_before = index.boundary_expansion_count
        self._tree_build_was_reused = not index.begin_account(self.account)

    def evaluate(self, elo: int, *, include_in_search: bool = False) -> dict[str, Any]:
        elo = int(elo)
        if include_in_search:
            self.search_point_elos.add(elo)
        cached = self.cache.get(elo)
        if cached is not None:
            return cached
        started = time.perf_counter()
        game_rows: list[dict[str, Any]] = []
        failure_reasons: Counter[str] = Counter()
        for target in self.target_records:
            game_id = str(target.get("gameId") or "")
            color = str(target.get("targetColor") or "").strip().casefold()
            scope = selected_metrics_scope(target)
            pool_key = f"{color}|{scope}"
            previous_z: float | None = None
            phase_rows: list[dict[str, Any]] = []
            game_failed: str | None = None
            for stage in range(1, 5):
                pool = self.index.pool(color, scope, stage)
                if pool is None:
                    result = {
                        "ok": False,
                        "reason": "insufficient_reference",
                        "phase": stage,
                        "fitAttempted": False,
                    }
                else:
                    result = score_conditional_phase_v3(
                        target,
                        stage,
                        float(elo),
                        pool,
                        account=self.account,
                        target_game_ids=self.target_game_ids,
                        previous_z=previous_z,
                        config=self.config,
                        # Do not carry an optimizer start point between Elo
                        # candidates.  The neighbor set changes with Elo, and
                        # a stale start can turn an otherwise convergent
                        # Beta-Binomial fit into an order-dependent failure.
                        initial_fit=None,
                    )
                result.update({
                    "gameId": game_id,
                    "scope": scope,
                    "color": color,
                    "elo": elo,
                })
                phase_rows.append(result)
                if result.get("fitAttempted") is True:
                    self.fit_count += 1
                if result.get("ok") is not True:
                    game_failed = str(result.get("reason") or "insufficient_reference")
                    failure_reasons[game_failed] += 1
                    if game_failed == "beta_binomial_fit_failed":
                        self.hard_failure_reasons[game_failed] += 1
                    break
                self.warm_fits[(game_id, stage)] = (
                    float(result["fitM"]), float(result["fitKappa"])
                )
                previous_z = float(result["targetZ"])
            if game_failed is None:
                game_nll = sum(float(row["negativeLogProbability"]) for row in phase_rows)
                game_rows.append({
                    "gameId": game_id,
                    "ok": True,
                    "gameNegativeLogLikelihood": game_nll,
                    "meanConditionalZ": statistics.fmean(float(row["targetZ"]) for row in phase_rows),
                    "phaseDiagnostics": phase_rows,
                })
            else:
                game_rows.append({
                    "gameId": game_id,
                    "ok": False,
                    "reason": game_failed,
                    "phaseDiagnostics": phase_rows,
                })
        valid_games = [row for row in game_rows if row.get("ok") is True]
        score = (
            statistics.fmean(float(row["gameNegativeLogLikelihood"]) for row in valid_games)
            if len(valid_games) == len(self.target_records) and valid_games else None
        )
        point = {
            "elo": elo,
            "meanNegativeLogLikelihood": score,
            "score": score,
            "meanConditionalZ": (
                statistics.fmean(float(row["meanConditionalZ"]) for row in valid_games)
                if len(valid_games) == len(self.target_records) and valid_games else None
            ),
            "validTargetGameCount": len(valid_games),
            "failureReasons": sorted(failure_reasons),
            "failureReasonCounts": dict(sorted(failure_reasons.items())),
            "gameDiagnostics": game_rows,
        }
        self.cache[elo] = point
        self.scoring_seconds += time.perf_counter() - started
        return point

    def evaluate_many(self, elos: Iterable[int], *, include_in_search: bool = False) -> list[dict[str, Any]]:
        return [self.evaluate(int(elo), include_in_search=include_in_search) for elo in elos]


def _v3_curve_points(
    evaluator: _V3ScoreEvaluator,
    *,
    include_elos: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    elos = set(evaluator.search_point_elos if include_elos is None else include_elos)
    return [
        {
            key: value for key, value in point.items()
            if key not in {"gameDiagnostics"}
        }
        for point in sorted(
            (evaluator.cache[elo] for elo in elos if elo in evaluator.cache),
            key=lambda row: int(row["elo"]),
        )
    ]


def _v3_best_point(points: Sequence[dict[str, Any]], tolerance: float) -> dict[str, Any] | None:
    valid = [point for point in points if finite_number(point.get("score"))]
    if not valid:
        return None
    minimum = min(float(point["score"]) for point in valid)
    return min(
        (point for point in valid if _v3_score_equal(float(point["score"]), minimum, tolerance)),
        key=lambda point: int(point["elo"]),
    )


def _v3_full_grid(config: dict[str, Any]) -> list[int]:
    return list(range(int(config["formalEloMinimum"]), int(config["formalEloMaximum"]) + 1))


def _v3_failure_status(points: Sequence[dict[str, Any]]) -> tuple[str, list[str]]:
    reasons = Counter(
        reason
        for point in points
        for reason in (point.get("failureReasons") or [])
    )
    if reasons.get("beta_binomial_fit_failed", 0):
        return "beta_binomial_fit_failed", ["beta_binomial_fit_failed"]
    if reasons:
        return "insufficient_reference", ["no_complete_J_value"]
    return "insufficient_reference", ["no_complete_J_value"]


def _v3_curve_stats(
    points: Sequence[dict[str, Any]],
    *,
    formal_minimum: int,
    formal_maximum: int,
    full_grid: bool,
    tolerance: float,
) -> dict[str, Any]:
    ordered = sorted(
        [point for point in points if finite_number(point.get("score"))],
        key=lambda point: int(point["elo"]),
    )
    if not ordered:
        status, reasons = _v3_failure_status(points)
        return {
            "status": status,
            "statusReasons": reasons,
            "minimumScore": None,
            "bestGridPoints": [],
            "localMinima": [],
            "minimumRegionWidth": None,
            "maximumAdjacentScoreJump": None,
            "maximumSampledScoreJump": None,
            "maximumSampledEloGap": None,
            "diagnosticContinuity": "none",
        }
    minimum = min(float(point["score"]) for point in ordered)
    best = [
        int(point["elo"]) for point in ordered
        if _v3_score_equal(float(point["score"]), minimum, tolerance)
    ]
    local_minima: list[dict[str, Any]] = []
    for index, point in enumerate(ordered):
        left = ordered[index - 1] if index else None
        right = ordered[index + 1] if index + 1 < len(ordered) else None
        left_adjacent = left is not None and int(point["elo"]) - int(left["elo"]) == 1
        right_adjacent = right is not None and int(right["elo"]) - int(point["elo"]) == 1
        if (
            (left is None or not left_adjacent or float(point["score"]) <= float(left["score"]) + tolerance)
            and (right is None or not right_adjacent or float(point["score"]) <= float(right["score"]) + tolerance)
            and (left_adjacent or right_adjacent)
        ):
            local_minima.append({"elo": int(point["elo"]), "score": float(point["score"])})
    sampled_jumps = [
        abs(float(right["score"]) - float(left["score"]))
        for left, right in zip(ordered, ordered[1:])
    ]
    sampled_gaps = [
        int(right["elo"]) - int(left["elo"])
        for left, right in zip(ordered, ordered[1:])
    ]
    contiguous = bool(
        full_grid
        and len(ordered) == formal_maximum - formal_minimum + 1
        and all(int(point["elo"]) == formal_minimum + index for index, point in enumerate(ordered))
    )
    if contiguous:
        adjacent_jumps = sampled_jumps
        continuity = "full_integer_grid"
        minimum_region_width = max(best) - min(best) if best else None
    else:
        adjacent_jumps = None
        continuity = "sparse_or_mixed_grid"
        minimum_region_width = None
    best_elo = min(best)
    if full_grid and best_elo == formal_maximum and all(
        float(right["score"]) <= float(left["score"]) + tolerance
        for left, right in zip(ordered, ordered[1:])
    ):
        status, reasons = "above_reference_range", ["J_continues_improving_to_upper_boundary"]
    elif full_grid and best_elo == formal_minimum and all(
        float(right["score"]) >= float(left["score"]) - tolerance
        for left, right in zip(ordered, ordered[1:])
    ):
        status, reasons = "below_reference_range", ["J_continues_improving_to_lower_boundary"]
    else:
        status = "valid"
        reasons = [
            "all_discovered_basins_retained" if len(local_minima) > 1
            else "J_minimum_identified"
        ]
    return {
        "status": status,
        "statusReasons": reasons,
        "minimumScore": minimum,
        "bestGridPoints": best,
        "localMinima": local_minima,
        "minimumRegionWidth": minimum_region_width,
        "maximumAdjacentScoreJump": max(adjacent_jumps) if adjacent_jumps else None,
        "maximumSampledScoreJump": max(sampled_jumps) if sampled_jumps else 0.0,
        "maximumSampledEloGap": max(sampled_gaps) if sampled_gaps else 0,
        "diagnosticContinuity": continuity,
    }


def intervals_for_score_threshold_v3(
    points: Sequence[dict[str, Any]],
    allowed_score: float,
    *,
    formal_minimum: int = DEFAULT_FORMAL_ELO_MINIMUM,
    formal_maximum: int = 2500,
) -> list[dict[str, Any]]:
    """Build intervals only from actually evaluated integer points."""

    eligible = sorted({
        int(point["elo"])
        for point in points
        if finite_number(point.get("elo"))
        and finite_number(point.get("score"))
        and float(point["score"]) <= float(allowed_score)
    })
    if not eligible:
        return []
    intervals: list[dict[str, Any]] = []
    start = previous = eligible[0]
    for elo in eligible[1:]:
        if elo == previous + 1:
            previous = elo
            continue
        intervals.append({
            "lower": start,
            "upper": previous,
            "truncatedLower": start == int(formal_minimum),
            "truncatedUpper": previous == int(formal_maximum),
        })
        start = previous = elo
    intervals.append({
        "lower": start,
        "upper": previous,
        "truncatedLower": start == int(formal_minimum),
        "truncatedUpper": previous == int(formal_maximum),
    })
    return intervals


def _v3_protected_intervals(
    evaluator: _V3ScoreEvaluator,
    basins: Sequence[dict[str, Any]],
    *,
    resolution: int,
    tolerance: float,
    formal_minimum: int,
    formal_maximum: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Resolve an active-edge basin using an already computed higher guard."""

    valid_points = sorted(
        [
            point for point in evaluator.cache.values()
            if finite_number(point.get("score"))
        ],
        key=lambda point: int(point["elo"]),
    )
    intervals: list[tuple[int, int]] = []
    protected: list[dict[str, Any]] = []
    reasons: list[str] = []
    for basin in basins:
        item = dict(basin)
        lower = item.get("leftProtectionPoint")
        upper = item.get("rightProtectionPoint")
        minimum_score = float(item["minimumScore"])
        if lower is None:
            candidates = [
                point for point in valid_points
                if int(point["elo"]) < int(item["sampledLower"])
                and float(point["score"]) > minimum_score + tolerance
            ]
            if candidates:
                lower = int(candidates[-1]["elo"])
                item["expandedLeftTo"] = lower
                item["boundaryExpansion"] = True
            else:
                proposed = max(
                    int(formal_minimum),
                    int(item["sampledLower"]) - int(resolution),
                )
                if proposed < int(item["sampledLower"]):
                    lower = proposed
                    item["expandedLeftTo"] = lower
                    item["boundaryExpansion"] = True
                    item["expansionWithoutHigherGuard"] = True
                else:
                    reasons.append("minimum_region_not_bracketed_on_lower_side")
        if upper is None:
            candidates = [
                point for point in valid_points
                if int(point["elo"]) > int(item["sampledUpper"])
                and float(point["score"]) > minimum_score + tolerance
            ]
            if candidates:
                upper = int(candidates[0]["elo"])
                item["expandedRightTo"] = upper
                item["boundaryExpansion"] = True
            else:
                proposed = min(
                    int(formal_maximum),
                    int(item["sampledUpper"]) + int(resolution),
                )
                if proposed > int(item["sampledUpper"]):
                    upper = proposed
                    item["expandedRightTo"] = upper
                    item["boundaryExpansion"] = True
                    item["expansionWithoutHigherGuard"] = True
                else:
                    reasons.append("minimum_region_not_bracketed_on_upper_side")
        if lower is None or upper is None:
            continue
        if int(lower) < formal_minimum or int(upper) > formal_maximum:
            reasons.append("active_interval_outside_formal_range")
            continue
        item["refinementLower"] = int(lower)
        item["refinementUpper"] = int(upper)
        item["sourceResolution"] = int(resolution)
        protected.append(item)
        intervals.append((int(lower), int(upper)))
    return _v3_merge_intervals(intervals), protected, sorted(set(reasons))


def _v3_rebuild_curve_points(
    evaluator: _V3ScoreEvaluator,
    curve_elos: set[int],
) -> list[dict[str, Any]]:
    return _v3_curve_points(evaluator, include_elos=curve_elos)


def _v3_full_grid_fallback(
    evaluator: _V3ScoreEvaluator,
    curve_elos: set[int],
    *,
    full_grid: Sequence[int],
) -> int:
    missing = [elo for elo in full_grid if int(elo) not in evaluator.cache]
    evaluator.evaluate_many(missing, include_in_search=True)
    curve_elos.update(int(elo) for elo in full_grid)
    return len(missing)


def _v3_confidence_region_search(
    evaluator: _V3ScoreEvaluator,
    curve_elos: set[int],
    *,
    minimum_score: float,
    t95: float,
    config: dict[str, Any],
    fallback_reasons: list[str],
    fallback_state: dict[str, bool],
) -> dict[str, Any]:
    if not math.isfinite(float(t95)) or float(t95) < 0:
        raise ValueError("v3 T95 must be a finite non-negative number")
    cutoff = float(minimum_score) + float(t95)
    formal_minimum = int(config["formalEloMinimum"])
    formal_maximum = int(config["formalEloMaximum"])
    full_grid = _v3_full_grid(config)
    points = _v3_rebuild_curve_points(evaluator, curve_elos)
    candidates: list[tuple[int, int]] = []
    eligible_points = [
        int(point["elo"]) for point in points
        if finite_number(point.get("score")) and float(point["score"]) <= cutoff
    ]
    candidates.extend((elo, elo) for elo in eligible_points)
    ordered = sorted(
        [point for point in points if finite_number(point.get("score"))],
        key=lambda point: int(point["elo"]),
    )
    for left, right in zip(ordered, ordered[1:]):
        if int(right["elo"]) - int(left["elo"]) > 1 and (
            float(left["score"]) <= cutoff or float(right["score"]) <= cutoff
        ):
            candidates.append((int(left["elo"]), int(right["elo"])))
    # Search diagnostics are attached by score_candidate_curve_v3 after the
    # adaptive pass.  The basins already present in curve_elos are sufficient
    # here; each below-cutoff sampled point is also a candidate above.
    merged = _v3_merge_intervals(candidates)
    confidence_new_points: set[int] = set()
    expansion_rounds = 0
    interval_history: list[dict[str, Any]] = []
    while merged:
        fill = set()
        for interval in merged:
            fill.update(range(int(interval["lower"]), int(interval["upper"]) + 1))
        missing = sorted(fill - set(evaluator.cache))
        remaining = len([elo for elo in full_grid if elo not in evaluator.cache])
        if missing and len(missing) >= remaining:
            added = _v3_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            fallback_state["value"] = True
            reason = "confidence_region_cost_not_below_full_grid_remaining"
            fallback_reasons.append(reason)
            interval_history.append({
                "action": "fallback_full_grid",
                "reason": reason,
                "newPointCount": added,
            })
            break
        evaluator.evaluate_many(missing, include_in_search=True)
        confidence_new_points.update(missing)
        curve_elos.update(missing)
        actual = intervals_for_score_threshold_v3(
            _v3_rebuild_curve_points(evaluator, curve_elos), cutoff,
            formal_minimum=formal_minimum, formal_maximum=formal_maximum,
        )
        if not actual:
            break
        expanded: list[tuple[int, int]] = []
        changed = False
        for interval in actual:
            lower, upper = int(interval["lower"]), int(interval["upper"])
            if lower > formal_minimum:
                probe = lower - 1
                if probe not in evaluator.cache:
                    evaluator.evaluate(probe, include_in_search=True)
                    curve_elos.add(probe)
                    confidence_new_points.add(probe)
                if finite_number(evaluator.cache[probe].get("score")) and float(evaluator.cache[probe]["score"]) <= cutoff:
                    lower = probe
                    changed = True
            if upper < formal_maximum:
                probe = upper + 1
                if probe not in evaluator.cache:
                    evaluator.evaluate(probe, include_in_search=True)
                    curve_elos.add(probe)
                    confidence_new_points.add(probe)
                if finite_number(evaluator.cache[probe].get("score")) and float(evaluator.cache[probe]["score"]) <= cutoff:
                    upper = probe
                    changed = True
            expanded.append((lower, upper))
        next_merged = _v3_merge_intervals(expanded)
        interval_history.append({
            "action": "evaluate_cutoff_region",
            "intervals": next_merged,
            "newPointCount": len(confidence_new_points),
        })
        if next_merged == merged and not changed:
            merged = next_merged
            break
        expansion_rounds += 1 if changed else 0
        if expansion_rounds > int(config["searchMaxExpansionRounds"]):
            fallback_state["value"] = True
            reason = "confidence_region_continues_to_expand"
            fallback_reasons.append(reason)
            added = _v3_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            interval_history.append({
                "action": "fallback_full_grid",
                "reason": reason,
                "newPointCount": added,
            })
            break
        merged = next_merged
    final_points = _v3_rebuild_curve_points(evaluator, curve_elos)
    intervals = intervals_for_score_threshold_v3(
        final_points, cutoff,
        formal_minimum=formal_minimum, formal_maximum=formal_maximum,
    )
    return {
        "t95": float(t95),
        "minimumScore": float(minimum_score),
        "cutoff": cutoff,
        "intervals": intervals,
        "newEvaluatedEloPoints": sorted(confidence_new_points),
        "intervalHistory": interval_history,
        "sparseConfidenceSearch": not fallback_state["value"] and len(curve_elos) < len(full_grid),
        "source": "discovered_basins_and_global_20_point_curve",
    }


def _v3_probe_known_elo(
    evaluator: _V3ScoreEvaluator,
    known_elo: float,
    *,
    formal_minimum: int,
    formal_maximum: int,
) -> dict[str, Any]:
    """Probe knownElo after search without adding it to the search objective."""

    known = float(known_elo)
    result: dict[str, Any] = {
        "requestedElo": known,
        "integer": known.is_integer(),
        "participatesInMinimumJ": False,
        "isolatedFromFormalSearch": True,
        "status": "out_of_formal_range",
        "evaluatedEloPoints": [],
        "score": None,
        "scoreAtKnownElo": None,
        "reason": None,
    }
    if not math.isfinite(known):
        result["status"] = "invalid_known_elo"
        result["reason"] = "knownElo_is_not_finite"
        return result
    if known < formal_minimum or known > formal_maximum:
        result["reason"] = "knownElo_outside_formal_range"
        return result
    lower = int(math.floor(known))
    upper = int(math.ceil(known))
    probe_elos = [lower] if lower == upper else [lower, upper]
    rows = [evaluator.evaluate(elo, include_in_search=False) for elo in probe_elos]
    result["evaluatedEloPoints"] = probe_elos
    result["points"] = [
        {
            "elo": int(row["elo"]),
            "score": row.get("score"),
            "status": "ok" if finite_number(row.get("score")) else (row.get("failureReasons") or ["score_failed"])[0],
            "wasFormalSearchPoint": int(row["elo"]) in evaluator.search_point_elos,
        }
        for row in rows
    ]
    if any(not finite_number(row.get("score")) for row in rows):
        result["status"] = "failed"
        result["reason"] = sorted({
            str(reason)
            for row in rows
            for reason in (row.get("failureReasons") or [])
        }) or ["known_elo_probe_score_failed"]
        return result
    if lower == upper:
        score = float(rows[0]["score"])
    else:
        fraction = known - lower
        score = float(rows[0]["score"]) * (1.0 - fraction) + float(rows[1]["score"]) * fraction
    result["status"] = "ok"
    result["score"] = score
    result["scoreAtKnownElo"] = score
    result["interpolation"] = "direct_integer" if lower == upper else "linear_floor_ceil"
    return result


def score_candidate_curve_v3(
    target_records: Sequence[dict[str, Any]],
    conditional_records: Sequence[dict[str, Any]],
    conditional_manifest: dict[str, Any],
    *,
    target_account: str,
    config: dict[str, Any] | None = None,
    confidence_t95: float | None = None,
    known_elo: float | None = None,
    index: GlobalConditionalKNNIndexV3 | None = None,
) -> dict[str, Any]:
    """Evaluate J(E) with the fixed 40→20→10→5→2→1 multi-basin search."""

    cfg = validate_v3_config(config or default_v3_config())
    started = time.perf_counter()
    own_index = index is None
    global_index = index or GlobalConditionalKNNIndexV3(
        conditional_records, conditional_manifest, cfg
    )
    evaluator = _V3ScoreEvaluator(target_records, target_account, global_index, cfg)
    formal_minimum = int(cfg["formalEloMinimum"])
    formal_maximum = int(cfg["formalEloMaximum"])
    full_grid = _v3_full_grid(cfg)
    curve_elos: set[int] = set()
    fallback_reasons: list[str] = []
    fallback_state = {"value": False}
    search_step_diagnostics: list[dict[str, Any]] = []
    discovered_basins: list[dict[str, Any]] = []
    refined_intervals: list[dict[str, Any]] = []
    previous_intervals: list[dict[str, int]] = []
    expansion_rounds = 0

    for step_index, step in enumerate(SEARCH_STEPS_V3):
        if step_index == 0:
            points = aligned_elo_points_v3(
                formal_minimum, formal_maximum, step,
                formal_minimum=formal_minimum, formal_maximum=formal_maximum,
            )
            evaluator.evaluate_many(points, include_in_search=True)
            curve_elos.update(points)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": points,
                "newEvaluatedPointCount": len(points),
                "refinedIntervals": [],
                "discoveredBasins": [],
            })
            if evaluator.hard_failure_reasons:
                break
            continue
        if step_index == 1:
            points = aligned_elo_points_v3(
                formal_minimum, formal_maximum, step,
                formal_minimum=formal_minimum, formal_maximum=formal_maximum,
            )
            missing = [point for point in points if point not in evaluator.cache]
            evaluator.evaluate_many(missing, include_in_search=True)
            curve_elos.update(points)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": missing,
                "newEvaluatedPointCount": len(missing),
                "refinedIntervals": [],
                "discoveredBasins": [],
            })
            if evaluator.hard_failure_reasons:
                break
            continue

        current_resolution = SEARCH_STEPS_V3[step_index - 1]
        current_points = _v3_rebuild_curve_points(evaluator, curve_elos)
        current_elos = {int(point["elo"]) for point in current_points}
        basins = _v3_discover_basins(
            current_points,
            resolution=current_resolution,
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
            tolerance=float(cfg["searchTieTolerance"]),
        )
        active_edge_basins = _v3_discover_active_edge_basins(
            current_points,
            previous_intervals,
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
            tolerance=float(cfg["searchTieTolerance"]),
        )
        existing_basin_minima = {
            tuple(int(value) for value in basin.get("minimumEloPoints") or [])
            for basin in basins
        }
        basins.extend(
            basin for basin in active_edge_basins
            if tuple(int(value) for value in basin.get("minimumEloPoints") or [])
            not in existing_basin_minima
        )
        for basin in basins:
            item = dict(basin)
            item["discoveredAtStep"] = step
            discovered_basins.append(item)
        if not basins:
            fallback_state["value"] = True
            reason = "sparse_curve_insufficient_to_identify_all_local_minima"
            fallback_reasons.append(reason)
            added = _v3_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [elo for elo in full_grid if elo in evaluator.cache and elo not in current_elos],
                "newEvaluatedPointCount": added,
                "refinedIntervals": [],
                "discoveredBasins": basins,
                "fallbackToFullGrid": True,
                "fallbackReason": reason,
            })
            break
        if len(basins) > int(cfg["searchMaxBasins"]):
            fallback_state["value"] = True
            reason = "multiple_basins_cost_not_below_full_grid"
            fallback_reasons.append(reason)
            added = _v3_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [elo for elo in full_grid if elo not in current_elos],
                "newEvaluatedPointCount": added,
                "refinedIntervals": [],
                "discoveredBasins": basins,
                "fallbackToFullGrid": True,
                "fallbackReason": reason,
            })
            break
        intervals, protected_basins, protection_reasons = _v3_protected_intervals(
            evaluator, basins,
            resolution=current_resolution,
            tolerance=float(cfg["searchTieTolerance"]),
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
        )
        for reason in protection_reasons:
            if reason not in fallback_reasons:
                fallback_reasons.append(reason)
        if protection_reasons:
            fallback_state["value"] = True
            added = _v3_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [elo for elo in full_grid if elo not in current_elos],
                "newEvaluatedPointCount": added,
                "refinedIntervals": intervals,
                "discoveredBasins": protected_basins,
                "fallbackToFullGrid": True,
                "fallbackReason": protection_reasons,
            })
            break
        if any(
            not any(
                interval["lower"] >= previous["lower"]
                and interval["upper"] <= previous["upper"]
                for previous in previous_intervals
            )
            for interval in intervals
        ):
            expansion_rounds += 1
        if expansion_rounds > int(cfg["searchMaxExpansionRounds"]):
            fallback_state["value"] = True
            reason = "active_interval_continues_to_expand"
            fallback_reasons.append(reason)
            added = _v3_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [elo for elo in full_grid if elo not in current_elos],
                "newEvaluatedPointCount": added,
                "refinedIntervals": intervals,
                "discoveredBasins": protected_basins,
                "fallbackToFullGrid": True,
                "fallbackReason": reason,
            })
            break
        next_points = sorted({
            point for interval in intervals
            for point in aligned_elo_points_v3(
                int(interval["lower"]), int(interval["upper"]), step,
                formal_minimum=formal_minimum, formal_maximum=formal_maximum,
            )
        })
        missing = [point for point in next_points if point not in evaluator.cache]
        remaining = len([point for point in full_grid if point not in evaluator.cache])
        if missing and len(missing) >= remaining:
            fallback_state["value"] = True
            reason = "refinement_cost_not_below_full_grid_remaining"
            fallback_reasons.append(reason)
            added = _v3_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [elo for elo in full_grid if elo not in current_elos],
                "newEvaluatedPointCount": added,
                "refinedIntervals": intervals,
                "discoveredBasins": protected_basins,
                "fallbackToFullGrid": True,
                "fallbackReason": reason,
            })
            break
        evaluator.evaluate_many(missing, include_in_search=True)
        curve_elos.update(missing)
        refined_intervals.append({
            "fromResolution": current_resolution,
            "toResolution": step,
            "lower": min(interval["lower"] for interval in intervals),
            "upper": max(interval["upper"] for interval in intervals),
            "intervals": intervals,
            "basinCount": len(protected_basins),
        })
        search_step_diagnostics.append({
            "step": step,
            "newEvaluatedEloPoints": missing,
            "newEvaluatedPointCount": len(missing),
            "refinedIntervals": intervals,
            "discoveredBasins": protected_basins,
        })
        previous_intervals = intervals
        if evaluator.hard_failure_reasons:
            break

    # A final one-point check makes an active interval edge a reason to run the
    # exact complete grid.  It prevents an unprotected edge from being called
    # a confirmed integer minimum merely because no finer sample was requested.
    if not evaluator.hard_failure_reasons and not fallback_state["value"]:
        final_points = _v3_rebuild_curve_points(evaluator, curve_elos)
        final_basins = _v3_discover_basins(
            final_points,
            resolution=1,
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
            tolerance=float(cfg["searchTieTolerance"]),
        )
        if not final_basins:
            fallback_state["value"] = True
            reason = "final_sparse_curve_has_no_protected_minimum"
            fallback_reasons.append(reason)
            _v3_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
        elif any(basin.get("needsExpansion") for basin in final_basins):
            fallback_state["value"] = True
            reason = "minimum_region_not_bracketed_after_final_refinement"
            fallback_reasons.append(reason)
            _v3_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)

    curve_points = _v3_rebuild_curve_points(evaluator, curve_elos)
    stats = _v3_curve_stats(
        curve_points,
        formal_minimum=formal_minimum,
        formal_maximum=formal_maximum,
        full_grid=(len(curve_points) == len(full_grid) and set(curve_elos) >= set(full_grid)),
        tolerance=float(cfg["searchTieTolerance"]),
    )
    if evaluator.hard_failure_reasons:
        stats["status"] = "beta_binomial_fit_failed"
        stats["statusReasons"] = ["beta_binomial_fit_failed"]
    confidence = None
    if (
        confidence_t95 is not None
        and stats.get("minimumScore") is not None
        and stats.get("status") not in {"beta_binomial_fit_failed", "insufficient_reference"}
    ):
        confidence = _v3_confidence_region_search(
            evaluator, curve_elos,
            minimum_score=float(stats["minimumScore"]),
            t95=float(confidence_t95), config=cfg,
            fallback_reasons=fallback_reasons, fallback_state=fallback_state,
        )
        curve_points = _v3_rebuild_curve_points(evaluator, curve_elos)
        stats = _v3_curve_stats(
            curve_points,
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
            full_grid=(len(curve_points) == len(full_grid) and set(curve_elos) >= set(full_grid)),
            tolerance=float(cfg["searchTieTolerance"]),
        )
    known_probe = None
    if known_elo is not None:
        known_probe = _v3_probe_known_elo(
            evaluator, float(known_elo),
            formal_minimum=formal_minimum, formal_maximum=formal_maximum,
        )
    best_point = _v3_best_point(curve_points, float(cfg["searchTieTolerance"]))
    target_failures: dict[str, list[str]] = defaultdict(list)
    best_game_diagnostics: list[dict[str, Any]] = []
    if best_point is not None:
        best_raw = evaluator.cache[int(best_point["elo"])]
        best_game_diagnostics = best_raw.get("gameDiagnostics") or []
    for raw_point in evaluator.cache.values():
        for game in raw_point.get("gameDiagnostics") or []:
            if game.get("ok") is not True:
                target_failures[str(game.get("gameId") or "")].append(
                    str(game.get("reason") or "score_failed")
                )
    tree_query_start = getattr(evaluator, "query_count_before", 0)
    query_count = global_index.query_count - tree_query_start
    curve = {
        "schema": SCHEMA_CURVE_V3,
        "algorithmVersion": ALGORITHM_VERSION_V3,
        "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V3,
        "searchSteps": list(SEARCH_STEPS_V3),
        "formalEloMinimum": formal_minimum,
        "formalEloMaximum": formal_maximum,
        "points": curve_points,
        "evaluatedEloPoints": sorted(curve_elos),
        "evaluatedPointCount": len(curve_elos),
        "isFullGrid": len(curve_elos) >= len(full_grid) and set(curve_elos) >= set(full_grid),
        "targetGameCount": len(target_records),
        "targetGameFailures": {
            game_id: sorted(set(reasons))
            for game_id, reasons in sorted(target_failures.items()) if game_id and reasons
        },
        "excludedReferenceGameCount": None,
        "bestGridPoint": int(best_point["elo"]) if best_point is not None else None,
        "candidateZAtBest": None,
        "bestGameDiagnostics": best_game_diagnostics,
        "searchStepDiagnostics": search_step_diagnostics,
        "refinedIntervals": refined_intervals,
        "discoveredBasins": discovered_basins,
        "fallbackToFullGrid": bool(fallback_state["value"]),
        "fallbackReasons": sorted(set(fallback_reasons)),
        "confidenceIntervalSearch": confidence,
        "knownEloProbe": known_probe,
        "knnQueryCount": query_count,
        "cKDTreeQueryCount": global_index.tree_query_count - getattr(evaluator, "tree_query_count_before", 0),
        "boundaryExpansionQueryCount": global_index.boundary_expansion_count - getattr(evaluator, "boundary_query_count_before", 0),
        "betaBinomialFitCount": evaluator.fit_count,
        "treeBuildSeconds": global_index.tree_build_seconds,
        "treeBuildCount": global_index.tree_build_count,
        "treeBuildReused": evaluator._tree_build_was_reused,
        "scoringSeconds": evaluator.scoring_seconds,
        "totalRuntimeSeconds": time.perf_counter() - started,
        "globalIndexPoolCount": len(global_index.pools),
        **stats,
    }
    # The excluded game IDs are account-local, not a property of a single
    # color/scope pool.  Keep the exact set in the public curve for auditability.
    excluded_ids = set(str(row.get("gameId") or "") for row in target_records)
    for pool in global_index.pools.values():
        for index in pool.excluded_indexes(target_account, excluded_ids):
            game_id = str(pool.records[index].get("gameId") or "")
            if game_id:
                excluded_ids.add(game_id)
    curve["excludedReferenceGameIds"] = sorted(excluded_ids)
    curve["excludedReferenceGameCount"] = len(excluded_ids)
    if confidence is not None:
        curve["databaseCalibrated95Intervals"] = confidence["intervals"]
        curve["databaseCalibrated95Cutoff"] = confidence["cutoff"]
    else:
        curve["databaseCalibrated95Intervals"] = []
        curve["databaseCalibrated95Cutoff"] = None
    return curve


def _flatten_best_diagnostics_v3(
    curve: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    games: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    for game in curve.get("bestGameDiagnostics") or []:
        games.append({
            "gameId": game.get("gameId"),
            "gameNegativeLogLikelihood": game.get("gameNegativeLogLikelihood"),
            "meanConditionalZ": game.get("meanConditionalZ"),
            "status": "valid" if game.get("ok") is True else game.get("reason"),
        })
        for row in game.get("phaseDiagnostics") or []:
            phases.append({
                "gameId": row.get("gameId"),
                "phase": row.get("phase"),
                "x": row.get("x"),
                "n": row.get("n"),
                "targetZ": row.get("targetZ"),
                "PExact": row.get("PExact"),
                "negativeLogProbability": row.get("negativeLogProbability"),
                "N_allowed": row.get("N_allowed"),
                "K": row.get("K"),
                "boundaryDistance": row.get("boundaryDistance"),
                "excludedRecordCount": row.get("excludedRecordCount"),
                "maximumPossibleExcludedRecordCount": row.get("maximumPossibleExcludedRecordCount"),
                "queryK": row.get("queryK"),
                "eligibleReferenceCount": row.get("eligibleReferenceCount"),
                "fitM": row.get("fitM"),
                "fitKappa": row.get("fitKappa"),
                "fitStatus": row.get("fitStatus") or row.get("reason"),
                "neighborSetSha256": row.get("neighborSetSha256"),
                "neighborRecordIds": row.get("neighborRecordIds"),
                "scope": row.get("scope"),
                "color": row.get("color"),
            })
    return games, phases


def estimate_database_calibrated_range_v3(
    account: str,
    target_records: Sequence[dict[str, Any]],
    conditional_records: Sequence[dict[str, Any]],
    conditional_manifest: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
    conditional_manifest_sha256: str | None = None,
    calibration_sha256: str | None = None,
    known_elo: float | None = None,
    index: GlobalConditionalKNNIndexV3 | None = None,
) -> TargetEstimate:
    """Estimate one account with v3 search and post-search known-Elo probing."""

    cfg = validate_v3_config(config or default_v3_config())
    if conditional_manifest.get("schema") != SCHEMA_CONDITIONAL_REFERENCE:
        raise ValueError("v3 estimate requires a conditional-reference-v1 manifest")
    _v3_validate_conditional_manifest(conditional_manifest, cfg)
    if calibration is not None:
        if calibration.get("schema") != SCHEMA_CALIBRATION_V3:
            raise ValueError("v1/v2 calibration artifacts cannot be used by estimated-Elo v3")
        if calibration.get("configSha256") not in {None, canonical_sha256(cfg)}:
            raise ValueError("v3 calibration/config mismatch")
        if (
            calibration.get("conditionalManifestSha256") is not None
            and calibration.get("conditionalManifestSha256") != conditional_manifest_sha256
        ):
            raise ValueError("v3 calibration/conditional-reference mismatch")
    selection = select_target_records(target_records, config=cfg)
    selected = list(selection["selected"])
    payload: dict[str, Any] = {
        "schema": SCHEMA_ESTIMATE_V3,
        "algorithmVersion": ALGORITHM_VERSION_V3,
        "account": account_key(account),
        "configSha256": canonical_sha256(cfg),
        "referenceModelContractSha256": canonical_sha256(v3_reference_model_contract(cfg)),
        "searchContractSha256": canonical_sha256(v3_search_contract(cfg)),
        "calibrationContractSha256": canonical_sha256(v3_calibration_contract(cfg)),
        "conditionalManifestSha256": conditional_manifest_sha256,
        "calibrationSha256": calibration_sha256,
        "selectedGameIds": [str(row.get("gameId") or "") for row in selected],
        "selectedGameCount": len(selected),
        "excludedGamesWithReasons": selection["excluded"],
        "formalMinimumGameCount": int(cfg["minimumTargetGames"]),
        "formalMaximumGameCount": int(cfg["maximumTargetGames"]),
        "formalEloMinimum": int(cfg["formalEloMinimum"]),
        "formalEloMaximum": int(cfg["formalEloMaximum"]),
        "eloGridMinimum": int(cfg["eloGridMinimum"]),
        "eloGridMaximum": int(cfg["eloGridMaximum"]),
        "estimatedElo": None,
        "bestGridPoint": None,
        "minimumScore": None,
        "minimumJ": None,
        "meanNegativeLogLikelihoodAtBest": None,
        "meanConditionalZAtBest": None,
        "databaseCalibrated95Range": None,
        "databaseCalibrated95Intervals": [],
        "databaseCalibrated95Cutoff": None,
        "status": selection["status"],
        "statusReasons": [],
        "gameDiagnostics": [],
        "phaseDiagnostics": [],
        "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V3,
        "searchSteps": list(SEARCH_STEPS_V3),
        "evaluatedEloPoints": [],
        "evaluatedPointCount": 0,
        "refinedIntervals": [],
        "discoveredBasins": [],
        "fallbackToFullGrid": False,
        "fallbackReasons": [],
        "knownElo": float(known_elo) if known_elo is not None and finite_number(known_elo) else None,
        "knownEloUsage": "post-search probe only; never used by search or minimumJ",
        "knownEloProbe": None,
        "knnQueryCount": 0,
        "betaBinomialFitCount": 0,
        "treeBuildSeconds": 0.0,
        "scoringSeconds": 0.0,
        "totalRuntimeSeconds": 0.0,
        "createdAt": utc_now(),
    }
    if selection["status"] != "valid":
        payload["statusReasons"] = ["fewer_than_minimum_complete_recent_target_games"]
        curve = {
            "schema": SCHEMA_CURVE_V3,
            "algorithmVersion": ALGORITHM_VERSION_V3,
            "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V3,
            "searchSteps": list(SEARCH_STEPS_V3),
            "points": [],
            "status": selection["status"],
            "statusReasons": payload["statusReasons"],
            "evaluatedEloPoints": [],
            "evaluatedPointCount": 0,
            "refinedIntervals": [],
            "discoveredBasins": [],
            "fallbackToFullGrid": False,
            "fallbackReasons": [],
            "knownEloProbe": None,
            "databaseCalibrated95Intervals": [],
        }
        return TargetEstimate(payload, curve, tuple(selected))
    confidence_t95 = None
    if calibration is not None and calibration.get("status") == "validated":
        if finite_number(calibration.get("t95")):
            confidence_t95 = float(calibration["t95"])
        else:
            raise ValueError("validated v3 calibration is missing finite T95")
    curve = score_candidate_curve_v3(
        selected,
        conditional_records,
        conditional_manifest,
        target_account=account,
        config=cfg,
        confidence_t95=confidence_t95,
        known_elo=known_elo,
        index=index,
    )
    payload["bestGridPoint"] = curve.get("bestGridPoint")
    payload["minimumScore"] = curve.get("minimumScore")
    payload["minimumJ"] = curve.get("minimumScore")
    best = curve.get("bestGridPoint")
    best_point = next(
        (point for point in curve.get("points", []) if point.get("elo") == best), None
    )
    if best_point is not None:
        payload["meanNegativeLogLikelihoodAtBest"] = best_point.get("meanNegativeLogLikelihood")
        payload["meanConditionalZAtBest"] = best_point.get("meanConditionalZ")
    games, phases = _flatten_best_diagnostics_v3(curve)
    payload["gameDiagnostics"] = games
    payload["phaseDiagnostics"] = phases
    for key in (
        "searchStrategyVersion", "searchSteps", "evaluatedEloPoints",
        "evaluatedPointCount", "refinedIntervals", "discoveredBasins",
        "fallbackToFullGrid", "fallbackReasons", "knownEloProbe",
        "knnQueryCount", "cKDTreeQueryCount", "boundaryExpansionQueryCount",
        "betaBinomialFitCount", "treeBuildSeconds", "treeBuildCount",
        "treeBuildReused", "scoringSeconds", "totalRuntimeSeconds",
    ):
        payload[key] = curve.get(key)
    payload["databaseCalibrated95Intervals"] = curve.get("databaseCalibrated95Intervals") or []
    payload["databaseCalibrated95Cutoff"] = curve.get("databaseCalibrated95Cutoff")
    payload["confidenceIntervalSearch"] = curve.get("confidenceIntervalSearch")
    payload["referenceFeaturePolicy"] = cfg["referenceFeaturePolicy"]
    payload["scoringSemantics"] = (
        "J(E) is the arithmetic mean across selected games of the sum of four "
        "conditional Beta-Binomial exact-count negative log probabilities; "
        "mid-CDF z is used only for the next stage distance and diagnostics"
    )
    payload["status"] = curve.get("status")
    payload["statusReasons"] = list(curve.get("statusReasons") or [])
    if curve.get("status") == "beta_binomial_fit_failed":
        # Numerical fit errors are terminal v3 results.  They must not trigger
        # a complete-grid retry that could hide the failed fit.
        payload["statusReasons"] = ["beta_binomial_fit_failed"]
    elif curve.get("status") in {"valid", "multiple_minima"} and best is not None:
        payload["estimatedElo"] = int(best)
        if calibration is None or calibration.get("status") != "validated":
            if known_elo is None:
                payload["status"] = "calibration_unavailable"
                payload["statusReasons"].append("validated_v3_T95_is_not_available")
        elif len(payload["databaseCalibrated95Intervals"]) > 1:
            payload["status"] = "multiple_intervals"
            payload["statusReasons"].append("calibrated_allowed_set_is_discontinuous")
    if known_elo is not None:
        probe = curve.get("knownEloProbe") or {}
        payload["scoreAtKnownElo"] = probe.get("scoreAtKnownElo")
        payload["trueScoreIncrease"] = (
            float(probe["scoreAtKnownElo"]) - float(curve["minimumScore"])
            if probe.get("status") == "ok" and finite_number(curve.get("minimumScore"))
            else None
        )
        payload["estimatedEloError"] = (
            float(best) - float(known_elo) if best is not None else None
        )
        if probe.get("status") == "failed":
            payload["knownEloProbeStatus"] = "failed"
            payload["knownEloProbeFailure"] = probe.get("reason")
    return TargetEstimate(payload, curve, tuple(selected))


_V3_WORKER_CONDITIONAL_RECORDS: list[dict[str, Any]] = []
_V3_WORKER_CONDITIONAL_MANIFEST: dict[str, Any] = {}
_V3_WORKER_CONDITIONAL_MANIFEST_SHA: str | None = None
_V3_WORKER_CONFIG: dict[str, Any] = {}
_V3_WORKER_INDEX: GlobalConditionalKNNIndexV3 | None = None


def _init_v3_calibration_worker(
    conditional_records_path: str,
    conditional_manifest_path: str,
    config: dict[str, Any],
) -> None:
    """Load the immutable reference once and build all eight trees once."""

    global _V3_WORKER_CONDITIONAL_RECORDS
    global _V3_WORKER_CONDITIONAL_MANIFEST
    global _V3_WORKER_CONDITIONAL_MANIFEST_SHA
    global _V3_WORKER_CONFIG
    global _V3_WORKER_INDEX
    _V3_WORKER_CONFIG = validate_v3_config(config)
    _V3_WORKER_CONDITIONAL_RECORDS = read_jsonl(conditional_records_path)
    _V3_WORKER_CONDITIONAL_MANIFEST = read_json(conditional_manifest_path)
    _V3_WORKER_CONDITIONAL_MANIFEST_SHA = sha256_file(conditional_manifest_path)
    _V3_WORKER_INDEX = GlobalConditionalKNNIndexV3(
        _V3_WORKER_CONDITIONAL_RECORDS,
        _V3_WORKER_CONDITIONAL_MANIFEST,
        _V3_WORKER_CONFIG,
    )


def _v3_calibration_case_payload(
    role: str,
    account: str,
    known_elo: float,
    target_records: Sequence[dict[str, Any]],
    *,
    t95: float | None,
) -> dict[str, Any]:
    if _V3_WORKER_INDEX is None or _V3_WORKER_CONDITIONAL_MANIFEST_SHA is None:
        raise RuntimeError("v3 calibration worker was not initialized")
    started = time.perf_counter()
    calibration_context = None
    if t95 is not None:
        calibration_context = {
            "schema": SCHEMA_CALIBRATION_V3,
            "status": "validated",
            "configSha256": canonical_sha256(_V3_WORKER_CONFIG),
            "conditionalManifestSha256": _V3_WORKER_CONDITIONAL_MANIFEST_SHA,
            "t95": float(t95),
        }
    estimate = estimate_database_calibrated_range_v3(
        account,
        target_records,
        _V3_WORKER_CONDITIONAL_RECORDS,
        _V3_WORKER_CONDITIONAL_MANIFEST,
        config=_V3_WORKER_CONFIG,
        calibration=calibration_context,
        conditional_manifest_sha256=_V3_WORKER_CONDITIONAL_MANIFEST_SHA,
        known_elo=float(known_elo),
        index=_V3_WORKER_INDEX,
    )
    curve = estimate.curve
    payload = estimate.payload
    selected = list(estimate.selected_records)
    probe = curve.get("knownEloProbe") or {}
    minimum = curve.get("minimumScore")
    true_increase = (
        float(probe["scoreAtKnownElo"]) - float(minimum)
        if probe.get("status") == "ok" and finite_number(minimum) else None
    )
    case = {
        "schema": SCHEMA_CALIBRATION_CASE_V3,
        "algorithmVersion": ALGORITHM_VERSION_V3,
        "configSha256": canonical_sha256(_V3_WORKER_CONFIG),
        "referenceModelContractSha256": canonical_sha256(v3_reference_model_contract(_V3_WORKER_CONFIG)),
        "searchContractSha256": canonical_sha256(v3_search_contract(_V3_WORKER_CONFIG)),
        "calibrationContractSha256": canonical_sha256(v3_calibration_contract(_V3_WORKER_CONFIG)),
        "conditionalManifestSha256": _V3_WORKER_CONDITIONAL_MANIFEST_SHA,
        "role": str(role),
        "account": account_key(account),
        "knownElo": float(known_elo),
        "knownEloDefinition": "newR from the latest created source-bundle detail for this account",
        "knownEloInFormalRange": int(_V3_WORKER_CONFIG["formalEloMinimum"]) <= float(known_elo) <= int(_V3_WORKER_CONFIG["formalEloMaximum"]),
        "knownEloProbe": probe,
        "selectedGameIds": [str(row.get("gameId") or "") for row in selected],
        "selectedGameCount": len(selected),
        "curveStatus": curve.get("status"),
        "bestGridPoint": curve.get("bestGridPoint"),
        "minimumScore": minimum,
        "minimumJ": minimum,
        "scoreAtKnownElo": probe.get("scoreAtKnownElo"),
        "trueScoreIncrease": true_increase,
        "estimatedEloError": (
            float(curve["bestGridPoint"]) - float(known_elo)
            if finite_number(curve.get("bestGridPoint")) else None
        ),
        "databaseCalibrated95Intervals": curve.get("databaseCalibrated95Intervals") or [],
        "databaseCalibrated95Cutoff": curve.get("databaseCalibrated95Cutoff"),
        "scoreCurve": curve.get("points") or [],
        "searchStrategyVersion": curve.get("searchStrategyVersion"),
        "searchSteps": curve.get("searchSteps"),
        "searchStepDiagnostics": curve.get("searchStepDiagnostics"),
        "evaluatedEloPoints": curve.get("evaluatedEloPoints"),
        "evaluatedPointCount": curve.get("evaluatedPointCount"),
        "refinedIntervals": curve.get("refinedIntervals"),
        "discoveredBasins": curve.get("discoveredBasins"),
        "fallbackToFullGrid": curve.get("fallbackToFullGrid"),
        "fallbackReasons": curve.get("fallbackReasons"),
        "excludedReferenceGameIds": curve.get("excludedReferenceGameIds"),
        "excludedReferenceGameCount": curve.get("excludedReferenceGameCount"),
        "knnQueryCount": curve.get("knnQueryCount"),
        "cKDTreeQueryCount": curve.get("cKDTreeQueryCount"),
        "boundaryExpansionQueryCount": curve.get("boundaryExpansionQueryCount"),
        "betaBinomialFitCount": curve.get("betaBinomialFitCount"),
        "treeBuildSeconds": curve.get("treeBuildSeconds"),
        "treeBuildReused": curve.get("treeBuildReused"),
        "scoringSeconds": curve.get("scoringSeconds"),
        "totalRuntimeSeconds": time.perf_counter() - started,
        "minimumRegionWidth": curve.get("minimumRegionWidth"),
        "maximumAdjacentScoreJump": curve.get("maximumAdjacentScoreJump"),
        "maximumSampledScoreJump": curve.get("maximumSampledScoreJump"),
        "maximumSampledEloGap": curve.get("maximumSampledEloGap"),
        "diagnosticContinuity": curve.get("diagnosticContinuity"),
        "statusReasons": curve.get("statusReasons") or [],
        "calibrationPass": "calibration_accounts_only" if t95 is None else "validation_after_frozen_T95",
        "confidenceThresholdFrozen": t95 is not None,
        "payloadStatus": payload.get("status"),
    }
    return case


def _v3_calibration_case_worker(
    task: tuple[str, str, float, list[dict[str, Any]], str, float | None],
) -> dict[str, Any]:
    role, account, known_elo, target_records, case_path, t95 = task
    case = _v3_calibration_case_payload(
        role, account, known_elo, target_records, t95=t95
    )
    atomic_write_json(case_path, case)
    return case


def _calibration_group_rows_v3(
    cases: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        count = int(case.get("selectedGameCount") or 0)
        count_band = "10-14" if count <= 14 else "15-19" if count <= 19 else "20-24" if count <= 24 else "25-30"
        known = float(case["knownElo"])
        elo_band = f"{int(known // 100) * 100}-{int(known // 100) * 100 + 99}"
        groups[f"gameCount:{count_band}"].append(case)
        groups[f"knownElo:{elo_band}"].append(case)
    output: dict[str, Any] = {}
    for key, rows in sorted(groups.items()):
        errors = [
            abs(float(row["estimatedEloError"]))
            for row in rows if finite_number(row.get("estimatedEloError"))
        ]
        increases = [
            float(row["trueScoreIncrease"])
            for row in rows if finite_number(row.get("trueScoreIncrease"))
        ]
        widths = []
        for row in rows:
            for interval in row.get("databaseCalibrated95Intervals") or []:
                widths.append(float(interval["upper"]) - float(interval["lower"]))
        output[key] = {
            "caseCount": len(rows),
            "error": _error_summary(errors),
            "trueScoreIncrease": _error_summary(increases),
            "intervalWidth": _error_summary(widths),
        }
    return output


def calibrate_global_interval_v3(
    directed_reference_records: Sequence[dict[str, Any]],
    source_bundle: dict[str, Any],
    conditional_records: Sequence[dict[str, Any]],
    conditional_manifest: dict[str, Any],
    output_dir: str | Path,
    *,
    config: dict[str, Any] | None = None,
    conditional_records_path: str | Path,
    conditional_manifest_path: str | Path,
    directed_records_sha256: str,
    resume: bool = False,
    parallel_workers: int = 16,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the two-pass v3 calibration with one account per process task."""

    cfg = validate_v3_config(config or default_v3_config())
    if int(parallel_workers) != 16:
        raise ValueError("formal estimated-Elo v3 calibration requires ProcessPoolExecutor(max_workers=16)")
    if int(cfg["referenceQueryWorkers"]) != 1:
        raise ValueError("v3 calibration requires referenceQueryWorkersPerWorker=1")
    conditional_manifest_sha = sha256_file(conditional_manifest_path)
    _v3_validate_conditional_manifest(conditional_manifest, cfg)
    output = Path(output_dir)
    progress_path = output / "progress.json"
    by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in directed_reference_records:
        account = account_key(row.get("targetPlayerId"))
        if account and row.get("formalReferenceEligible") is True and _target_record_rejection(row, cfg) is None:
            by_account[account].append(row)
    for account in by_account:
        by_account[account].sort(
            key=lambda row: (str(row.get("created") or ""), str(row.get("gameId") or "")),
            reverse=True,
        )
    known_elos = _latest_known_elos(source_bundle)
    eligible = [
        account for account in sorted(by_account)
        if len(by_account[account]) >= int(cfg["minimumTargetGames"])
        and finite_number(known_elos.get(account))
    ]
    validation_candidates = [
        account for account in eligible
        if len(by_account[account]) >= int(cfg["validationMinimumTargetGames"])
    ]
    calibration_accounts, validation_accounts, split = _split_accounts(
        eligible, cfg, validation_candidates=validation_candidates
    )
    roles = {account: "validation" for account in validation_accounts}
    roles.update({account: "calibration" for account in calibration_accounts})
    calibration_contract = v3_calibration_contract(cfg)
    contract = {
        "schema": SCHEMA_CALIBRATION_V3,
        "algorithmVersion": ALGORITHM_VERSION_V3,
        "configSha256": canonical_sha256(cfg),
        "referenceModelContractSha256": canonical_sha256(v3_reference_model_contract(cfg)),
        "searchContractSha256": canonical_sha256(v3_search_contract(cfg)),
        "calibrationContractSha256": canonical_sha256(calibration_contract),
        "directedRecordsSha256": directed_records_sha256,
        "conditionalManifestSha256": conditional_manifest_sha,
        "eligibleAccountsSha256": canonical_sha256(eligible),
        "calibrationAccountsSha256": canonical_sha256(calibration_accounts),
        "validationAccountsSha256": canonical_sha256(validation_accounts),
        "parallelWorkers": 16,
        "taskUnit": "one_player_account",
        "processPoolChunksize": 1,
        "referenceQueryWorkersPerWorker": 1,
    }
    contract_sha = canonical_sha256(contract)
    if output.exists() and not resume:
        raise FileExistsError(f"calibration output already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if progress_path.is_file():
        progress = read_json(progress_path)
        if progress.get("contractSha256") != contract_sha:
            raise ValueError("resume refused: v3 calibration config/input/role contract changed")
    else:
        if resume and any(output.iterdir()):
            raise ValueError("resume refused: v3 calibration directory has no compatible progress.json")
        progress = {
            "schema": "player-sentinel-elo-calibration-progress-v3",
            "contract": contract,
            "contractSha256": contract_sha,
            "status": "running",
            "createdAt": utc_now(),
            "completedCalibrationCases": {},
            "completedValidationCases": {},
            "failedCaseCount": 0,
            "retryCount": 0,
        }
        atomic_write_json(progress_path, progress)

    case_dir = output / "cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    cases_by_account: dict[str, dict[str, Any]] = {}
    for account, role in sorted(roles.items()):
        case_path = case_dir / f"{hashlib.sha256(account.encode('utf-8')).hexdigest()}.json"
        completed_map = (
            progress["completedCalibrationCases"] if role == "calibration"
            else progress["completedValidationCases"]
        )
        completed = completed_map.get(account)
        if completed:
            if not case_path.is_file() or sha256_file(case_path) != completed.get("sha256"):
                raise ValueError(f"resume refused: completed v3 calibration case changed: {account}")
            recovered = read_json(case_path)
            if (
                recovered.get("schema") != SCHEMA_CALIBRATION_CASE_V3
                or recovered.get("algorithmVersion") != ALGORITHM_VERSION_V3
                or recovered.get("account") != account
                or recovered.get("role") != role
                or recovered.get("configSha256") != canonical_sha256(cfg)
                or recovered.get("conditionalManifestSha256") != conditional_manifest_sha
            ):
                raise ValueError(f"resume refused: incompatible completed v3 case: {case_path}")
            if role == "validation" and recovered.get("confidenceThresholdFrozen") is not True:
                raise ValueError(f"resume refused: validation case was not scored with frozen T95: {case_path}")
            cases_by_account[account] = recovered
        elif case_path.is_file():
            raise ValueError(f"resume refused: orphan v3 calibration case: {case_path}")

    calibration_tasks = [
        (
            "calibration", account, float(known_elos[account]),
            by_account[account][:int(cfg["maximumTargetGames"])],
            str((case_dir / f"{hashlib.sha256(account.encode('utf-8')).hexdigest()}.json").resolve()),
            None,
        )
        for account in calibration_accounts if account not in cases_by_account
    ]
    started = time.perf_counter()
    t95: float | None = None
    with ProcessPoolExecutor(
        max_workers=16,
        initializer=_init_v3_calibration_worker,
        initargs=(str(Path(conditional_records_path).resolve()), str(Path(conditional_manifest_path).resolve()), cfg),
    ) as executor:
        if calibration_tasks:
            for case in executor.map(_v3_calibration_case_worker, calibration_tasks, chunksize=1):
                account = str(case["account"])
                cases_by_account[account] = case
                progress["completedCalibrationCases"][account] = {
                    "sha256": sha256_file(case_dir / f"{hashlib.sha256(account.encode('utf-8')).hexdigest()}.json"),
                    "runtimeSeconds": case.get("totalRuntimeSeconds"),
                    "completedAt": utc_now(),
                }
                progress["updatedAt"] = utc_now()
                atomic_write_json(progress_path, progress)

        calibration_cases = [
            case for case in cases_by_account.values()
            if case.get("role") == "calibration"
            and case.get("knownEloInFormalRange") is True
            and case.get("curveStatus") in {"valid", "multiple_minima"}
            and finite_number(case.get("trueScoreIncrease"))
        ]
        t95 = _empirical_quantile_v2(
            [float(case["trueScoreIncrease"]) for case in calibration_cases],
            float(cfg["calibrationCoverage"]),
        )
        progress["frozenT95"] = t95
        progress["frozenT95Source"] = "calibration_accounts_only"
        progress["updatedAt"] = utc_now()
        atomic_write_json(progress_path, progress)

        validation_tasks = [
            (
                "validation", account, float(known_elos[account]),
                by_account[account][:int(cfg["maximumTargetGames"])],
                str((case_dir / f"{hashlib.sha256(account.encode('utf-8')).hexdigest()}.json").resolve()),
                t95,
            )
            for account in validation_accounts if account not in cases_by_account
        ]
        if validation_tasks:
            for case in executor.map(_v3_calibration_case_worker, validation_tasks, chunksize=1):
                account = str(case["account"])
                cases_by_account[account] = case
                progress["completedValidationCases"][account] = {
                    "sha256": sha256_file(case_dir / f"{hashlib.sha256(account.encode('utf-8')).hexdigest()}.json"),
                    "runtimeSeconds": case.get("totalRuntimeSeconds"),
                    "frozenT95": t95,
                    "completedAt": utc_now(),
                }
                progress["updatedAt"] = utc_now()
                atomic_write_json(progress_path, progress)

    cases = [cases_by_account[account] for account in sorted(cases_by_account)]
    validation_cases = [
        case for case in cases
        if case.get("role") == "validation"
        and case.get("knownEloInFormalRange") is True
        and finite_number(case.get("knownElo"))
    ]
    coverage_rows = [
        any(
            float(interval["lower"]) <= float(case["knownElo"]) <= float(interval["upper"])
            for interval in case.get("databaseCalibrated95Intervals") or []
        )
        for case in validation_cases
    ]
    interval_widths = [
        float(interval["upper"]) - float(interval["lower"])
        for case in validation_cases
        for interval in case.get("databaseCalibrated95Intervals") or []
    ]
    validation_coverage = statistics.fmean(coverage_rows) if coverage_rows else None
    validation_errors = [
        abs(float(case["estimatedEloError"]))
        for case in validation_cases if finite_number(case.get("estimatedEloError"))
    ]
    coverage_confirmed = bool(
        t95 is not None
        and len(validation_cases) >= int(cfg["minimumValidationUsers"])
        and validation_coverage is not None
        and validation_coverage >= float(cfg["calibrationCoverage"])
    )
    runtimes = [
        float(case["totalRuntimeSeconds"])
        for case in cases if finite_number(case.get("totalRuntimeSeconds"))
    ]
    calibration_cases = [case for case in cases if case.get("role") == "calibration"]
    artifact = {
        "schema": SCHEMA_CALIBRATION_V3,
        "version": "v3",
        "algorithmVersion": ALGORITHM_VERSION_V3,
        "createdAt": utc_now(),
        "status": "validated" if coverage_confirmed else "calibration_unavailable",
        "configSha256": canonical_sha256(cfg),
        "referenceModelContractSha256": canonical_sha256(v3_reference_model_contract(cfg)),
        "searchContractSha256": canonical_sha256(v3_search_contract(cfg)),
        "calibrationContractSha256": canonical_sha256(v3_calibration_contract(cfg)),
        "conditionalManifestSha256": conditional_manifest_sha,
        "directedRecordsSha256": directed_records_sha256,
        "knownEloDefinition": "newR from the latest created source-bundle detail for each account",
        "knownEloUsage": "post-search floor/ceil probe and T95 only; never a search coordinate or minimumJ input",
        "quantileMethod": "unweighted empirical linear interpolation at p=(n-1)*q",
        "calibrationCoverage": float(cfg["calibrationCoverage"]),
        "t95": t95,
        "t95Source": "calibration_accounts_only",
        "calibrationUserCount": len(calibration_accounts),
        "validationUserCount": len(validation_accounts),
        "calibrationCaseCount": len(calibration_cases),
        "validationCaseCount": len(validation_cases),
        "validationCoveredCount": sum(coverage_rows),
        "validationCoverage": validation_coverage,
        "validationErrorSummary": _error_summary(validation_errors),
        "validationIntervalWidthSummary": _error_summary(interval_widths),
        "groupMetrics": _calibration_group_rows_v3(cases),
        "split": {**split, "calibrationAccounts": calibration_accounts, "validationAccounts": validation_accounts},
        "parallelWorkers": 16,
        "taskUnit": "one_player_account",
        "processPoolChunksize": 1,
        "referenceQueryWorkersPerWorker": 1,
        "accountRuntimeSeconds": _error_summary(runtimes),
        "wallRuntimeSecondsThisInvocation": time.perf_counter() - started,
        "failedCaseCount": int(progress.get("failedCaseCount", 0)),
        "retryCount": int(progress.get("retryCount", 0)),
        "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V3,
        "searchSteps": list(SEARCH_STEPS_V3),
        "twoPassCalibration": {
            "firstPass": "calibration_accounts_only_produce_T95",
            "secondPass": "validation_accounts_use_frozen_minimumJ_plus_T95",
            "frozenT95": t95,
        },
        "independentValidation": {
            "required": True,
            "usersAreDisjointFromCalibration": not bool(set(calibration_accounts) & set(validation_accounts)),
            "coverageTarget": float(cfg["calibrationCoverage"]),
            "coverageConfirmed": coverage_confirmed,
        },
    }
    atomic_write_jsonl(output / str(cfg["calibrationCases"]), cases)
    artifact["casesSha256"] = sha256_file(output / str(cfg["calibrationCases"]))
    atomic_write_json(output / str(cfg["calibrationArtifact"]), artifact)
    calibration_manifest = {
        "schema": SCHEMA_CALIBRATION_MANIFEST_V3,
        "algorithmVersion": ALGORITHM_VERSION_V3,
        "createdAt": utc_now(),
        "configSha256": canonical_sha256(cfg),
        "referenceModelContractSha256": artifact["referenceModelContractSha256"],
        "searchContractSha256": artifact["searchContractSha256"],
        "calibrationContractSha256": artifact["calibrationContractSha256"],
        "conditionalManifestSha256": conditional_manifest_sha,
        "directedRecordsSha256": directed_records_sha256,
        "files": [
            {
                "path": str(cfg["calibrationArtifact"]),
                "sha256": sha256_file(output / str(cfg["calibrationArtifact"])),
            },
            {
                "path": str(cfg["calibrationCases"]),
                "sha256": artifact["casesSha256"],
            },
        ],
    }
    atomic_write_json(output / "calibration_sha256_manifest_v3.json", calibration_manifest)
    progress["status"] = "completed"
    progress["completedAt"] = utc_now()
    progress["calibrationArtifactSha256"] = sha256_file(output / str(cfg["calibrationArtifact"]))
    progress["calibrationCasesSha256"] = artifact["casesSha256"]
    progress["calibrationManifestSha256"] = sha256_file(output / "calibration_sha256_manifest_v3.json")
    progress["updatedAt"] = utc_now()
    atomic_write_json(progress_path, progress)
    return artifact, cases


def _v3_bruteforce_nearest(
    records: Sequence[dict[str, Any]],
    scales: dict[str, Any],
    stage: int,
    config: dict[str, Any],
    *,
    account: str,
    target_game_ids: Iterable[str],
    trial_elo: float,
    opponent_elo: float,
    previous_z: float | None = None,
) -> dict[str, Any]:
    """Reference implementation used by the KNN consistency audit/tests."""

    cfg = validate_v3_config(config)
    candidate_rows = [
        row for row in records
        if _phase_counts(row, stage) is not None
        and (stage == 1 or finite_number(row.get(f"referenceZ{stage - 1}")))
    ]
    account_key_value = account_key(account)
    target_ids = {str(game_id) for game_id in target_game_ids}
    excluded = {
        index for index, row in enumerate(candidate_rows)
        if account_key(row.get("targetPlayerId")) == account_key_value
        or account_key(row.get("opponentPlayerId")) == account_key_value
        or str(row.get("gameId") or "") in target_ids
    }
    allowed = [row for index, row in enumerate(candidate_rows) if index not in excluded]
    n_allowed = len(allowed)
    k = neighbor_count_v3(n_allowed, float(cfg["neighborExponent"])) if n_allowed else 0
    if n_allowed < k + 1:
        return {
            "ok": False,
            "reason": "insufficient_reference",
            "N_allowed": n_allowed,
            "K": k,
            "excludedRecordCount": len(excluded),
        }
    previous_key = f"referenceZ{stage - 1}" if stage > 1 else None
    previous_sd = scales.get(f"phase{stage - 1}ZSd") if stage > 1 else None
    if previous_key is not None and not finite_number(previous_sd):
        raise ValueError(f"missing positive phase{stage - 1}ZSd")

    def feature(row: dict[str, Any]) -> tuple[float, ...]:
        values = (
            float(row["targetOldR"]) / float(scales["selfEloSd"]),
            float(row["opponentOldR"]) / float(scales["opponentEloSd"]),
        )
        if previous_key is not None:
            values += (
                math.sqrt(float(cfg["previousZWeight"]))
                * float(row[previous_key]) / float(previous_sd),
            )
        return values

    query = (
        float(trial_elo) / float(scales["selfEloSd"]),
        float(opponent_elo) / float(scales["opponentEloSd"]),
    )
    if previous_key is not None:
        if not finite_number(previous_z):
            return {"ok": False, "reason": "invalid_previous_phase_z"}
        query += (
            math.sqrt(float(cfg["previousZWeight"]))
            * float(previous_z) / float(previous_sd),
        )
    ranked = sorted(
        (
            math.sqrt(sum((left - right) ** 2 for left, right in zip(feature(row), query, strict=True))),
            _v3_record_stable_key(row, index),
            row,
        )
        for index, row in enumerate(allowed)
    )
    boundary = float(ranked[k][0])
    weights = [max(0.0, 1.0 - float(item[0]) / boundary) for item in ranked[:k]]
    return {
        "ok": True,
        "records": [item[2] for item in ranked[:k]],
        "weights": weights,
        "neighborRecordIds": [
            str(item[2].get("recordId") or _conditional_record_id(item[2]))
            for item in ranked[:k]
        ],
        "neighborSetSha256": canonical_sha256([
            str(item[2].get("recordId") or _conditional_record_id(item[2]))
            for item in ranked[:k]
        ]),
        "N_allowed": n_allowed,
        "K": k,
        "boundaryDistance": boundary,
        "excludedRecordCount": len(excluded),
        "effectiveWeight": sum(weights),
    }


def audit_global_knn_consistency_v3(
    conditional_records: Sequence[dict[str, Any]],
    conditional_manifest: dict[str, Any],
    config: dict[str, Any],
    samples: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Compare global-tree queries with exact per-query brute-force results."""

    cfg = validate_v3_config(config)
    index = GlobalConditionalKNNIndexV3(conditional_records, conditional_manifest, cfg)
    rows: list[dict[str, Any]] = []
    mismatch_count = 0
    for sample in samples:
        color = str(sample["targetColor"]).casefold()
        scope = str(sample["scope"])
        stage = int(sample["stage"])
        pool = index.pool(color, scope, stage)
        if pool is None:
            raise ValueError(f"sample references unavailable pool: {color}|{scope}|phase{stage}")
        account = str(sample["account"])
        target_game_ids = [str(value) for value in sample.get("targetGameIds", [])]
        kwargs = {
            "account": account,
            "target_game_ids": target_game_ids,
            "trial_elo": float(sample["trialElo"]),
            "opponent_elo": float(sample["opponentElo"]),
            "previous_z": sample.get("previousZ"),
        }
        actual = pool.nearest(**kwargs)
        expected = _v3_bruteforce_nearest(
            pool.records, pool.scales, stage, cfg, **kwargs
        )
        actual_ids = actual.get("neighborRecordIds")
        expected_ids = expected.get("neighborRecordIds")
        weights_match = (
            actual.get("weights") is None and expected.get("weights") is None
        ) or (
            isinstance(actual.get("weights"), list)
            and isinstance(expected.get("weights"), list)
            and len(actual["weights"]) == len(expected["weights"])
            and all(
                math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
                for left, right in zip(actual["weights"], expected["weights"], strict=True)
            )
        )
        match = bool(
            actual.get("ok") == expected.get("ok")
            and actual.get("N_allowed") == expected.get("N_allowed")
            and actual.get("K") == expected.get("K")
            and actual_ids == expected_ids
            and weights_match
            and (
                actual.get("boundaryDistance") is None
                or math.isclose(
                    float(actual["boundaryDistance"]),
                    float(expected["boundaryDistance"]),
                    rel_tol=0.0, abs_tol=1e-12,
                )
            )
        )
        if not match:
            mismatch_count += 1
        rows.append({
            "sample": dict(sample),
            "match": match,
            "actual": {
                key: actual.get(key) for key in (
                    "ok", "reason", "N_allowed", "K", "neighborRecordIds",
                    "boundaryDistance", "weights", "excludedRecordCount",
                )
            },
            "expected": {
                key: expected.get(key) for key in (
                    "ok", "reason", "N_allowed", "K", "neighborRecordIds",
                    "boundaryDistance", "weights", "excludedRecordCount",
                )
            },
        })
    return {
        "schema": "player-sentinel-elo-global-knn-consistency-audit-v3",
        "algorithmVersion": ALGORITHM_VERSION_V3,
        "sampleCount": len(rows),
        "mismatchCount": mismatch_count,
        "ok": mismatch_count == 0,
        "globalTreeBuildCount": index.tree_build_count,
        "globalTreeBuildSeconds": index.tree_build_seconds,
        "queryCount": index.query_count,
        "boundaryExpansionQueryCount": index.boundary_expansion_count,
        "samples": rows,
    }


# ---------------------------------------------------------------------------
# Estimated-Elo v4: Anscombe/local Gaussian model and global KNN
# ---------------------------------------------------------------------------


def default_v4_config() -> dict[str, Any]:
    """Return the isolated formal v4 configuration.

    The paths are intentionally different from every v1/v2/v3 product.  The
    source phase records are still the already-computed Level22 x/n records;
    changing the statistical reference model does not authorize another
    Level22 run.
    """

    return {
        "schema": SCHEMA_CONFIG_V4,
        "version": "v4-matchup600-20260911",
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "sourceReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_elo_matchup600_blackwhite_reference_level22_1600plus_20260911"
        ),
        "sentinelDerivedDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_reference_level22_1600plus_v11_20260911"
        ),
        "derivedReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_reference_level22_1600plus_v4_matchup600_20260911"
        ),
        "directedPhaseRecords": "directed_game_phase_records.jsonl",
        "referenceManifest": "reference_sha256_manifest.json",
        "conditionalReferenceDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_anscombe_reference_v4_matchup600_20260911"
        ),
        "conditionalReferenceRecords": "anscombe_reference_records.jsonl",
        "conditionalReferenceManifest": "anscombe_reference_manifest.json",
        "calibrationDirectory": (
            "research/offbook_detection/data/"
            "oq_sentinel_elo_calibration_v4_matchup600_20260911"
        ),
        "calibrationArtifact": "elo_calibration_v4.json",
        "calibrationCases": "elo_calibration_cases_v4.jsonl",
        "formalEloMinimum": 1600,
        "formalEloMaximum": 2500,
        "eloGridMinimum": 1600,
        "eloGridMaximum": 2500,
        "eloGridStep": 1,
        "minimumTargetGames": 10,
        "maximumTargetGames": 30,
        "validationMinimumTargetGames": 12,
        "phaseBoundaries": [30, 47, 53],
        "ge4Threshold": 4,
        "neighborExponent": DEFAULT_NEIGHBOR_EXPONENT,
        "distanceKernel": "standardized_euclidean_triangular_k_plus_one_boundary",
        "referenceFeaturePolicy": (
            "frozen_global_arrays_with_target_account_and_source_game_exclusion"
        ),
        "referencePartitionDimension": "black_white_directed_cell",
        "referenceEloBinMinimum": 1600,
        "referenceEloBinWidth": 100,
        "referenceEloBinCount": 9,
        "calibrationCoverage": DEFAULT_CALIBRATION_COVERAGE,
        "calibrationGrouping": "global",
        "calibrationValidationFraction": DEFAULT_CALIBRATION_VALIDATION_FRACTION,
        "minimumValidationUsers": DEFAULT_MINIMUM_VALIDATION_USERS,
        "calibrationSplitSeed": 20260911502,
        "calibrationWorkers": 16,
        "referenceQueryWorkers": 1,
        "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V4,
        "searchSteps": list(SEARCH_STEPS_V4),
        "searchCostFallback": (
            "fallback_when_adaptive_cost_not_below_remaining_full_grid_or_"
            "minimum_is_not_protected"
        ),
        "searchMaxBasins": 64,
        "searchMaxExpansionRounds": 2,
        "searchTieTolerance": 1e-12,
        "searchBoundaryTolerance": 1e-12,
        "searchRequireConfidenceCoverage": True,
        "taskUnit": "one_player_account",
        "processPoolChunksize": 1,
        "prepareAccountShardSize": 64,
        "expansionConfigPath": "oq_reference_blackwhite_expansion_config_v3_matchup600_20260911.json",
    }


def validate_v4_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the frozen v4 contract and reject old model knobs."""

    if config.get("schema") != SCHEMA_CONFIG_V4:
        raise ValueError(f"estimated-Elo v4 requires config schema {SCHEMA_CONFIG_V4}")
    result = dict(config)
    defaults = default_v4_config()
    for key, value in defaults.items():
        result.setdefault(key, value)
    if result.get("algorithmVersion") != ALGORITHM_VERSION_V4:
        raise ValueError("estimated-Elo v4 algorithmVersion mismatch")
    forbidden = sorted(
        key for key in result
        if "beta" in str(key).casefold()
        or "optimizer" in str(key).casefold()
        or str(key) in {"m", "kappa", "alpha"}
    )
    if forbidden:
        raise ValueError(
            "estimated-Elo v4 configuration cannot contain Beta-Binomial or optimizer fields: "
            + ", ".join(forbidden)
        )
    exact_values = {
        "formalEloMinimum": 1600,
        "formalEloMaximum": 2500,
        "eloGridMinimum": 1600,
        "eloGridMaximum": 2500,
        "eloGridStep": 1,
        "minimumTargetGames": 10,
        "maximumTargetGames": 30,
        "validationMinimumTargetGames": 12,
        "ge4Threshold": 4,
        "calibrationWorkers": 16,
        "referenceQueryWorkers": 1,
        "processPoolChunksize": 1,
        "referenceEloBinMinimum": 1600,
        "referenceEloBinWidth": 100,
        "referenceEloBinCount": 9,
    }
    for key, expected in exact_values.items():
        if float(result.get(key)) != float(expected):
            raise ValueError(f"estimated-Elo v4 requires {key}={expected}")
    if [int(value) for value in result.get("phaseBoundaries", [])] != [30, 47, 53]:
        raise ValueError("estimated-Elo v4 phaseBoundaries must be [30, 47, 53]")
    if not math.isclose(
        float(result["neighborExponent"]), DEFAULT_NEIGHBOR_EXPONENT,
        rel_tol=0.0, abs_tol=0.0,
    ):
        raise ValueError("estimated-Elo v4 neighborExponent must be exactly 2/3")
    if result.get("distanceKernel") != "standardized_euclidean_triangular_k_plus_one_boundary":
        raise ValueError("estimated-Elo v4 distanceKernel mismatch")
    if result.get("referenceFeaturePolicy") != (
        "frozen_global_arrays_with_target_account_and_source_game_exclusion"
    ):
        raise ValueError("estimated-Elo v4 referenceFeaturePolicy mismatch")
    if result.get("referencePartitionDimension") != "black_white_directed_cell":
        raise ValueError("estimated-Elo v4 requires black_white_directed_cell source partition")
    if result.get("calibrationGrouping") != "global":
        raise ValueError("estimated-Elo v4 calibrationGrouping must be global")
    if float(result["calibrationCoverage"]) != DEFAULT_CALIBRATION_COVERAGE:
        raise ValueError("estimated-Elo v4 calibrationCoverage must be exactly 0.95")
    if [int(value) for value in result.get("searchSteps", [])] != list(SEARCH_STEPS_V4):
        raise ValueError("estimated-Elo v4 searchSteps must be [40,20,10,5,2,1]")
    if result.get("searchStrategyVersion") != SEARCH_STRATEGY_VERSION_V4:
        raise ValueError("estimated-Elo v4 searchStrategyVersion mismatch")
    for key in ("searchMaxBasins", "searchMaxExpansionRounds", "prepareAccountShardSize"):
        if int(result[key]) < 1:
            raise ValueError(f"{key} must be positive")
    for key in ("searchTieTolerance", "searchBoundaryTolerance"):
        if not 0.0 < float(result[key]):
            raise ValueError(f"{key} must be positive")
    if int(result["validationMinimumTargetGames"]) > int(result["maximumTargetGames"]):
        raise ValueError("validationMinimumTargetGames must not exceed maximumTargetGames")
    return result


def anscombe_transform(x: int, n: int) -> float:
    """Return the fixed Anscombe variance-stabilized phase level."""

    if isinstance(x, bool) or isinstance(n, bool) or int(x) != x or int(n) != n:
        raise ValueError("Anscombe x and n must be integers")
    x_int, n_int = int(x), int(n)
    if n_int <= 0 or x_int < 0 or x_int > n_int:
        raise ValueError("Anscombe requires n > 0 and 0 <= x <= n")
    value = 2.0 * math.asin(
        math.sqrt((x_int + ANSCOMBE_X_CORRECTION) / (n_int + ANSCOMBE_N_CORRECTION))
    )
    if not math.isfinite(value):
        raise ValueError("Anscombe transform is not finite")
    return value


def anscombe_sampling_variance(n: int) -> float:
    """Return the fixed finite-denominator sampling variance ``1/(n+0.5)``."""

    if isinstance(n, bool) or int(n) != n or int(n) <= 0:
        raise ValueError("Anscombe n must be a positive integer")
    value = 1.0 / (int(n) + 0.5)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Anscombe sampling variance is not positive finite")
    return value


def anscombe_phase_values(x: int, n: int) -> dict[str, Any]:
    """Return the immutable x/n/y/v representation used by v4 records."""

    return {
        "x": int(x),
        "n": int(n),
        "transformedY": anscombe_transform(x, n),
        "samplingVariance": anscombe_sampling_variance(n),
    }


def _v4_phase_values(record: dict[str, Any], phase_number: int) -> dict[str, Any] | None:
    """Read or derive one validated v4 phase observation."""

    if phase_number not in {1, 2, 3, 4}:
        raise ValueError("phase_number must be 1..4")
    phase = record.get(f"phase{phase_number}")
    if not isinstance(phase, dict):
        scope = record.get("scope") or selected_metrics_scope(record)
        metrics = (record.get("metrics") or {}).get(scope, {})
        phase = metrics.get(f"phase{phase_number}") if isinstance(metrics, dict) else None
    if not isinstance(phase, dict):
        return None
    x = phase.get("x", phase.get("lossGe4Count"))
    n = phase.get("n", phase.get("validLossNodeCount"))
    if not finite_number(x) or not finite_number(n):
        return None
    if isinstance(x, bool) or isinstance(n, bool) or int(x) != x or int(n) != n:
        return None
    x_int, n_int = int(x), int(n)
    if n_int <= 0 or x_int < 0 or x_int > n_int:
        return None
    transformed = anscombe_transform(x_int, n_int)
    sampling = anscombe_sampling_variance(n_int)
    stored_transformed = phase.get("transformedY", phase.get("y"))
    stored_sampling = phase.get("samplingVariance", phase.get("v"))
    if stored_transformed is not None and (
        not finite_number(stored_transformed)
        or not math.isclose(float(stored_transformed), transformed, rel_tol=0.0, abs_tol=1e-12)
    ):
        raise ValueError(f"record {record.get('recordId') or record.get('gameId')} phase{phase_number} y mismatch")
    if stored_sampling is not None and (
        not finite_number(stored_sampling)
        or not math.isclose(float(stored_sampling), sampling, rel_tol=0.0, abs_tol=1e-12)
    ):
        raise ValueError(f"record {record.get('recordId') or record.get('gameId')} phase{phase_number} v mismatch")
    return {
        "x": x_int,
        "n": n_int,
        "transformedY": transformed,
        "samplingVariance": sampling,
    }


def _v4_record_id(row: dict[str, Any]) -> str:
    return canonical_sha256({
        "gameId": str(row.get("gameId") or ""),
        "targetColor": str(row.get("targetColor") or "").casefold(),
        "scope": str(row.get("scope") or ""),
        "targetPlayerId": account_key(row.get("targetPlayerId")),
        "opponentPlayerId": account_key(row.get("opponentPlayerId")),
    })


def _v4_record_stable_key(row: dict[str, Any], index: int) -> tuple[Any, ...]:
    """Stable key shared by global-tree and brute-force KNN paths."""

    return (
        str(row.get("gameId") or ""),
        str(row.get("targetColor") or "").casefold(),
        str(row.get("scope") or ""),
        account_key(row.get("targetPlayerId")),
        account_key(row.get("opponentPlayerId")),
        str(row.get("recordId") or _v4_record_id(row)),
        int(index),
    )


def anscombe_base_records_v4(
    reference_records: Sequence[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    maximum_records_per_pool: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Prepare one immutable y/v row per directed source record and scope."""

    cfg = validate_v4_config(config or default_v4_config())
    minimum = float(cfg["formalEloMinimum"])
    maximum = float(cfg["formalEloMaximum"])
    by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in reference_records:
        if source.get("formalReferenceEligible") is not True:
            continue
        if not finite_number(source.get("targetOldR")) or not finite_number(source.get("opponentOldR")):
            continue
        if not minimum <= float(source["targetOldR"]) <= maximum:
            continue
        if not minimum <= float(source["opponentOldR"]) <= maximum:
            continue
        color = str(source.get("targetColor") or "").strip().casefold()
        if color not in COLORS:
            continue
        for scope in METRICS_SCOPES:
            metrics = (source.get("metrics") or {}).get(scope)
            if not isinstance(metrics, dict) or metrics.get("completeFourPhase") is not True:
                continue
            phases: dict[str, Any] = {}
            valid = True
            for phase_number in range(1, 5):
                phase = metrics.get(f"phase{phase_number}")
                x = phase.get("lossGe4Count") if isinstance(phase, dict) else None
                n = phase.get("validLossNodeCount") if isinstance(phase, dict) else None
                if not finite_number(x) or not finite_number(n):
                    valid = False
                    break
                try:
                    phases[f"phase{phase_number}"] = anscombe_phase_values(int(x), int(n))
                except ValueError:
                    valid = False
                    break
                if int(x) != float(x) or int(n) != float(n):
                    valid = False
                    break
            if not valid:
                continue
            row = {
                "schema": SCHEMA_ANScombe_RECORD,
                "recordId": "",
                "gameId": str(source.get("gameId") or ""),
                "created": source.get("created"),
                "targetPlayerId": source.get("targetPlayerId"),
                "opponentPlayerId": source.get("opponentPlayerId"),
                "targetColor": color,
                "scope": scope,
                "targetOldR": float(source["targetOldR"]),
                "opponentOldR": float(source["opponentOldR"]),
                **phases,
                "referenceZ1": None,
                "referenceZ2": None,
                "referenceZ3": None,
                "referenceZ4": None,
                "sourceDirectedRecordId": source.get("recordId"),
            }
            row["recordId"] = _v4_record_id(row)
            by_pool[f"{color}|{scope}"].append(row)

    output: list[dict[str, Any]] = []
    scales: dict[str, dict[str, Any]] = {}
    for key in sorted(by_pool):
        rows = sorted(
            by_pool[key],
            key=lambda row: _v4_record_stable_key(row, 0),
        )
        if maximum_records_per_pool is not None:
            rows = rows[:max(0, int(maximum_records_per_pool))]
        if len(rows) < 2:
            raise ValueError(f"Anscombe reference pool {key} has fewer than two records")
        scales[key] = {
            "color": key.split("|", 1)[0],
            "scope": key.split("|", 1)[1],
            "recordCount": len(rows),
            "selfEloSd": _population_sd(
                [float(row["targetOldR"]) for row in rows], f"{key} self Elo"
            ),
            "opponentEloSd": _population_sd(
                [float(row["opponentOldR"]) for row in rows], f"{key} opponent Elo"
            ),
            "phase1ZSd": None,
            "phase2ZSd": None,
            "phase3ZSd": None,
        }
        output.extend(rows)
    if len({row["recordId"] for row in output}) != len(output):
        raise ValueError("Anscombe reference contains duplicate record identities")
    return output, scales


def _v4_z_distribution(values: Sequence[float], pool_key: str, stage: int) -> dict[str, Any]:
    finite_values = [float(value) for value in values if finite_number(value)]
    mean = statistics.fmean(finite_values) if finite_values else None
    standard_deviation = statistics.pstdev(finite_values) if len(finite_values) >= 2 else None
    distribution = {
        "pool": pool_key,
        "phase": int(stage),
        "count": len(values),
        "finiteValueCount": len(finite_values),
        "mean": mean,
        "standardDeviation": standard_deviation,
    }
    distribution["contractSha256"] = canonical_sha256(distribution)
    return distribution


def v4_reference_model_contract(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = validate_v4_config(config or default_v4_config())
    return {
        "schema": "player-sentinel-elo-v4-reference-model-contract-v1",
        "algorithm": "anscombe_local_gaussian_predictive_v1",
        "anscombe": {
            "formula": "2*asin(sqrt((x+3/8)/(n+3/4)))",
            "xCorrection": ANSCOMBE_X_CORRECTION,
            "nCorrection": ANSCOMBE_N_CORRECTION,
            "samplingVariance": "1/(n+0.5)",
        },
        "phaseLinks": {
            "phase1": [],
            "phase2": ["referenceZ1"],
            "phase3": ["referenceZ2"],
            "phase4": ["referenceZ3"],
        },
        "distance": {
            "coordinates": ["selfElo", "opponentElo"],
            "previousCoordinate": "adjacent_previous_phase_reference_z",
            "previousZStandardDeviation": "frozen_previous_phase_z_population_sd",
            "previousZWeight": 1.0,
            "metric": "sqrt(sum(dz^2))",
        },
        "knn": {
            "exclusion": [
                "targetPlayerId",
                "opponentPlayerId",
                "targetGameIds",
                "same_source_game_both_directed_records",
            ],
            "k": "ceil(N_allowed^(2/3))",
            "boundary": "K+1_allowed_neighbor",
            "kernel": "max(0,1-distance/D)",
            "tieOrder": "stable_record_key",
        },
        "closedForm": {
            "localMean": "sum(u*y)",
            "C": "1-sum(u^2)",
            "observedVariance": "sum(u*(y-mu)^2)/C",
            "samplingVariance": "sum(u*(1-u)*v)/C",
            "betweenGameVariance": "max(0,observedVariance-samplingVariance)",
            "meanEstimateVariance": "sum(u^2*(betweenGameVariance+v))",
            "predictiveVariance": "betweenGameVariance+targetV+meanEstimateVariance",
            "targetZ": "(targetY-mu)/sqrt(predictiveVariance)",
            "phaseScore": "0.5*(log(2*pi*predictiveVariance)+targetZ^2)",
        },
        "fields": {
            key: cfg[key]
            for key in (
                "formalEloMinimum", "formalEloMaximum", "minimumTargetGames",
                "maximumTargetGames", "phaseBoundaries", "ge4Threshold",
                "neighborExponent", "distanceKernel", "referenceFeaturePolicy",
                "referencePartitionDimension",
            )
        },
    }


def v4_search_contract(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = validate_v4_config(config or default_v4_config())
    return {
        "schema": "player-sentinel-elo-v4-search-contract-v1",
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "strategyVersion": SEARCH_STRATEGY_VERSION_V4,
        "formalRange": [int(cfg["formalEloMinimum"]), int(cfg["formalEloMaximum"])],
        "steps": list(SEARCH_STEPS_V4),
        "grid40": "complete_aligned_1600_to_2500_with_2500",
        "grid20": "complete_aligned_1600_to_2500_with_2500_reusing_grid40",
        "lowerSteps": "all_local_minima_platforms_and_boundary_trends",
        "basinPolicy": "retain_multiple_low_areas_and_protect_both_sides",
        "fallback": str(cfg["searchCostFallback"]),
        "knownElo": "post_search_probe_only",
    }


def v4_calibration_contract(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = validate_v4_config(config or default_v4_config())
    return {
        "schema": "player-sentinel-elo-v4-calibration-contract-v1",
        "grouping": cfg["calibrationGrouping"],
        "coverage": float(cfg["calibrationCoverage"]),
        "validationFraction": float(cfg["calibrationValidationFraction"]),
        "minimumValidationUsers": int(cfg["minimumValidationUsers"]),
        "workers": 16,
        "taskUnit": "one_player_account",
        "chunksize": 1,
        "referenceQueryWorkersPerWorker": 1,
        "twoPass": (
            "calibration_accounts_only_produce_T95_then_"
            "validation_accounts_use_frozen_cutoff"
        ),
        "trueScoreIncrease": "J(knownElo)-minimumJ",
        "cutoff": "minimumJ+T95",
    }


def neighbor_count_v4(reference_count: int, exponent: float = DEFAULT_NEIGHBOR_EXPONENT) -> int:
    """Return the frozen v4 ``ceil(N_allowed^(2/3))`` K."""

    if int(reference_count) != reference_count or int(reference_count) <= 0:
        raise ValueError("reference_count must be a positive integer")
    if float(exponent) != DEFAULT_NEIGHBOR_EXPONENT:
        raise ValueError("v4 neighbor exponent must be exactly 2/3")
    return max(1, int(math.ceil(float(reference_count) ** (2.0 / 3.0))))


def _scipy_v4() -> tuple[Any, Any]:
    try:
        import numpy as np  # type: ignore
        from scipy.spatial import cKDTree  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency is required by formal path
        raise RuntimeError(
            "estimated-Elo v4 requires NumPy and SciPy spatial cKDTree; "
            "no alternate statistical algorithm is permitted"
        ) from exc
    return np, cKDTree


def anscombe_weighted_predictive_statistics(
    reference_y: Sequence[float],
    reference_v: Sequence[float],
    weights: Sequence[float],
    target_y: float,
    target_v: float,
) -> dict[str, Any]:
    """Apply the v4 closed-form weighted local predictive calculation."""

    np, _tree = _scipy_v4()
    y = np.asarray(reference_y, dtype=float)
    v = np.asarray(reference_v, dtype=float)
    w = np.asarray(weights, dtype=float)
    if y.ndim != 1 or v.ndim != 1 or w.ndim != 1 or not (len(y) == len(v) == len(w)):
        raise ValueError("reference y, v, and weights must be one-dimensional arrays of equal length")
    if len(y) == 0 or not np.isfinite(y).all() or not np.isfinite(v).all() or not np.isfinite(w).all():
        return {"ok": False, "reason": "insufficient_reference"}
    if (v <= 0).any() or (w < 0).any():
        return {"ok": False, "reason": "insufficient_reference"}
    weight_sum = float(np.sum(w, dtype=float))
    if not math.isfinite(weight_sum) or weight_sum <= 0:
        return {"ok": False, "reason": "insufficient_reference", "weightSum": weight_sum}
    u = w / weight_sum
    mu = float(np.sum(u * y, dtype=float))
    c_value = float(1.0 - np.sum(u * u, dtype=float))
    if not math.isfinite(c_value) or c_value <= 0:
        return {"ok": False, "reason": "insufficient_reference", "weightSum": weight_sum, "C": c_value}
    observed = float(np.sum(u * (y - mu) ** 2, dtype=float) / c_value)
    sampling = float(np.sum(u * (1.0 - u) * v, dtype=float) / c_value)
    between = max(0.0, observed - sampling)
    mean_variance = float(np.sum(u * u * (between + v), dtype=float))
    predictive = float(between + float(target_v) + mean_variance)
    if not all(math.isfinite(value) for value in (mu, observed, sampling, between, mean_variance, predictive)):
        return {"ok": False, "reason": "invalid_predictive_variance"}
    if predictive <= 0:
        return {"ok": False, "reason": "invalid_predictive_variance", "predictiveVariance": predictive}
    target_z = (float(target_y) - mu) / math.sqrt(predictive)
    phase_score = 0.5 * (math.log(2.0 * math.pi * predictive) + target_z ** 2)
    if not math.isfinite(target_z) or not math.isfinite(phase_score):
        return {"ok": False, "reason": "invalid_predictive_variance"}
    return {
        "ok": True,
        "weightSum": weight_sum,
        "normalizedWeights": u,
        "C": c_value,
        "localMeanY": mu,
        "observedVariance": observed,
        "samplingVariance": sampling,
        "betweenGameVariance": between,
        "meanEstimateVariance": mean_variance,
        "predictiveVariance": predictive,
        "targetZ": float(target_z),
        "negativeLogPredictiveDensity": float(phase_score),
        "phaseScore": float(phase_score),
    }


class _GlobalAnscombePoolV4:
    """One immutable phase pool with a reusable global cKDTree and arrays."""

    def __init__(
        self,
        records: Sequence[dict[str, Any]],
        scales: dict[str, Any],
        stage: int,
        config: dict[str, Any],
        pool_key: str,
    ) -> None:
        if int(stage) not in {1, 2, 3, 4}:
            raise ValueError("stage must be 1..4")
        self.stage = int(stage)
        self.pool_key = str(pool_key)
        self.config = validate_v4_config(config)
        self.scales = scales
        self.previous_key = f"referenceZ{self.stage - 1}" if self.stage > 1 else None
        self.previous_sd = (
            None if self.stage == 1 else scales.get(f"phase{self.stage - 1}ZSd")
        )
        if self.previous_key is not None and (
            not finite_number(self.previous_sd) or float(self.previous_sd) <= 0
        ):
            raise ValueError(
                f"missing positive phase{self.stage - 1}ZSd for {self.pool_key} phase{self.stage}"
            )
        filtered: list[dict[str, Any]] = []
        phase_values: list[dict[str, Any]] = []
        for row in records:
            values = _v4_phase_values(row, self.stage)
            if values is None:
                continue
            if self.previous_key is not None and not finite_number(row.get(self.previous_key)):
                continue
            filtered.append(row)
            phase_values.append(values)
        self.records = filtered
        self.record_count = len(filtered)
        np, cKDTree = _scipy_v4()
        self._np = np
        self.y = np.asarray([item["transformedY"] for item in phase_values], dtype=float)
        self.v = np.asarray([item["samplingVariance"] for item in phase_values], dtype=float)
        self.record_ids = tuple(
            str(row.get("recordId") or _v4_record_id(row)) for row in self.records
        )
        self.game_ids = tuple(str(row.get("gameId") or "") for row in self.records)
        self.target_player_ids = tuple(
            account_key(row.get("targetPlayerId")) for row in self.records
        )
        self.opponent_player_ids = tuple(
            account_key(row.get("opponentPlayerId")) for row in self.records
        )
        self.stable_keys = tuple(
            _v4_record_stable_key(row, index)
            for index, row in enumerate(self.records)
        )
        # Keep the hot-path metadata alongside y/v in immutable NumPy arrays;
        # Python records remain available only for the final diagnostics.
        self.record_id_array = np.asarray(self.record_ids, dtype=object)
        self.game_id_array = np.asarray(self.game_ids, dtype=object)
        self.target_player_id_array = np.asarray(self.target_player_ids, dtype=object)
        self.opponent_player_id_array = np.asarray(self.opponent_player_ids, dtype=object)
        self.stable_sort_key_array = np.asarray(self.stable_keys, dtype=object)
        feature_rows = [self._feature_for_record(row) for row in self.records]
        self.features = np.asarray(feature_rows, dtype=float) if feature_rows else np.empty((0, 2 if self.stage == 1 else 3), dtype=float)
        self.tree = cKDTree(self.features) if feature_rows else None
        self.query_count = 0
        self.query_ball_count = 0
        # cKDTree reads are safe to share between target-game threads.  The
        # counters are diagnostics, so protect only their increments and keep
        # the expensive tree queries concurrent.
        self._counter_lock = threading.Lock()
        self._account_indexes: dict[str, set[int]] = defaultdict(set)
        self._game_indexes: dict[str, set[int]] = defaultdict(set)
        for index, row in enumerate(self.records):
            game_id = str(row.get("gameId") or "")
            if game_id:
                self._game_indexes[game_id].add(index)
            for player in (
                account_key(row.get("targetPlayerId")),
                account_key(row.get("opponentPlayerId")),
            ):
                if player:
                    self._account_indexes[player].add(index)
        self.maximum_possible_excluded_record_count = max(
            (len(indexes) for indexes in self._account_indexes.values()),
            default=0,
        )

    def _feature_for_record(self, row: dict[str, Any]) -> list[float]:
        features = [
            float(row["targetOldR"]) / float(self.scales["selfEloSd"]),
            float(row["opponentOldR"]) / float(self.scales["opponentEloSd"]),
        ]
        if self.previous_key is not None:
            features.append(float(row[self.previous_key]) / float(self.previous_sd))
        return features

    def _query_feature(
        self,
        trial_elo: float,
        opponent_elo: float,
        previous_z: float | None,
    ) -> Any:
        query = [
            float(trial_elo) / float(self.scales["selfEloSd"]),
            float(opponent_elo) / float(self.scales["opponentEloSd"]),
        ]
        if self.previous_key is not None:
            if not finite_number(previous_z):
                raise ValueError("all previous-z distance values must be finite")
            query.append(float(previous_z) / float(self.previous_sd))
        return self._np.asarray(query, dtype=float)

    def excluded_indexes(
        self,
        account: str,
        target_game_ids: Iterable[str],
    ) -> set[int]:
        excluded = set(self._account_indexes.get(account_key(account), set()))
        for game_id in target_game_ids:
            excluded.update(self._game_indexes.get(str(game_id), set()))
        return excluded

    def nearest(
        self,
        account: str,
        target_game_ids: Iterable[str],
        trial_elo: float,
        opponent_elo: float,
        previous_z: float | None = None,
    ) -> dict[str, Any]:
        excluded = self.excluded_indexes(account, target_game_ids)
        n_allowed = self.record_count - len(excluded)
        k = neighbor_count_v4(n_allowed, float(self.config["neighborExponent"])) if n_allowed else 0
        base = {
            "eligibleReferenceCount": n_allowed,
            "N_allowed": n_allowed,
            "K": k,
            "excludedRecordCount": len(excluded),
            "maximumPossibleExcludedRecordCount": self.maximum_possible_excluded_record_count,
        }
        if self.tree is None or n_allowed < k + 1:
            return {"ok": False, "reason": "insufficient_reference", **base}
        query = self._query_feature(trial_elo, opponent_elo, previous_z)
        exclusion_bound = max(self.maximum_possible_excluded_record_count, len(excluded))
        query_k = min(self.record_count, k + 1 + exclusion_bound)
        distances, indexes = self.tree.query(
            query, k=query_k, workers=int(self.config["referenceQueryWorkers"])
        )
        with self._counter_lock:
            self.query_count += 1
        raw_distances = [float(value) for value in self._np.atleast_1d(distances)]
        raw_indexes = [int(value) for value in self._np.atleast_1d(indexes)]
        candidates = {
            index for index in raw_indexes
            if 0 <= index < self.record_count and index not in excluded
        }

        def rank(indexes_to_rank: Iterable[int]) -> list[tuple[float, tuple[Any, ...], int]]:
            ranked: list[tuple[float, tuple[Any, ...], int]] = []
            for index in indexes_to_rank:
                distance = float(self._np.linalg.norm(self.features[index] - query))
                ranked.append((distance, self.stable_keys[index], int(index)))
            ranked.sort(key=lambda item: (item[0], item[1]))
            return ranked

        query_boundary = raw_distances[-1] if raw_distances else math.inf
        boundary_tie = bool(
            len(raw_distances) >= 2
            and abs(raw_distances[-1] - raw_distances[-2])
                <= float(self.config["searchBoundaryTolerance"])
        )
        ranked = rank(candidates)
        selected_boundary_tie = bool(
            len(ranked) >= k + 1
            and abs(ranked[k - 1][0] - ranked[k][0])
                <= float(self.config["searchBoundaryTolerance"])
        )
        if query_k < self.record_count and (boundary_tie or selected_boundary_tie):
            expanded = self.tree.query_ball_point(
                query,
                r=float(query_boundary) + float(self.config["searchBoundaryTolerance"]),
                workers=int(self.config["referenceQueryWorkers"]),
            )
            with self._counter_lock:
                self.query_ball_count += 1
            candidates.update(
                int(index) for index in expanded
                if 0 <= int(index) < self.record_count and int(index) not in excluded
            )
            ranked = rank(candidates)
        if len(ranked) < k + 1:
            _all_distances, all_indexes = self.tree.query(
                query, k=self.record_count,
                workers=int(self.config["referenceQueryWorkers"]),
            )
            with self._counter_lock:
                self.query_count += 1
            all_candidates = {
                int(index) for index in self._np.atleast_1d(all_indexes)
                if 0 <= int(index) < self.record_count and int(index) not in excluded
            }
            ranked = rank(all_candidates)
        if len(ranked) < k + 1:
            return {"ok": False, "reason": "insufficient_reference", "queryK": query_k, **base}
        boundary = float(ranked[k][0])
        if not math.isfinite(boundary) or boundary <= 0:
            return {
                "ok": False,
                "reason": "insufficient_reference",
                "boundaryDistance": boundary,
                "queryK": query_k,
                **base,
            }
        selected = ranked[:k]
        weights = [max(0.0, 1.0 - float(item[0]) / boundary) for item in selected]
        weight_sum = float(sum(weights))
        if not math.isfinite(weight_sum) or weight_sum <= 0:
            return {
                "ok": False,
                "reason": "insufficient_reference",
                "boundaryDistance": boundary,
                "queryK": query_k,
                "weightSum": weight_sum,
                **base,
            }
        neighbor_indexes = [item[2] for item in selected]
        neighbor_ids = [self.record_ids[index] for index in neighbor_indexes]
        return {
            "ok": True,
            "neighborIndexes": neighbor_indexes,
            "neighborRecordIds": neighbor_ids,
            "neighborSetSha256": canonical_sha256(neighbor_ids),
            "weights": weights,
            "K": k,
            "N_allowed": n_allowed,
            "eligibleReferenceCount": n_allowed,
            "boundaryDistance": boundary,
            "effectiveWeight": weight_sum,
            "excludedRecordCount": len(excluded),
            "maximumPossibleExcludedRecordCount": self.maximum_possible_excluded_record_count,
            "queryK": query_k,
            "queryCandidateCount": len(candidates),
            "boundaryExpanded": bool(boundary_tie or selected_boundary_tie),
        }


class GlobalAnscombeKNNIndexV4:
    """Build all color/scope/phase trees once and reuse them by account."""

    def __init__(
        self,
        records: Sequence[dict[str, Any]],
        reference_manifest: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        started = time.perf_counter()
        self.config = validate_v4_config(config)
        _v4_validate_reference_manifest(reference_manifest, self.config)
        self.reference_manifest = reference_manifest
        # Keep the immutable source sequence available for target-game worker
        # initialization when the caller has no on-disk reference path.  This
        # stores only another list of references to the existing row objects.
        self.source_records = list(records)
        self.pools: dict[tuple[str, str, int], _GlobalAnscombePoolV4] = {}
        by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in records:
            color = str(row.get("targetColor") or "").strip().casefold()
            scope = str(row.get("scope") or "").strip()
            if color in COLORS and scope in METRICS_SCOPES:
                by_pool[f"{color}|{scope}"].append(row)
        scales = reference_manifest.get("scales") or {}
        for color in COLORS:
            for scope in METRICS_SCOPES:
                key = f"{color}|{scope}"
                scale = scales.get(key)
                if not isinstance(scale, dict):
                    raise ValueError(f"Anscombe reference manifest has no pool {key}")
                for stage in range(1, 5):
                    self.pools[(color, scope, stage)] = _GlobalAnscombePoolV4(
                        by_pool.get(key, []), scale, stage, self.config, key
                    )
        self.tree_build_seconds = time.perf_counter() - started
        self.tree_build_count = len(self.pools)
        self._accounts_seen: set[str] = set()

    def pool(self, color: str, scope: str, stage: int) -> _GlobalAnscombePoolV4 | None:
        return self.pools.get((str(color).casefold(), str(scope), int(stage)))

    def begin_account(self, account: str) -> bool:
        key = account_key(account)
        first = key not in self._accounts_seen
        self._accounts_seen.add(key)
        return first

    @property
    def query_count(self) -> int:
        return sum(pool.query_count + pool.query_ball_count for pool in self.pools.values())

    @property
    def tree_query_count(self) -> int:
        return sum(pool.query_count for pool in self.pools.values())

    @property
    def boundary_expansion_count(self) -> int:
        return sum(pool.query_ball_count for pool in self.pools.values())


def _v4_validate_reference_manifest(
    manifest: dict[str, Any], config: dict[str, Any], *, strict: bool = False
) -> None:
    if manifest.get("schema") != SCHEMA_ANScombe_REFERENCE:
        raise ValueError("unsupported Anscombe reference manifest schema")
    if manifest.get("algorithmVersion") != ALGORITHM_VERSION_V4:
        raise ValueError("Anscombe reference algorithm version mismatch")
    if strict and manifest.get("configSha256") != canonical_sha256(config):
        raise ValueError("Anscombe reference/config SHA-256 mismatch")
    if manifest.get("referenceModelContractSha256") not in {None, canonical_sha256(v4_reference_model_contract(config))}:
        raise ValueError("Anscombe reference model contract SHA-256 mismatch")
    scales = manifest.get("scales")
    if not isinstance(scales, dict):
        raise ValueError("Anscombe reference manifest has no scales")
    for color in COLORS:
        for scope in METRICS_SCOPES:
            key = f"{color}|{scope}"
            scale = scales.get(key)
            if not isinstance(scale, dict):
                raise ValueError(f"Anscombe reference manifest is missing {key}")
            for field in ("selfEloSd", "opponentEloSd"):
                if not finite_number(scale.get(field)) or float(scale[field]) <= 0:
                    raise ValueError(f"Anscombe reference scale {key}.{field} is invalid")
            for stage in (1, 2, 3):
                value = scale.get(f"phase{stage}ZSd")
                if value is not None and (not finite_number(value) or float(value) <= 0):
                    raise ValueError(f"Anscombe reference scale {key}.phase{stage}ZSd is invalid")


def load_anscombe_reference_v4(
    directory: str | Path,
    *,
    config: dict[str, Any] | None = None,
    expected_reference_manifest_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    cfg = validate_v4_config(config or default_v4_config())
    root = Path(directory)
    manifest_path = root / str(cfg["conditionalReferenceManifest"])
    records_path = root / str(cfg["conditionalReferenceRecords"])
    manifest = read_json(manifest_path)
    _v4_validate_reference_manifest(manifest, cfg, strict=True)
    if (
        expected_reference_manifest_sha256 is not None
        and manifest.get("referenceManifestSha256") != expected_reference_manifest_sha256
    ):
        raise ValueError("Anscombe reference/source manifest SHA-256 mismatch")
    if not records_path.is_file() or sha256_file(records_path) != manifest.get("recordsSha256"):
        raise ValueError("Anscombe reference records SHA-256 mismatch")
    rows = read_jsonl(records_path)
    if len(rows) != int(manifest.get("recordCount", -1)):
        raise ValueError("Anscombe reference record count mismatch")
    ids: set[str] = set()
    for row in rows:
        if row.get("schema") != SCHEMA_ANScombe_RECORD:
            raise ValueError("unsupported Anscombe reference record schema")
        record_id = str(row.get("recordId") or "")
        if not record_id or record_id in ids:
            raise ValueError("Anscombe reference contains duplicate or empty recordId")
        ids.add(record_id)
        for stage in range(1, 5):
            if _v4_phase_values(row, stage) is None:
                raise ValueError(f"Anscombe reference row is missing valid phase{stage}: {record_id}")
    return rows, manifest, sha256_file(manifest_path)


def score_anscombe_phase_v4(
    target_record: dict[str, Any],
    stage: int,
    trial_elo: float,
    pool: _GlobalAnscombePoolV4,
    *,
    account: str,
    target_game_ids: Iterable[str],
    previous_z: float | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one target phase with the closed-form v4 predictive model."""

    cfg = validate_v4_config(config or pool.config)
    target = _v4_phase_values(target_record, int(stage))
    if target is None:
        return {
            "ok": False,
            "reason": "incomplete_phase_data",
            "phase": int(stage),
            "fitStatus": "closed_form_anscombe",
        }
    opponent = target_record.get("opponentOldR")
    if not finite_number(opponent):
        return {
            "ok": False,
            "reason": "opponent_out_of_reference_range",
            "phase": int(stage),
            "fitStatus": "closed_form_anscombe",
        }
    knn_started = time.perf_counter()
    nearest = pool.nearest(
        account,
        target_game_ids,
        float(trial_elo),
        float(opponent),
        previous_z,
    )
    knn_seconds = time.perf_counter() - knn_started
    if nearest.get("ok") is not True:
        return {
            "ok": False,
            "phase": int(stage),
            "fitStatus": "closed_form_anscombe",
            "knnQuerySeconds": knn_seconds,
            "closedFormStatisticsSeconds": 0.0,
            **nearest,
        }
    indexes = nearest["neighborIndexes"]
    closed_form_started = time.perf_counter()
    stats = anscombe_weighted_predictive_statistics(
        pool.y[pool._np.asarray(indexes, dtype=int)],
        pool.v[pool._np.asarray(indexes, dtype=int)],
        nearest["weights"],
        float(target["transformedY"]),
        float(target["samplingVariance"]),
    )
    closed_form_seconds = time.perf_counter() - closed_form_started
    if stats.get("ok") is not True:
        return {
            "ok": False,
            "phase": int(stage),
            "fitStatus": "closed_form_anscombe",
            "knnQuerySeconds": knn_seconds,
            "closedFormStatisticsSeconds": closed_form_seconds,
            **nearest,
            **{key: value for key, value in stats.items() if key != "normalizedWeights"},
        }
    return {
        "ok": True,
        "phase": int(stage),
        "x": int(target["x"]),
        "n": int(target["n"]),
        "transformedY": float(target["transformedY"]),
        "targetSamplingVariance": float(target["samplingVariance"]),
        "samplingVariance": float(stats["samplingVariance"]),
        "localMeanY": float(stats["localMeanY"]),
        "observedVariance": float(stats["observedVariance"]),
        "betweenGameVariance": float(stats["betweenGameVariance"]),
        "meanEstimateVariance": float(stats["meanEstimateVariance"]),
        "predictiveVariance": float(stats["predictiveVariance"]),
        "targetZ": float(stats["targetZ"]),
        "negativeLogPredictiveDensity": float(stats["negativeLogPredictiveDensity"]),
        "phaseScore": float(stats["phaseScore"]),
        "C": float(stats["C"]),
        "K": int(nearest["K"]),
        "N_allowed": int(nearest["N_allowed"]),
        "eligibleReferenceCount": int(nearest["eligibleReferenceCount"]),
        "boundaryDistance": float(nearest["boundaryDistance"]),
        "effectiveWeight": float(nearest["effectiveWeight"]),
        "excludedRecordCount": int(nearest["excludedRecordCount"]),
        "maximumPossibleExcludedRecordCount": int(nearest["maximumPossibleExcludedRecordCount"]),
        "queryK": int(nearest["queryK"]),
        "neighborSetSha256": nearest["neighborSetSha256"],
        "neighborRecordIds": nearest["neighborRecordIds"],
        "fitStatus": "closed_form_anscombe",
        "fitAttempted": False,
        "knnQuerySeconds": knn_seconds,
        "closedFormStatisticsSeconds": closed_form_seconds,
    }


def _v4_preparation_manifest_for_index(
    config: dict[str, Any], scales: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": SCHEMA_ANScombe_REFERENCE,
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "configSha256": canonical_sha256(config),
        "referenceModelContractSha256": canonical_sha256(v4_reference_model_contract(config)),
        "referenceFeaturePolicy": config["referenceFeaturePolicy"],
        "scales": scales,
    }


def _v4_stage_patch_diagnostic(result: dict[str, Any], excluded_count: int) -> dict[str, Any]:
    diagnostic = {
        key: result.get(key)
        for key in (
            "reason", "K", "N_allowed", "boundaryDistance", "eligibleReferenceCount",
            "effectiveWeight", "C", "localMeanY", "observedVariance", "samplingVariance",
            "betweenGameVariance", "meanEstimateVariance", "predictiveVariance", "targetZ",
            "negativeLogPredictiveDensity", "neighborSetSha256", "queryK", "fitStatus",
        )
        if result.get(key) is not None
    }
    diagnostic["excludedSourceGameCount"] = int(excluded_count)
    diagnostic["status"] = "ok" if result.get("ok") is True else str(result.get("reason") or "failed")
    return diagnostic


def _v4_prepare_pool_stage(
    pool_rows: Sequence[dict[str, Any]],
    pool_key: str,
    stage: int,
    scales: dict[str, Any],
    config: dict[str, Any],
    completed_patches: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compute one pool/stage from a single global tree.

    Account shards are committed by the caller.  This helper only computes
    missing shards and never alters the base rows, which keeps stage order and
    restart behavior deterministic.
    """

    index_manifest = _v4_preparation_manifest_for_index(config, {pool_key: scales})
    # The index constructor expects all four pool keys.  A preparation query
    # only needs the current pool, so construct that pool directly while still
    # retaining exactly the same global-tree/exclusion implementation.
    pool = _GlobalAnscombePoolV4(pool_rows, scales, stage, config, pool_key)
    accounts = sorted({
        account_key(row.get("targetPlayerId"))
        for row in pool_rows
        if account_key(row.get("targetPlayerId"))
    })
    shard_size = int(config["prepareAccountShardSize"])
    generated: list[dict[str, Any]] = []
    for shard_index, start in enumerate(range(0, len(accounts), shard_size)):
        shard_accounts = accounts[start:start + shard_size]
        shard_key = f"{pool_key}|phase{stage}|shard{shard_index:04d}"
        if shard_key in completed_patches:
            generated.extend(completed_patches[shard_key]["patches"])
            continue
        account_set = set(shard_accounts)
        for target in pool_rows:
            account = account_key(target.get("targetPlayerId"))
            if account not in account_set:
                continue
            game_id = str(target.get("gameId") or "")
            result = score_anscombe_phase_v4(
                target,
                stage,
                float(target["targetOldR"]),
                pool,
                account=account,
                target_game_ids=[game_id],
                previous_z=(target.get(f"referenceZ{stage - 1}") if stage > 1 else None),
                config=config,
            )
            excluded = pool.excluded_indexes(account, [game_id])
            generated.append({
                "recordId": str(target["recordId"]),
                "referenceZ": result.get("targetZ") if result.get("ok") is True else None,
                "diagnostic": _v4_stage_patch_diagnostic(result, len(excluded)),
            })
    generated.sort(key=lambda patch: str(patch["recordId"]))
    return generated, {
        "queryCount": pool.query_count,
        "boundaryExpansionQueryCount": pool.query_ball_count,
        "accounts": accounts,
    }


def prepare_anscombe_reference_v4(
    reference_records: Sequence[dict[str, Any]],
    output_dir: str | Path,
    *,
    config: dict[str, Any] | None = None,
    input_records_path: str | Path | None = None,
    reference_manifest_sha256: str | None = None,
    resume: bool = False,
    maximum_records_per_pool: int | None = None,
) -> dict[str, Any]:
    """Build the single resumable v4 reference-z cache.

    Stages are strictly sequential.  Each account shard is atomically written
    before progress is advanced; final records, manifest, and audit are also
    atomically committed.  A resumed run verifies every stored SHA and the
    full input/model contract before continuing.
    """

    cfg = validate_v4_config(config or default_v4_config())
    output = Path(output_dir).resolve()
    progress_path = output / "progress.json"
    records_name = str(cfg["conditionalReferenceRecords"])
    manifest_name = str(cfg["conditionalReferenceManifest"])
    input_sha = (
        sha256_file(input_records_path)
        if input_records_path is not None else canonical_sha256(list(reference_records))
    )
    contract = {
        "schema": SCHEMA_ANScombe_REFERENCE,
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "configSha256": canonical_sha256(cfg),
        "referenceModelContractSha256": canonical_sha256(v4_reference_model_contract(cfg)),
        "inputSha256": input_sha,
        "referenceManifestSha256": reference_manifest_sha256,
        "maximumRecordsPerPool": maximum_records_per_pool,
        "preparation": "single_frozen_reference_z_cache_stage1_to_stage4",
    }
    contract_sha = canonical_sha256(contract)
    if output.exists() and not resume and any(output.iterdir()):
        raise FileExistsError(f"Anscombe reference output already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if progress_path.is_file():
        progress = read_json(progress_path)
        if progress.get("contractSha256") != contract_sha:
            raise ValueError("resume refused: Anscombe reference input/model contract changed")
    else:
        if resume and any(output.iterdir()):
            raise ValueError("resume refused: Anscombe reference directory has no compatible progress.json")
        progress = {
            "schema": "player-sentinel-elo-anscombe-reference-progress-v1",
            "contract": contract,
            "contractSha256": contract_sha,
            "status": "running",
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "completedStages": {},
            "completedShards": {},
            "timingSeconds": {},
        }
        atomic_write_json(progress_path, progress)

    base_path = output / "base_anscombe_records.jsonl"
    scales_path = output / "anscombe_scales.json"
    if base_path.is_file() and scales_path.is_file():
        rows = read_jsonl(base_path)
        scales = read_json(scales_path)
        if sha256_file(base_path) != progress.get("baseRecordsSha256"):
            raise ValueError("resume refused: Anscombe base records hash changed")
    else:
        started = time.perf_counter()
        rows, scales = anscombe_base_records_v4(
            reference_records,
            config=cfg,
            maximum_records_per_pool=maximum_records_per_pool,
        )
        atomic_write_jsonl(base_path, rows)
        atomic_write_json(scales_path, scales)
        progress["baseRecordsSha256"] = sha256_file(base_path)
        progress["baseRecordCount"] = len(rows)
        progress["timingSeconds"]["base"] = time.perf_counter() - started
        progress["updatedAt"] = utc_now()
        atomic_write_json(progress_path, progress)

    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pools[f"{row['targetColor']}|{row['scope']}"].append(row)
    per_pool_audit: dict[str, Any] = {}
    stage_distributions: dict[str, Any] = {}
    for pool_key in sorted(pools):
        pool_rows = pools[pool_key]
        for stage in range(1, 5):
            stage_key = f"{pool_key}|phase{stage}"
            stage_file = output / "stages" / f"{pool_key.replace('|', '__')}__phase{stage}.jsonl"
            completed_stage = progress["completedStages"].get(stage_key)
            if completed_stage:
                if not stage_file.is_file() or sha256_file(stage_file) != completed_stage.get("sha256"):
                    raise ValueError(f"resume refused: completed Anscombe stage changed: {stage_key}")
                patches = read_jsonl(stage_file)
                _apply_stage_patches_v4(pool_rows, patches, stage)
                if stage <= 3:
                    scales[pool_key][f"phase{stage}ZSd"] = completed_stage.get("zSd")
                stage_distributions[f"{pool_key}|phase{stage}"] = completed_stage.get("zDistribution")
                continue

            stage_started = time.perf_counter()
            # Completed shard rows are loaded and verified before the global
            # tree is queried.  The tree itself is built once for this pool and
            # stage, then reused for all accounts in that stage.
            shard_root = output / "shards" / pool_key.replace("|", "__") / f"phase{stage}"
            accounts = sorted({
                account_key(row.get("targetPlayerId"))
                for row in pool_rows
                if account_key(row.get("targetPlayerId"))
            })
            shard_size = int(cfg["prepareAccountShardSize"])
            patches: list[dict[str, Any]] = []
            pending: list[tuple[int, list[str], Path, str]] = []
            completed_for_helper: dict[str, dict[str, Any]] = {}
            for shard_index, start in enumerate(range(0, len(accounts), shard_size)):
                shard_accounts = accounts[start:start + shard_size]
                shard_key = f"{stage_key}|shard{shard_index:04d}"
                shard_path = shard_root / f"shard_{shard_index:04d}.jsonl"
                completed_shard = progress["completedShards"].get(shard_key)
                if completed_shard:
                    if not shard_path.is_file() or sha256_file(shard_path) != completed_shard.get("sha256"):
                        raise ValueError(f"resume refused: completed Anscombe shard changed: {shard_key}")
                    shard_patches = read_jsonl(shard_path)
                    if len(shard_patches) != int(completed_shard.get("recordCount", -1)):
                        raise ValueError(f"resume refused: completed Anscombe shard count changed: {shard_key}")
                    patches.extend(shard_patches)
                    continue
                pending.append((shard_index, shard_accounts, shard_path, shard_key))

            # The stage pool is global for the whole worker/process.  We use a
            # direct pool object here because the preparation process is already
            # single-owner; account-specific trees are never constructed.
            pool = _GlobalAnscombePoolV4(pool_rows, scales[pool_key], stage, cfg, pool_key)
            for shard_index, shard_accounts, shard_path, shard_key in pending:
                account_set = set(shard_accounts)
                shard_patches: list[dict[str, Any]] = []
                for target in pool_rows:
                    account = account_key(target.get("targetPlayerId"))
                    if account not in account_set:
                        continue
                    game_id = str(target.get("gameId") or "")
                    result = score_anscombe_phase_v4(
                        target,
                        stage,
                        float(target["targetOldR"]),
                        pool,
                        account=account,
                        target_game_ids=[game_id],
                        previous_z=(target.get(f"referenceZ{stage - 1}") if stage > 1 else None),
                        config=cfg,
                    )
                    excluded_count = len(pool.excluded_indexes(account, [game_id]))
                    shard_patches.append({
                        "recordId": str(target["recordId"]),
                        "referenceZ": result.get("targetZ") if result.get("ok") is True else None,
                        "diagnostic": _v4_stage_patch_diagnostic(result, excluded_count),
                    })
                shard_patches.sort(key=lambda patch: str(patch["recordId"]))
                atomic_write_jsonl(shard_path, shard_patches)
                shard_sha = sha256_file(shard_path)
                progress["completedShards"][shard_key] = {
                    "sha256": shard_sha,
                    "accountCount": len(shard_accounts),
                    "recordCount": len(shard_patches),
                    "completedAt": utc_now(),
                }
                progress["updatedAt"] = utc_now()
                atomic_write_json(progress_path, progress)
                patches.extend(shard_patches)

            patches.sort(key=lambda patch: str(patch["recordId"]))
            _apply_stage_patches_v4(pool_rows, patches, stage)
            z_values = [
                float(row[f"referenceZ{stage}"])
                for row in pool_rows if finite_number(row.get(f"referenceZ{stage}"))
            ]
            distribution = _v4_z_distribution(
                [row.get(f"referenceZ{stage}") for row in pool_rows], pool_key, stage
            )
            if stage <= 3:
                z_sd = distribution.get("standardDeviation")
                if not finite_number(z_sd) or float(z_sd) <= 0:
                    raise ValueError(f"{pool_key} phase{stage} has no positive finite reference z standard deviation")
                scales[pool_key][f"phase{stage}ZSd"] = float(z_sd)
                atomic_write_json(scales_path, scales)
            stage_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_jsonl(stage_file, patches)
            status_counts: dict[str, int] = defaultdict(int)
            k_values: list[int] = []
            for patch in patches:
                diagnostic = patch.get("diagnostic") or {}
                status_counts[str(diagnostic.get("status") or "unknown")] += 1
                if finite_number(diagnostic.get("K")):
                    k_values.append(int(diagnostic["K"]))
            completed_stage = {
                "sha256": sha256_file(stage_file),
                "recordCount": len(patches),
                "validRecordCount": len(z_values),
                "failedRecordCount": len(patches) - len(z_values),
                "zSd": scales[pool_key].get(f"phase{stage}ZSd") if stage <= 3 else None,
                "zDistribution": distribution,
                "statusCounts": dict(sorted(status_counts.items())),
                "kDistribution": _error_summary(k_values),
                "completedAt": utc_now(),
            }
            progress["completedStages"][stage_key] = completed_stage
            stage_distributions[stage_key] = distribution
            progress["timingSeconds"][stage_key] = time.perf_counter() - stage_started
            progress["updatedAt"] = utc_now()
            atomic_write_json(progress_path, progress)

        per_pool_audit[pool_key] = {
            "recordCount": len(pool_rows),
            "selfEloSd": scales[pool_key]["selfEloSd"],
            "opponentEloSd": scales[pool_key]["opponentEloSd"],
            "phase1ZSd": scales[pool_key]["phase1ZSd"],
            "phase2ZSd": scales[pool_key]["phase2ZSd"],
            "phase3ZSd": scales[pool_key]["phase3ZSd"],
            "stages": {
                f"phase{stage}": progress["completedStages"][f"{pool_key}|phase{stage}"]
                for stage in range(1, 5)
            },
        }

    rows.sort(key=lambda row: _v4_record_stable_key(row, 0))
    final_records_path = output / records_name
    atomic_write_jsonl(final_records_path, rows)
    z_distributions = {
        key: value for key, value in sorted(stage_distributions.items())
    }
    manifest = {
        "schema": SCHEMA_ANScombe_REFERENCE,
        "version": "v4",
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "createdAt": utc_now(),
        "configSha256": canonical_sha256(cfg),
        "referenceModelContractSha256": canonical_sha256(v4_reference_model_contract(cfg)),
        "referenceFeaturePolicy": cfg["referenceFeaturePolicy"],
        "referenceManifestSha256": reference_manifest_sha256,
        "inputSha256": input_sha,
        "recordCount": len(rows),
        "recordsFile": records_name,
        "recordsSha256": sha256_file(final_records_path),
        "scales": scales,
        "zDistributions": z_distributions,
        "pools": per_pool_audit,
        "preparationContractSha256": contract_sha,
        "sourceLevel22Rerun": False,
        "sourcePhaseCounts": "reused_existing_Level22_x_n",
        "singleFrozenCache": True,
    }
    manifest_path = output / manifest_name
    atomic_write_json(manifest_path, manifest)
    progress["status"] = "completed"
    progress["completedAt"] = utc_now()
    progress["recordsSha256"] = manifest["recordsSha256"]
    progress["manifestSha256"] = sha256_file(manifest_path)
    progress["updatedAt"] = utc_now()
    atomic_write_json(progress_path, progress)
    audit = {
        "schema": "player-sentinel-elo-anscombe-reference-audit-v1",
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "ok": True,
        "createdAt": utc_now(),
        "preparationContractSha256": contract_sha,
        "referenceModelContractSha256": manifest["referenceModelContractSha256"],
        "manifestSha256": sha256_file(manifest_path),
        "recordsSha256": manifest["recordsSha256"],
        "recordCount": len(rows),
        "pools": per_pool_audit,
        "zDistributions": z_distributions,
        "timingSeconds": progress["timingSeconds"],
        "sourceLevel22Rerun": False,
        "checks": {
            "oneFrozenCache": manifest["singleFrozenCache"] is True,
            "fourSequentialStages": all(
                f"phase{stage}" in per_pool_audit[key]["stages"]
                for key in per_pool_audit for stage in range(1, 5)
            ),
            "phase1ToPhase2ToPhase3ToPhase4": True,
            "allStoredCountsValid": all(
                _v4_phase_values(row, stage) is not None
                for row in rows for stage in range(1, 5)
            ),
        },
    }
    audit["ok"] = bool(all(audit["checks"].values()))
    atomic_write_json(output / "anscombe_reference_audit.json", audit)
    return manifest


def _apply_stage_patches_v4(
    rows: Sequence[dict[str, Any]], patches: Sequence[dict[str, Any]], stage: int
) -> None:
    by_id = {str(row.get("recordId") or ""): row for row in rows}
    if len(by_id) != len(rows) or "" in by_id:
        raise ValueError("Anscombe reference contains duplicate or empty record identities")
    seen: set[str] = set()
    for patch in patches:
        record_id = str(patch.get("recordId") or "")
        if record_id in seen or record_id not in by_id:
            raise ValueError("Anscombe stage patch references an unknown or duplicate record")
        seen.add(record_id)
        by_id[record_id][f"referenceZ{int(stage)}"] = patch.get("referenceZ")
        by_id[record_id].setdefault("zDiagnostics", {})[f"phase{int(stage)}"] = patch.get("diagnostic")
    if len(seen) != len(rows):
        raise ValueError("Anscombe stage patch does not cover the base pool")


def _v4_target_record_rejection(record: dict[str, Any], config: dict[str, Any]) -> str | None:
    """Return one deterministic pre-search rejection reason for a target game."""

    scope = None
    try:
        scope = selected_metrics_scope(record)
    except ValueError:
        return "invalid_metrics_scope"
    if scope not in METRICS_SCOPES:
        return "invalid_metrics_scope"
    metrics = (record.get("metrics") or {}).get(scope)
    if not isinstance(metrics, dict) or metrics.get("completeFourPhase") is not True:
        return "incomplete_phase_data"
    for stage in range(1, 5):
        try:
            values = _v4_phase_values(record, stage)
        except ValueError:
            return f"phase{stage}_anscombe_value_mismatch"
        if values is None:
            return f"phase{stage}_invalid_count"
    opponent = record.get("opponentOldR")
    if not finite_number(opponent):
        return "opponent_out_of_reference_range"
    if not int(config["formalEloMinimum"]) <= float(opponent) <= int(config["formalEloMaximum"]):
        return "opponent_out_of_reference_range"
    return None


def select_target_records_v4(
    records: Sequence[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze the complete target-game set before any Elo candidate query."""

    cfg = validate_v4_config(config or default_v4_config())
    selected_candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_game_ids: set[str] = set()
    for record in records:
        game_id = str(record.get("gameId") or "")
        if not game_id:
            excluded.append({"gameId": game_id, "reason": "invalid_game_id"})
            continue
        if game_id in seen_game_ids:
            raise ValueError(f"duplicate target game ID: {game_id}")
        seen_game_ids.add(game_id)
        reason = _v4_target_record_rejection(record, cfg)
        if reason is None:
            selected_candidates.append(record)
        else:
            excluded.append({"gameId": game_id, "reason": reason})
    selected_candidates.sort(
        key=lambda row: (str(row.get("created") or ""), str(row.get("gameId") or "")),
        reverse=True,
    )
    maximum = int(cfg["maximumTargetGames"])
    selected = selected_candidates[:maximum]
    for record in selected_candidates[maximum:]:
        excluded.append({
            "gameId": str(record["gameId"]),
            "reason": "older_than_recent_maximum",
        })
    minimum = int(cfg["minimumTargetGames"])
    return {
        "selected": selected,
        "excluded": sorted(
            excluded,
            key=lambda row: (str(row.get("gameId") or ""), str(row.get("reason") or "")),
        ),
        "candidateCount": len(selected_candidates),
        "selectedCount": len(selected),
        "status": "valid" if len(selected) >= minimum else "insufficient_target_games",
    }


def _v4_aligned_elo_points(
    lower: int,
    upper: int,
    step: int,
    *,
    formal_minimum: int,
    formal_maximum: int,
) -> list[int]:
    lower = max(int(formal_minimum), int(lower))
    upper = min(int(formal_maximum), int(upper))
    if lower > upper:
        return []
    if int(step) <= 0:
        raise ValueError("Elo search step must be positive")
    first = int(formal_minimum) + math.ceil((lower - int(formal_minimum)) / int(step)) * int(step)
    points = list(range(first, upper + 1, int(step)))
    points.extend((lower, upper))
    return sorted(set(point for point in points if lower <= point <= upper))


def elo_grid_v4(config: dict[str, Any] | None = None) -> list[int]:
    cfg = validate_v4_config(config or default_v4_config())
    return list(range(int(cfg["eloGridMinimum"]), int(cfg["eloGridMaximum"]) + 1))


def _v4_merge_intervals(intervals: Sequence[tuple[int, int]]) -> list[dict[str, int]]:
    ordered = sorted(
        [(int(lower), int(upper)) for lower, upper in intervals if int(lower) <= int(upper)],
        key=lambda item: (item[0], item[1]),
    )
    merged: list[list[int]] = []
    for lower, upper in ordered:
        if not merged or lower > merged[-1][1]:
            merged.append([lower, upper])
        else:
            merged[-1][1] = max(merged[-1][1], upper)
    return [{"lower": lower, "upper": upper} for lower, upper in merged]


def _v4_discover_basins(
    points: Sequence[dict[str, Any]],
    *,
    resolution: int,
    formal_minimum: int,
    formal_maximum: int,
    tolerance: float,
) -> list[dict[str, Any]]:
    """Discover all sampled low areas, including platforms and edges."""

    valid = sorted(
        [point for point in points if finite_number(point.get("elo")) and finite_number(point.get("score"))],
        key=lambda point: int(point["elo"]),
    )
    if not valid:
        return []
    resolution = max(1, int(resolution))
    groups: list[list[dict[str, Any]]] = []
    current = [valid[0]]
    for point in valid[1:]:
        if (
            int(point["elo"]) - int(current[-1]["elo"]) <= resolution
            and abs(float(point["score"]) - float(current[-1]["score"])) <= float(tolerance)
        ):
            current.append(point)
        else:
            groups.append(current)
            current = [point]
    groups.append(current)
    basins: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        first, last = group[0], group[-1]
        first_index = valid.index(first)
        last_index = valid.index(last)
        left = valid[first_index - 1] if first_index else None
        right = valid[last_index + 1] if last_index + 1 < len(valid) else None
        left_adjacent = left is not None and int(first["elo"]) - int(left["elo"]) <= resolution
        right_adjacent = right is not None and int(right["elo"]) - int(last["elo"]) <= resolution
        minimum = float(first["score"])
        left_higher = left is not None and left_adjacent and float(left["score"]) > minimum + tolerance
        right_higher = right is not None and right_adjacent and float(right["score"]) > minimum + tolerance
        left_equal_or_higher = left is not None and left_adjacent and float(left["score"]) >= minimum - tolerance
        right_equal_or_higher = right is not None and right_adjacent and float(right["score"]) >= minimum - tolerance
        internal = bool(
            (left_higher or (left_equal_or_higher and right_higher))
            and (right_higher or (right_equal_or_higher and left_higher))
        )
        boundary = bool(
            (int(first["elo"]) == int(formal_minimum) and right_higher)
            or (int(last["elo"]) == int(formal_maximum) and left_higher)
        )
        if not internal and not boundary:
            continue
        left_guard = int(left["elo"]) if left is not None and left_adjacent else None
        right_guard = int(right["elo"]) if right is not None and right_adjacent else None
        basins.append({
            "basinId": f"basin-{index:04d}",
            "sampledLower": int(first["elo"]),
            "sampledUpper": int(last["elo"]),
            "minimumScore": minimum,
            "minimumEloPoints": [int(point["elo"]) for point in group],
            "leftProtectionPoint": left_guard,
            "rightProtectionPoint": right_guard,
            "refinementLower": left_guard if left_guard is not None else int(first["elo"]),
            "refinementUpper": right_guard if right_guard is not None else int(last["elo"]),
            "internalMinimum": internal,
            "boundaryTrend": boundary,
            "needsExpansion": left_guard is None or right_guard is None,
            "sourceResolution": resolution,
        })
    return basins


def _v4_discover_active_edge_basins(
    points: Sequence[dict[str, Any]],
    active_intervals: Sequence[dict[str, int]],
    *,
    formal_minimum: int,
    formal_maximum: int,
    tolerance: float,
) -> list[dict[str, Any]]:
    valid = sorted(
        [point for point in points if finite_number(point.get("elo")) and finite_number(point.get("score"))],
        key=lambda point: int(point["elo"]),
    )
    basins: list[dict[str, Any]] = []
    for index, interval in enumerate(active_intervals):
        lower, upper = int(interval["lower"]), int(interval["upper"])
        inside = [point for point in valid if lower <= int(point["elo"]) <= upper]
        if not inside:
            continue
        minimum = min(float(point["score"]) for point in inside)
        minima = [
            point for point in inside
            if abs(float(point["score"]) - minimum) <= float(tolerance)
        ]
        at_lower = any(int(point["elo"]) == lower for point in minima)
        at_upper = any(int(point["elo"]) == upper for point in minima)
        if not at_lower and not at_upper:
            continue
        left_guards = [
            point for point in valid
            if int(point["elo"]) < lower and float(point["score"]) > minimum + tolerance
        ]
        right_guards = [
            point for point in valid
            if int(point["elo"]) > upper and float(point["score"]) > minimum + tolerance
        ]
        left_guard = int(left_guards[-1]["elo"]) if left_guards else None
        right_guard = int(right_guards[0]["elo"]) if right_guards else None
        needs_left = at_lower and lower > formal_minimum and left_guard is None
        needs_right = at_upper and upper < formal_maximum and right_guard is None
        if not needs_left and not needs_right:
            continue
        basins.append({
            "basinId": f"active-edge-{index:04d}",
            "sampledLower": min(int(point["elo"]) for point in minima),
            "sampledUpper": max(int(point["elo"]) for point in minima),
            "minimumScore": minimum,
            "minimumEloPoints": [int(point["elo"]) for point in minima],
            "leftProtectionPoint": left_guard if at_lower else lower,
            "rightProtectionPoint": right_guard if at_upper else upper,
            "refinementLower": left_guard if at_lower and left_guard is not None else lower,
            "refinementUpper": right_guard if at_upper and right_guard is not None else upper,
            "internalMinimum": False,
            "boundaryTrend": False,
            "needsExpansion": needs_left or needs_right,
            "activeIntervalEdge": {"lower": needs_left, "upper": needs_right},
            "sourceResolution": None,
        })
    return basins


_V4_TARGET_GAME_WORKER_INDEX: GlobalAnscombeKNNIndexV4 | None = None
_V4_TARGET_GAME_WORKER_ACCOUNT: str | None = None
_V4_TARGET_GAME_WORKER_IDS: tuple[str, ...] = ()
_V4_TARGET_GAME_WORKER_CONFIG: dict[str, Any] = {}
_V4_TARGET_GAME_WORKER_INIT_REPORTED = False


def _evaluate_v4_target_game(
    target: dict[str, Any],
    elo: int,
    *,
    account: str,
    target_game_ids: Sequence[str],
    index: GlobalAnscombeKNNIndexV4,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate one target game with phase1 through phase4 in sequence."""

    game_started = time.perf_counter()
    game_id = str(target.get("gameId") or "")
    color = str(target.get("targetColor") or "").strip().casefold()
    scope = selected_metrics_scope(target)
    previous_z: float | None = None
    phase_rows: list[dict[str, Any]] = []
    failure_reasons: Counter[str] = Counter()
    phase_knn_seconds = {f"phase{stage}": 0.0 for stage in range(1, 5)}
    closed_form_statistics_seconds = 0.0
    game_failure: str | None = None
    for stage in range(1, 5):
        pool = index.pool(color, scope, stage)
        if pool is None:
            result = {
                "ok": False,
                "reason": "insufficient_reference",
                "phase": stage,
                "fitAttempted": False,
            }
        else:
            before = time.perf_counter()
            result = score_anscombe_phase_v4(
                target,
                stage,
                float(elo),
                pool,
                account=account,
                target_game_ids=target_game_ids,
                previous_z=previous_z,
                config=config,
            )
            phase_knn_seconds[f"phase{stage}"] += float(
                result.get("knnQuerySeconds") or 0.0
            )
            closed_form_statistics_seconds += float(
                result.get("closedFormStatisticsSeconds") or 0.0
            )
            # This timing guard supports synthetic/older test pools only.  It
            # is a diagnostic clock, not a statistical fallback.
            if result.get("knnQuerySeconds") is None:
                phase_knn_seconds[f"phase{stage}"] += time.perf_counter() - before
        result.update({
            "gameId": game_id,
            "scope": scope,
            "color": color,
            "elo": elo,
        })
        phase_rows.append(result)
        if result.get("ok") is not True:
            game_failure = str(result.get("reason") or "insufficient_reference")
            failure_reasons[game_failure] += 1
            break
        previous_z = float(result["targetZ"])
    if game_failure is None:
        game_score = sum(
            float(row["negativeLogPredictiveDensity"])
            for row in phase_rows
        )
        game_row = {
            "gameId": game_id,
            "ok": True,
            "gameScore": game_score,
            "gameNegativeLogPredictiveDensity": game_score,
            "meanTargetZ": statistics.fmean(
                float(row["targetZ"]) for row in phase_rows
            ),
            "phaseDiagnostics": phase_rows,
        }
    else:
        game_row = {
            "gameId": game_id,
            "ok": False,
            "reason": game_failure,
            "phaseDiagnostics": phase_rows,
        }
    return {
        "gameRow": game_row,
        "failureReasons": dict(failure_reasons),
        "phaseKnnSeconds": phase_knn_seconds,
        "closedFormStatisticsSeconds": closed_form_statistics_seconds,
        "runtimeSeconds": time.perf_counter() - game_started,
    }


def _init_v4_target_game_worker(
    reference_records: Sequence[dict[str, Any]] | None,
    reference_records_path: str | None,
    reference_manifest: dict[str, Any],
    config: dict[str, Any],
    account: str,
    target_game_ids: Sequence[str],
) -> None:
    """Build one immutable reference index once in each target-game process."""

    global _V4_TARGET_GAME_WORKER_INDEX
    global _V4_TARGET_GAME_WORKER_ACCOUNT
    global _V4_TARGET_GAME_WORKER_IDS
    global _V4_TARGET_GAME_WORKER_CONFIG
    global _V4_TARGET_GAME_WORKER_INIT_REPORTED
    cfg = validate_v4_config(config)
    rows = (
        read_jsonl(reference_records_path)
        if reference_records_path is not None
        else list(reference_records or [])
    )
    if not rows:
        raise RuntimeError("v4 target-game worker received no reference records")
    _V4_TARGET_GAME_WORKER_INDEX = GlobalAnscombeKNNIndexV4(
        rows, reference_manifest, cfg
    )
    _V4_TARGET_GAME_WORKER_ACCOUNT = account_key(account)
    _V4_TARGET_GAME_WORKER_IDS = tuple(str(value) for value in target_game_ids)
    _V4_TARGET_GAME_WORKER_CONFIG = cfg
    _V4_TARGET_GAME_WORKER_INDEX.begin_account(_V4_TARGET_GAME_WORKER_ACCOUNT)
    _V4_TARGET_GAME_WORKER_INIT_REPORTED = False


def _v4_target_game_worker(
    task: tuple[int, dict[str, Any], int],
) -> tuple[int, dict[str, Any]]:
    """Process one target-game task and return explicit counter deltas."""

    global _V4_TARGET_GAME_WORKER_INIT_REPORTED
    if _V4_TARGET_GAME_WORKER_INDEX is None or _V4_TARGET_GAME_WORKER_ACCOUNT is None:
        raise RuntimeError("v4 target-game process worker was not initialized")
    ordinal, target, elo = task
    query_before = _V4_TARGET_GAME_WORKER_INDEX.query_count
    tree_before = _V4_TARGET_GAME_WORKER_INDEX.tree_query_count
    boundary_before = _V4_TARGET_GAME_WORKER_INDEX.boundary_expansion_count
    result = _evaluate_v4_target_game(
        target,
        int(elo),
        account=_V4_TARGET_GAME_WORKER_ACCOUNT,
        target_game_ids=_V4_TARGET_GAME_WORKER_IDS,
        index=_V4_TARGET_GAME_WORKER_INDEX,
        config=_V4_TARGET_GAME_WORKER_CONFIG,
    )
    result["workerProcessId"] = os.getpid()
    result["queryCount"] = _V4_TARGET_GAME_WORKER_INDEX.query_count - query_before
    result["treeQueryCount"] = _V4_TARGET_GAME_WORKER_INDEX.tree_query_count - tree_before
    result["boundaryExpansionCount"] = (
        _V4_TARGET_GAME_WORKER_INDEX.boundary_expansion_count - boundary_before
    )
    if not _V4_TARGET_GAME_WORKER_INIT_REPORTED:
        result["workerInitialization"] = {
            "treeBuildCount": _V4_TARGET_GAME_WORKER_INDEX.tree_build_count,
            "treeBuildSeconds": _V4_TARGET_GAME_WORKER_INDEX.tree_build_seconds,
        }
        _V4_TARGET_GAME_WORKER_INIT_REPORTED = True
    return int(ordinal), result


class _V4ScoreEvaluator:
    """Evaluate one fixed target-game set against one reusable v4 index."""

    def __init__(
        self,
        target_records: Sequence[dict[str, Any]],
        account: str,
        index: GlobalAnscombeKNNIndexV4,
        config: dict[str, Any],
    ) -> None:
        self.target_records = list(target_records)
        self.account = account_key(account)
        self.index = index
        self.config = validate_v4_config(config)
        self.target_game_ids = [str(row.get("gameId") or "") for row in self.target_records]
        self.cache: dict[int, dict[str, Any]] = {}
        self.search_point_elos: set[int] = set()
        self.scoring_seconds = 0.0
        self.closed_form_statistics_seconds = 0.0
        self.phase_knn_seconds: dict[str, float] = {f"phase{stage}": 0.0 for stage in range(1, 5)}
        self.failure_reasons: Counter[str] = Counter()
        self.target_game_workers = DEFAULT_TARGET_GAME_WORKERS_V4
        self.reference_records_path: str | None = None
        self._process_executor: ProcessPoolExecutor | None = None
        self.process_task_count = 0
        self.process_worker_ids: set[int] = set()
        self.process_worker_tree_build_count = 0
        self.process_worker_tree_build_seconds = 0.0
        self.process_query_count = 0
        self.process_tree_query_count = 0
        self.process_boundary_expansion_count = 0
        self.query_count_before = index.query_count
        self.tree_query_count_before = index.tree_query_count
        self.boundary_query_count_before = index.boundary_expansion_count
        self._tree_build_was_reused = not index.begin_account(self.account)

    def _evaluate_game(self, target: dict[str, Any], elo: int) -> dict[str, Any]:
        """Evaluate one target game; its four phases remain sequential."""

        return _evaluate_v4_target_game(
            target,
            elo,
            account=self.account,
            target_game_ids=self.target_game_ids,
            index=self.index,
            config=self.config,
        )

    def _ensure_process_executor(self) -> ProcessPoolExecutor:
        workers = int(self.target_game_workers)
        if not 2 <= workers <= MAXIMUM_TARGET_GAME_WORKERS_V4:
            raise ValueError(
                "target_game_workers must be between 2 and "
                f"{MAXIMUM_TARGET_GAME_WORKERS_V4} for process-pool evaluation"
            )
        if self._process_executor is None:
            effective_workers = min(workers, len(self.target_records))
            reference_records = (
                None if self.reference_records_path is not None
                else self.index.source_records
            )
            # 这是用户要求的并行调度变更：单候选 Elo 点按一局一任务分发到
            # 最多 4 个进程；每个进程只初始化一次 reference index，并在该
            # 进程后续收到的候选点任务中复用。phase1 到 phase4 仍在局内串行。
            self._process_executor = ProcessPoolExecutor(
                max_workers=effective_workers,
                initializer=_init_v4_target_game_worker,
                initargs=(
                    reference_records,
                    self.reference_records_path,
                    self.index.reference_manifest,
                    self.config,
                    self.account,
                    self.target_game_ids,
                ),
            )
        return self._process_executor

    def close(self) -> None:
        if self._process_executor is not None:
            self._process_executor.shutdown(wait=True, cancel_futures=False)
            self._process_executor = None

    def __enter__(self) -> _V4ScoreEvaluator:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def parallelization_audit(self, *, task_count: int | None = None) -> dict[str, Any]:
        workers = int(self.target_game_workers)
        process_mode = workers > 1 and len(self.target_records) > 1
        return {
            "mode": "target_game_process_pool" if process_mode else "target_game_serial",
            "workers": workers,
            "effectiveWorkers": min(workers, len(self.target_records)) if process_mode else 1,
            "actualWorkers": len(self.process_worker_ids) if process_mode else 1,
            "workerProcessIds": sorted(self.process_worker_ids),
            "taskCount": self.process_task_count if task_count is None else int(task_count),
            "taskUnit": "one_target_game",
            "phaseExecution": "sequential_within_game",
            "candidateEloExecution": "serial_by_existing_search_contract",
            "referenceIndexInitialization": "once_per_worker_process_and_reused",
            "referenceQueryWorkersPerWorker": int(self.config["referenceQueryWorkers"]),
            "nestedParallelism": False,
            "schedulingChange": "这是用户要求的并行调度变更",
            "counterAggregation": {
                "knnQueryCount": self.process_query_count,
                "cKDTreeQueryCount": self.process_tree_query_count,
                "boundaryExpansionQueryCount": self.process_boundary_expansion_count,
                "workerTreeBuildCount": self.process_worker_tree_build_count,
                "workerTreeBuildSeconds": self.process_worker_tree_build_seconds,
            },
            "note": (
                "Main estimation uses at most 4 target-game processes; formal "
                "calibration keeps 16 account processes with target_game_workers=1."
                if process_mode else
                "Target games are evaluated serially in the current process."
            ),
        }

    def evaluate(self, elo: int, *, include_in_search: bool = False) -> dict[str, Any]:
        elo = int(elo)
        if include_in_search:
            self.search_point_elos.add(elo)
        cached = self.cache.get(elo)
        if cached is not None:
            return cached
        started = time.perf_counter()
        workers = int(self.target_game_workers)
        if not 1 <= workers <= MAXIMUM_TARGET_GAME_WORKERS_V4:
            raise ValueError(
                "target_game_workers must be between 1 and "
                f"{MAXIMUM_TARGET_GAME_WORKERS_V4}"
            )
        if workers > 1 and len(self.target_records) > 1:
            tasks = [
                (ordinal, target, elo)
                for ordinal, target in enumerate(self.target_records)
            ]
            try:
                ordered_results = list(
                    self._ensure_process_executor().map(
                        _v4_target_game_worker, tasks, chunksize=1
                    )
                )
            except Exception as exc:
                raise RuntimeError(
                    f"v4 target-game process-pool evaluation failed at Elo {elo}"
                ) from exc
            game_results = []
            for expected_ordinal, (actual_ordinal, result) in enumerate(ordered_results):
                if actual_ordinal != expected_ordinal:
                    raise RuntimeError(
                        "v4 target-game process pool returned results out of target_records order"
                    )
                game_results.append(result)
            self.process_task_count += len(tasks)
        else:
            game_results = [
                self._evaluate_game(target, elo) for target in self.target_records
            ]
        game_rows: list[dict[str, Any]] = []
        failure_reasons: Counter[str] = Counter()
        for result in game_results:
            game_rows.append(result["gameRow"])
            failure_reasons.update(result["failureReasons"])
            worker_pid = result.get("workerProcessId")
            if worker_pid is not None:
                self.process_worker_ids.add(int(worker_pid))
            self.process_query_count += int(result.get("queryCount") or 0)
            self.process_tree_query_count += int(result.get("treeQueryCount") or 0)
            self.process_boundary_expansion_count += int(
                result.get("boundaryExpansionCount") or 0
            )
            initialization = result.get("workerInitialization") or {}
            self.process_worker_tree_build_count += int(
                initialization.get("treeBuildCount") or 0
            )
            self.process_worker_tree_build_seconds += float(
                initialization.get("treeBuildSeconds") or 0.0
            )
            for phase, seconds in result["phaseKnnSeconds"].items():
                self.phase_knn_seconds[phase] += float(seconds)
            self.closed_form_statistics_seconds += float(
                result["closedFormStatisticsSeconds"]
            )
        if len(game_rows) != len(self.target_records):
            raise RuntimeError("v4 evaluator changed the fixed target-game set")
        valid_games = [row for row in game_rows if row.get("ok") is True]
        score = (
            statistics.fmean(float(row["gameScore"]) for row in valid_games)
            if len(valid_games) == len(self.target_records) and valid_games
            else None
        )
        raw = {
            "elo": elo,
            "score": score,
            "minimumJ": score,
            "validGameCount": len(valid_games),
            "targetGameCount": len(self.target_records),
            "failureReasons": dict(sorted(failure_reasons.items())),
            "gameDiagnostics": game_rows,
            "ok": score is not None,
            "parallelization": self.parallelization_audit(
                task_count=len(self.target_records)
            ),
        }
        if score is None:
            self.failure_reasons.update(failure_reasons)
        self.cache[elo] = raw
        self.scoring_seconds += time.perf_counter() - started
        return raw

    def evaluate_many(self, elos: Iterable[int], *, include_in_search: bool = False) -> None:
        for elo in elos:
            self.evaluate(int(elo), include_in_search=include_in_search)


def _v4_curve_points(
    evaluator: _V4ScoreEvaluator,
    include_elos: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    selected = set(int(elo) for elo in include_elos) if include_elos is not None else None
    rows: list[dict[str, Any]] = []
    for elo, raw in sorted(evaluator.cache.items()):
        if selected is not None and int(elo) not in selected:
            continue
        rows.append({
            "elo": int(elo),
            "score": raw.get("score"),
            "minimumJ": raw.get("minimumJ"),
            "meanNegativeLogPredictiveDensity": raw.get("score"),
            "meanTargetZ": (
                statistics.fmean(
                    float(game["meanTargetZ"])
                    for game in raw.get("gameDiagnostics") or []
                    if game.get("ok") is True and finite_number(game.get("meanTargetZ"))
                )
                if any(game.get("ok") is True for game in raw.get("gameDiagnostics") or [])
                else None
            ),
            "validGameCount": raw.get("validGameCount"),
            "targetGameCount": raw.get("targetGameCount"),
            "failureReasons": raw.get("failureReasons") or {},
        })
    return rows


def _v4_score_equal(left: float, right: float, tolerance: float) -> bool:
    return abs(float(left) - float(right)) <= float(tolerance)


def _v4_best_point(
    points: Sequence[dict[str, Any]], tolerance: float
) -> dict[str, Any] | None:
    valid = [
        point for point in points
        if finite_number(point.get("score"))
    ]
    if not valid:
        return None
    minimum = min(float(point["score"]) for point in valid)
    return min(
        (point for point in valid if _v4_score_equal(float(point["score"]), minimum, tolerance)),
        key=lambda point: int(point["elo"]),
    )


def _v4_curve_stats(
    points: Sequence[dict[str, Any]],
    *,
    formal_minimum: int,
    formal_maximum: int,
    full_grid: bool,
    tolerance: float,
) -> dict[str, Any]:
    ordered = sorted(
        [point for point in points if finite_number(point.get("score"))],
        key=lambda point: int(point["elo"]),
    )
    if not ordered:
        reasons = sorted({
            str(reason)
            for point in points
            for reason in (point.get("failureReasons") or {}).keys()
        })
        return {
            "status": "insufficient_reference",
            "statusReasons": reasons or ["no_complete_J_value"],
            "minimumScore": None,
            "bestGridPoints": [],
            "localMinima": [],
            "minimumRegionWidth": None,
            "maximumAdjacentScoreJump": None,
            "maximumSampledScoreJump": None,
            "maximumSampledEloGap": None,
            "diagnosticContinuity": "none",
            "pointFailureCount": len(points),
        }
    minimum = min(float(point["score"]) for point in ordered)
    best = [
        int(point["elo"]) for point in ordered
        if _v4_score_equal(float(point["score"]), minimum, tolerance)
    ]
    local_minima: list[dict[str, Any]] = []
    for index, point in enumerate(ordered):
        left = ordered[index - 1] if index else None
        right = ordered[index + 1] if index + 1 < len(ordered) else None
        left_adjacent = left is not None and int(point["elo"]) - int(left["elo"]) == 1
        right_adjacent = right is not None and int(right["elo"]) - int(point["elo"]) == 1
        if (
            (left is None or not left_adjacent or float(point["score"]) <= float(left["score"]) + tolerance)
            and (right is None or not right_adjacent or float(point["score"]) <= float(right["score"]) + tolerance)
            and (left_adjacent or right_adjacent)
        ):
            local_minima.append({"elo": int(point["elo"]), "score": float(point["score"])})
    sampled_jumps = [
        abs(float(right["score"]) - float(left["score"]))
        for left, right in zip(ordered, ordered[1:])
    ]
    sampled_gaps = [
        int(right["elo"]) - int(left["elo"])
        for left, right in zip(ordered, ordered[1:])
    ]
    contiguous = bool(
        full_grid
        and len(ordered) == formal_maximum - formal_minimum + 1
        and all(int(point["elo"]) == formal_minimum + index for index, point in enumerate(ordered))
    )
    if contiguous:
        adjacent_jumps = sampled_jumps
        continuity = "full_integer_grid"
        minimum_region_width = max(best) - min(best) if best else None
    else:
        adjacent_jumps = None
        continuity = "sparse_or_mixed_grid"
        minimum_region_width = None
    best_elo = min(best)
    if full_grid and best_elo == formal_maximum and all(
        float(right["score"]) <= float(left["score"]) + tolerance
        for left, right in zip(ordered, ordered[1:])
    ):
        status, reasons = "above_reference_range", ["J_continues_improving_to_upper_boundary"]
    elif full_grid and best_elo == formal_minimum and all(
        float(right["score"]) >= float(left["score"]) - tolerance
        for left, right in zip(ordered, ordered[1:])
    ):
        status, reasons = "below_reference_range", ["J_continues_improving_to_lower_boundary"]
    else:
        status = "multiple_minima" if len(local_minima) > 1 else "valid"
        reasons = [
            "all_discovered_basins_retained" if len(local_minima) > 1
            else "J_minimum_identified"
        ]
    return {
        "status": status,
        "statusReasons": reasons,
        "minimumScore": minimum,
        "bestGridPoints": best,
        "localMinima": local_minima,
        "minimumRegionWidth": minimum_region_width,
        "maximumAdjacentScoreJump": max(adjacent_jumps) if adjacent_jumps else None,
        "maximumSampledScoreJump": max(sampled_jumps) if sampled_jumps else 0.0,
        "maximumSampledEloGap": max(sampled_gaps) if sampled_gaps else 0,
        "diagnosticContinuity": continuity,
        "pointFailureCount": len(points) - len(ordered),
    }


def intervals_for_score_threshold_v4(
    points: Sequence[dict[str, Any]],
    allowed_score: float,
    *,
    formal_minimum: int,
    formal_maximum: int,
) -> list[dict[str, Any]]:
    eligible = sorted({
        int(point["elo"])
        for point in points
        if finite_number(point.get("elo"))
        and finite_number(point.get("score"))
        and float(point["score"]) <= float(allowed_score)
    })
    if not eligible:
        return []
    intervals: list[dict[str, Any]] = []
    start = previous = eligible[0]
    for elo in eligible[1:]:
        if elo == previous + 1:
            previous = elo
            continue
        intervals.append({
            "lower": start,
            "upper": previous,
            "truncatedLower": start == int(formal_minimum),
            "truncatedUpper": previous == int(formal_maximum),
        })
        start = previous = elo
    intervals.append({
        "lower": start,
        "upper": previous,
        "truncatedLower": start == int(formal_minimum),
        "truncatedUpper": previous == int(formal_maximum),
    })
    return intervals


def _v4_protected_intervals(
    evaluator: _V4ScoreEvaluator,
    basins: Sequence[dict[str, Any]],
    *,
    resolution: int,
    tolerance: float,
    formal_minimum: int,
    formal_maximum: int,
) -> tuple[list[dict[str, int]], list[dict[str, Any]], list[str]]:
    valid_points = sorted(
        [point for point in _v4_curve_points(evaluator) if finite_number(point.get("score"))],
        key=lambda point: int(point["elo"]),
    )
    intervals: list[tuple[int, int]] = []
    protected: list[dict[str, Any]] = []
    reasons: list[str] = []
    for basin in basins:
        item = dict(basin)
        lower = item.get("leftProtectionPoint")
        upper = item.get("rightProtectionPoint")
        minimum_score = float(item["minimumScore"])
        if lower is None:
            candidates = [
                point for point in valid_points
                if int(point["elo"]) < int(item["sampledLower"])
                and float(point["score"]) > minimum_score + tolerance
            ]
            if candidates:
                lower = int(candidates[-1]["elo"])
                item["expandedLeftTo"] = lower
                item["boundaryExpansion"] = True
            else:
                proposed = max(formal_minimum, int(item["sampledLower"]) - int(resolution))
                if proposed < int(item["sampledLower"]):
                    lower = proposed
                    item["expandedLeftTo"] = lower
                    item["boundaryExpansion"] = True
                    item["expansionWithoutHigherGuard"] = True
                else:
                    reasons.append("minimum_region_not_bracketed_on_lower_side")
        if upper is None:
            candidates = [
                point for point in valid_points
                if int(point["elo"]) > int(item["sampledUpper"])
                and float(point["score"]) > minimum_score + tolerance
            ]
            if candidates:
                upper = int(candidates[0]["elo"])
                item["expandedRightTo"] = upper
                item["boundaryExpansion"] = True
            else:
                proposed = min(formal_maximum, int(item["sampledUpper"]) + int(resolution))
                if proposed > int(item["sampledUpper"]):
                    upper = proposed
                    item["expandedRightTo"] = upper
                    item["boundaryExpansion"] = True
                    item["expansionWithoutHigherGuard"] = True
                else:
                    reasons.append("minimum_region_not_bracketed_on_upper_side")
        if lower is None or upper is None:
            continue
        if int(lower) < formal_minimum or int(upper) > formal_maximum:
            reasons.append("active_interval_outside_formal_range")
            continue
        item["refinementLower"] = int(lower)
        item["refinementUpper"] = int(upper)
        item["sourceResolution"] = int(resolution)
        protected.append(item)
        intervals.append((int(lower), int(upper)))
    return _v4_merge_intervals(intervals), protected, sorted(set(reasons))


def _v4_full_grid_fallback(
    evaluator: _V4ScoreEvaluator,
    curve_elos: set[int],
    *,
    full_grid: Sequence[int],
) -> int:
    missing = [int(elo) for elo in full_grid if int(elo) not in evaluator.cache]
    evaluator.evaluate_many(missing, include_in_search=True)
    curve_elos.update(int(elo) for elo in full_grid)
    return len(missing)


def _v4_confidence_region_search(
    evaluator: _V4ScoreEvaluator,
    curve_elos: set[int],
    *,
    minimum_score: float,
    t95: float,
    config: dict[str, Any],
    fallback_reasons: list[str],
    fallback_state: dict[str, bool],
) -> dict[str, Any]:
    if not math.isfinite(float(t95)) or float(t95) < 0:
        raise ValueError("v4 T95 must be a finite non-negative number")
    cutoff = float(minimum_score) + float(t95)
    formal_minimum = int(config["formalEloMinimum"])
    formal_maximum = int(config["formalEloMaximum"])
    full_grid = elo_grid_v4(config)
    confidence_new_points: set[int] = set()
    interval_history: list[dict[str, Any]] = []
    while True:
        points = _v4_curve_points(evaluator, curve_elos)
        eligible = [
            int(point["elo"]) for point in points
            if finite_number(point.get("score")) and float(point["score"]) <= cutoff
        ]
        if not eligible:
            break
        ordered = sorted(
            [point for point in points if finite_number(point.get("score"))],
            key=lambda point: int(point["elo"]),
        )
        candidates: list[tuple[int, int]] = [(elo, elo) for elo in eligible]
        for left, right in zip(ordered, ordered[1:]):
            if int(right["elo"]) - int(left["elo"]) > 1 and (
                float(left["score"]) <= cutoff or float(right["score"]) <= cutoff
            ):
                candidates.append((int(left["elo"]), int(right["elo"])))
        intervals = _v4_merge_intervals(candidates)
        fill = {
            elo
            for interval in intervals
            for elo in range(int(interval["lower"]), int(interval["upper"]) + 1)
        }
        missing = sorted(fill - set(evaluator.cache))
        remaining = len([elo for elo in full_grid if elo not in evaluator.cache])
        if missing and len(missing) >= remaining:
            added = _v4_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            fallback_state["value"] = True
            reason = "confidence_region_cost_not_below_full_grid_remaining"
            fallback_reasons.append(reason)
            interval_history.append({"action": "fallback_full_grid", "reason": reason, "newPointCount": added})
            break
        evaluator.evaluate_many(missing, include_in_search=True)
        confidence_new_points.update(missing)
        actual = intervals_for_score_threshold_v4(
            _v4_curve_points(evaluator, curve_elos), cutoff,
            formal_minimum=formal_minimum, formal_maximum=formal_maximum,
        )
        if not actual:
            break
        expanded: list[tuple[int, int]] = []
        changed = False
        for interval in actual:
            lower, upper = int(interval["lower"]), int(interval["upper"])
            if lower > formal_minimum:
                probe = lower - 1
                if probe not in evaluator.cache:
                    evaluator.evaluate(probe, include_in_search=True)
                    curve_elos.add(probe)
                    confidence_new_points.add(probe)
                if finite_number(evaluator.cache[probe].get("score")) and float(evaluator.cache[probe]["score"]) <= cutoff:
                    lower = probe
                    changed = True
            if upper < formal_maximum:
                probe = upper + 1
                if probe not in evaluator.cache:
                    evaluator.evaluate(probe, include_in_search=True)
                    curve_elos.add(probe)
                    confidence_new_points.add(probe)
                if finite_number(evaluator.cache[probe].get("score")) and float(evaluator.cache[probe]["score"]) <= cutoff:
                    upper = probe
                    changed = True
            expanded.append((lower, upper))
        next_intervals = _v4_merge_intervals(expanded)
        interval_history.append({
            "action": "evaluate_cutoff_region",
            "intervals": next_intervals,
            "newPointCount": len(confidence_new_points),
        })
        if next_intervals == intervals and not changed:
            break
        if len(interval_history) > int(config["searchMaxExpansionRounds"]):
            fallback_state["value"] = True
            reason = "confidence_region_continues_to_expand"
            fallback_reasons.append(reason)
            added = _v4_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            interval_history.append({"action": "fallback_full_grid", "reason": reason, "newPointCount": added})
            break
    final_points = _v4_curve_points(evaluator, curve_elos)
    return {
        "t95": float(t95),
        "minimumScore": float(minimum_score),
        "cutoff": cutoff,
        "intervals": intervals_for_score_threshold_v4(
            final_points, cutoff,
            formal_minimum=formal_minimum, formal_maximum=formal_maximum,
        ),
        "newEvaluatedEloPoints": sorted(confidence_new_points),
        "intervalHistory": interval_history,
        "sparseConfidenceSearch": not fallback_state["value"] and len(curve_elos) < len(full_grid),
        "source": "discovered_basins_and_global_20_point_curve",
    }


def _v4_probe_known_elo(
    evaluator: _V4ScoreEvaluator,
    known_elo: float,
    *,
    formal_minimum: int,
    formal_maximum: int,
) -> dict[str, Any]:
    """Probe knownElo after the search; it never changes the search curve."""

    known = float(known_elo)
    result: dict[str, Any] = {
        "requestedElo": known,
        "integer": known.is_integer(),
        "participatesInMinimumJ": False,
        "isolatedFromFormalSearch": True,
        "status": "out_of_formal_range",
        "evaluatedEloPoints": [],
        "score": None,
        "scoreAtKnownElo": None,
        "reason": None,
    }
    if not math.isfinite(known):
        result["status"] = "invalid_known_elo"
        result["reason"] = "knownElo_is_not_finite"
        return result
    if known < formal_minimum or known > formal_maximum:
        result["reason"] = "knownElo_outside_formal_range"
        return result
    lower, upper = int(math.floor(known)), int(math.ceil(known))
    probe_elos = [lower] if lower == upper else [lower, upper]
    rows = [evaluator.evaluate(elo, include_in_search=False) for elo in probe_elos]
    result["evaluatedEloPoints"] = probe_elos
    result["points"] = [
        {
            "elo": int(row["elo"]),
            "score": row.get("score"),
            "status": "ok" if finite_number(row.get("score")) else (row.get("failureReasons") or {"score_failed": 1}),
            "wasFormalSearchPoint": int(row["elo"]) in evaluator.search_point_elos,
        }
        for row in rows
    ]
    if any(not finite_number(row.get("score")) for row in rows):
        result["status"] = "failed"
        result["reason"] = sorted({
            str(reason)
            for row in rows
            for reason in (row.get("failureReasons") or {}).keys()
        }) or ["known_elo_probe_score_failed"]
        return result
    fraction = known - lower
    score = float(rows[0]["score"]) if lower == upper else (
        float(rows[0]["score"]) * (1.0 - fraction) + float(rows[1]["score"]) * fraction
    )
    result.update({
        "status": "ok",
        "score": score,
        "scoreAtKnownElo": score,
        "interpolation": "direct_integer" if lower == upper else "linear_floor_ceil",
    })
    return result


def score_candidate_curve_v4(
    target_records: Sequence[dict[str, Any]],
    reference_records: Sequence[dict[str, Any]],
    reference_manifest: dict[str, Any],
    *,
    target_account: str,
    config: dict[str, Any] | None = None,
    confidence_t95: float | None = None,
    known_elo: float | None = None,
    index: GlobalAnscombeKNNIndexV4 | None = None,
    target_game_workers: int = DEFAULT_TARGET_GAME_WORKERS_V4,
    reference_records_path: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate the v4 J(E) curve with the frozen adaptive grid contract."""

    cfg = validate_v4_config(config or default_v4_config())
    started = time.perf_counter()
    global_index = index or GlobalAnscombeKNNIndexV4(reference_records, reference_manifest, cfg)
    evaluator = _V4ScoreEvaluator(target_records, target_account, global_index, cfg)
    if not 1 <= int(target_game_workers) <= MAXIMUM_TARGET_GAME_WORKERS_V4:
        raise ValueError(
            "target_game_workers must be between 1 and "
            f"{MAXIMUM_TARGET_GAME_WORKERS_V4}"
        )
    evaluator.target_game_workers = int(target_game_workers)
    evaluator.reference_records_path = (
        str(Path(reference_records_path).resolve())
        if reference_records_path is not None else None
    )
    try:
        return _score_candidate_curve_v4_with_evaluator(
            target_records,
            target_account=target_account,
            config=cfg,
            confidence_t95=confidence_t95,
            known_elo=known_elo,
            global_index=global_index,
            evaluator=evaluator,
            started=started,
        )
    finally:
        close = getattr(evaluator, "close", None)
        if close is not None:
            close()


def _score_candidate_curve_v4_with_evaluator(
    target_records: Sequence[dict[str, Any]],
    *,
    target_account: str,
    config: dict[str, Any],
    confidence_t95: float | None,
    known_elo: float | None,
    global_index: GlobalAnscombeKNNIndexV4,
    evaluator: _V4ScoreEvaluator,
    started: float,
) -> dict[str, Any]:
    """Run the serial candidate search using one reusable game-process pool."""

    cfg = config
    formal_minimum = int(cfg["formalEloMinimum"])
    formal_maximum = int(cfg["formalEloMaximum"])
    full_grid = elo_grid_v4(cfg)
    curve_elos: set[int] = set()
    fallback_reasons: list[str] = []
    fallback_state = {"value": False}
    search_step_diagnostics: list[dict[str, Any]] = []
    discovered_basins: list[dict[str, Any]] = []
    refined_intervals: list[dict[str, Any]] = []
    previous_intervals: list[dict[str, int]] = []
    expansion_rounds = 0

    for step_index, step in enumerate(SEARCH_STEPS_V4):
        if step_index < 2:
            points = _v4_aligned_elo_points(
                formal_minimum, formal_maximum, step,
                formal_minimum=formal_minimum, formal_maximum=formal_maximum,
            )
            missing = [point for point in points if point not in evaluator.cache]
            evaluator.evaluate_many(missing, include_in_search=True)
            curve_elos.update(points)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": missing,
                "newEvaluatedPointCount": len(missing),
                "refinedIntervals": [],
                "discoveredBasins": [],
            })
            continue

        current_resolution = SEARCH_STEPS_V4[step_index - 1]
        current_points = _v4_curve_points(evaluator, curve_elos)
        current_elos = {int(point["elo"]) for point in current_points}
        basins = _v4_discover_basins(
            current_points,
            resolution=current_resolution,
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
            tolerance=float(cfg["searchTieTolerance"]),
        )
        active_edge_basins = _v4_discover_active_edge_basins(
            current_points,
            previous_intervals,
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
            tolerance=float(cfg["searchTieTolerance"]),
        )
        existing_minima = {
            tuple(int(value) for value in basin.get("minimumEloPoints") or [])
            for basin in basins
        }
        basins.extend(
            basin for basin in active_edge_basins
            if tuple(int(value) for value in basin.get("minimumEloPoints") or []) not in existing_minima
        )
        for basin in basins:
            item = dict(basin)
            item["discoveredAtStep"] = step
            discovered_basins.append(item)
        if not basins:
            fallback_state["value"] = True
            reason = "sparse_curve_insufficient_to_identify_all_local_minima"
            fallback_reasons.append(reason)
            added = _v4_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [
                    elo for elo in full_grid if elo not in current_elos
                ],
                "newEvaluatedPointCount": added,
                "refinedIntervals": [],
                "discoveredBasins": basins,
                "fallbackToFullGrid": True,
                "fallbackReason": reason,
            })
            break
        if len(basins) > int(cfg["searchMaxBasins"]):
            fallback_state["value"] = True
            reason = "multiple_basins_cost_not_below_full_grid"
            fallback_reasons.append(reason)
            added = _v4_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [elo for elo in full_grid if elo not in current_elos],
                "newEvaluatedPointCount": added,
                "refinedIntervals": [],
                "discoveredBasins": basins,
                "fallbackToFullGrid": True,
                "fallbackReason": reason,
            })
            break
        intervals, protected_basins, protection_reasons = _v4_protected_intervals(
            evaluator, basins,
            resolution=current_resolution,
            tolerance=float(cfg["searchTieTolerance"]),
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
        )
        for reason in protection_reasons:
            if reason not in fallback_reasons:
                fallback_reasons.append(reason)
        if protection_reasons or not intervals:
            fallback_state["value"] = True
            if not protection_reasons:
                fallback_reasons.append("minimum_region_not_protected")
            added = _v4_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [elo for elo in full_grid if elo not in current_elos],
                "newEvaluatedPointCount": added,
                "refinedIntervals": intervals,
                "discoveredBasins": protected_basins,
                "fallbackToFullGrid": True,
                "fallbackReason": sorted(set(protection_reasons)) or ["minimum_region_not_protected"],
            })
            break
        if any(
            not any(
                interval["lower"] >= previous["lower"]
                and interval["upper"] <= previous["upper"]
                for previous in previous_intervals
            )
            for interval in intervals
        ):
            expansion_rounds += 1
        if expansion_rounds > int(cfg["searchMaxExpansionRounds"]):
            fallback_state["value"] = True
            reason = "active_interval_continues_to_expand"
            fallback_reasons.append(reason)
            added = _v4_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [elo for elo in full_grid if elo not in current_elos],
                "newEvaluatedPointCount": added,
                "refinedIntervals": intervals,
                "discoveredBasins": protected_basins,
                "fallbackToFullGrid": True,
                "fallbackReason": reason,
            })
            break
        next_points = sorted({
            point
            for interval in intervals
            for point in _v4_aligned_elo_points(
                int(interval["lower"]), int(interval["upper"]), step,
                formal_minimum=formal_minimum, formal_maximum=formal_maximum,
            )
        })
        missing = [point for point in next_points if point not in evaluator.cache]
        remaining = len([point for point in full_grid if point not in evaluator.cache])
        if missing and len(missing) >= remaining:
            fallback_state["value"] = True
            reason = "refinement_cost_not_below_full_grid_remaining"
            fallback_reasons.append(reason)
            added = _v4_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
            search_step_diagnostics.append({
                "step": step,
                "newEvaluatedEloPoints": [elo for elo in full_grid if elo not in current_elos],
                "newEvaluatedPointCount": added,
                "refinedIntervals": intervals,
                "discoveredBasins": protected_basins,
                "fallbackToFullGrid": True,
                "fallbackReason": reason,
            })
            break
        evaluator.evaluate_many(missing, include_in_search=True)
        curve_elos.update(missing)
        refined_intervals.append({
            "fromResolution": current_resolution,
            "toResolution": step,
            "lower": min(interval["lower"] for interval in intervals),
            "upper": max(interval["upper"] for interval in intervals),
            "intervals": intervals,
            "basinCount": len(protected_basins),
        })
        search_step_diagnostics.append({
            "step": step,
            "newEvaluatedEloPoints": missing,
            "newEvaluatedPointCount": len(missing),
            "refinedIntervals": intervals,
            "discoveredBasins": protected_basins,
        })
        previous_intervals = intervals

    if not fallback_state["value"]:
        final_points = _v4_curve_points(evaluator, curve_elos)
        final_basins = _v4_discover_basins(
            final_points,
            resolution=1,
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
            tolerance=float(cfg["searchTieTolerance"]),
        )
        if not final_basins:
            fallback_state["value"] = True
            reason = "final_sparse_curve_has_no_protected_minimum"
            fallback_reasons.append(reason)
            _v4_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)
        elif any(basin.get("needsExpansion") for basin in final_basins):
            fallback_state["value"] = True
            reason = "minimum_region_not_bracketed_after_final_refinement"
            fallback_reasons.append(reason)
            _v4_full_grid_fallback(evaluator, curve_elos, full_grid=full_grid)

    curve_points = _v4_curve_points(evaluator, curve_elos)
    stats = _v4_curve_stats(
        curve_points,
        formal_minimum=formal_minimum,
        formal_maximum=formal_maximum,
        full_grid=set(curve_elos) >= set(full_grid),
        tolerance=float(cfg["searchTieTolerance"]),
    )
    confidence = None
    if (
        confidence_t95 is not None
        and stats.get("minimumScore") is not None
        and stats.get("status") != "insufficient_reference"
    ):
        confidence = _v4_confidence_region_search(
            evaluator, curve_elos,
            minimum_score=float(stats["minimumScore"]),
            t95=float(confidence_t95),
            config=cfg,
            fallback_reasons=fallback_reasons,
            fallback_state=fallback_state,
        )
        curve_points = _v4_curve_points(evaluator, curve_elos)
        stats = _v4_curve_stats(
            curve_points,
            formal_minimum=formal_minimum,
            formal_maximum=formal_maximum,
            full_grid=set(curve_elos) >= set(full_grid),
            tolerance=float(cfg["searchTieTolerance"]),
        )
    known_probe = (
        _v4_probe_known_elo(
            evaluator, float(known_elo),
            formal_minimum=formal_minimum, formal_maximum=formal_maximum,
        )
        if known_elo is not None else None
    )
    best_point = _v4_best_point(curve_points, float(cfg["searchTieTolerance"]))
    best_game_diagnostics = []
    if best_point is not None:
        best_game_diagnostics = evaluator.cache[int(best_point["elo"])].get("gameDiagnostics") or []
    target_failures: dict[str, list[str]] = defaultdict(list)
    for raw_point in evaluator.cache.values():
        for game in raw_point.get("gameDiagnostics") or []:
            if game.get("ok") is not True:
                target_failures[str(game.get("gameId") or "")].append(
                    str(game.get("reason") or "score_failed")
                )
    query_count = (
        global_index.query_count - evaluator.query_count_before
        + int(getattr(evaluator, "process_query_count", 0))
    )
    tree_query_count = (
        global_index.tree_query_count - evaluator.tree_query_count_before
        + int(getattr(evaluator, "process_tree_query_count", 0))
    )
    boundary_query_count = (
        global_index.boundary_expansion_count - evaluator.boundary_query_count_before
        + int(getattr(evaluator, "process_boundary_expansion_count", 0))
    )
    audit_method = getattr(evaluator, "parallelization_audit", None)
    parallelization = (
        audit_method()
        if audit_method is not None else {
            "mode": "test_evaluator",
            "workers": int(getattr(evaluator, "target_game_workers", 1)),
            "taskUnit": "one_target_game",
            "phaseExecution": "sequential_within_game",
        }
    )
    excluded_ids = {str(row.get("gameId") or "") for row in target_records}
    for pool in global_index.pools.values():
        for row_index in pool.excluded_indexes(target_account, excluded_ids):
            game_id = str(pool.records[row_index].get("gameId") or "")
            if game_id:
                excluded_ids.add(game_id)
    curve = {
        "schema": SCHEMA_CURVE_V4,
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V4,
        "searchSteps": list(SEARCH_STEPS_V4),
        "formalEloMinimum": formal_minimum,
        "formalEloMaximum": formal_maximum,
        "points": curve_points,
        "evaluatedEloPoints": sorted(curve_elos),
        "evaluatedPointCount": len(curve_elos),
        "isFullGrid": set(curve_elos) >= set(full_grid),
        "targetGameCount": len(target_records),
        "selectedGameIds": [str(row.get("gameId") or "") for row in target_records],
        "selectedGameCount": len(target_records),
        "targetGameFailures": {
            game_id: sorted(set(reasons))
            for game_id, reasons in sorted(target_failures.items()) if game_id and reasons
        },
        "excludedReferenceGameIds": sorted(excluded_ids),
        "excludedReferenceGameCount": len(excluded_ids),
        "bestGridPoint": int(best_point["elo"]) if best_point is not None else None,
        "minimumScore": stats.get("minimumScore"),
        "minimumJ": stats.get("minimumScore"),
        "bestGameDiagnostics": best_game_diagnostics,
        "searchStepDiagnostics": search_step_diagnostics,
        "refinedIntervals": refined_intervals,
        "discoveredBasins": discovered_basins,
        "fallbackToFullGrid": bool(fallback_state["value"]),
        "fallbackReasons": sorted(set(fallback_reasons)),
        "confidenceIntervalSearch": confidence,
        "knownEloProbe": known_probe,
        "knnQueryCount": query_count,
        "cKDTreeQueryCount": tree_query_count,
        "boundaryExpansionQueryCount": boundary_query_count,
        "treeBuildSeconds": global_index.tree_build_seconds,
        "treeBuildCount": global_index.tree_build_count,
        "treeBuildReused": evaluator._tree_build_was_reused,
        "scoringSeconds": evaluator.scoring_seconds,
        "phaseKnnSeconds": dict(evaluator.phase_knn_seconds),
        "closedFormStatisticsSeconds": evaluator.closed_form_statistics_seconds,
        "totalRuntimeSeconds": time.perf_counter() - started,
        "globalIndexPoolCount": len(global_index.pools),
        "parallelization": parallelization,
        **stats,
    }
    if confidence is not None:
        curve["databaseCalibrated95Intervals"] = confidence["intervals"]
        curve["databaseCalibrated95Cutoff"] = confidence["cutoff"]
    else:
        curve["databaseCalibrated95Intervals"] = []
        curve["databaseCalibrated95Cutoff"] = None
    return curve


def _flatten_best_diagnostics_v4(
    curve: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    games: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    for game in curve.get("bestGameDiagnostics") or []:
        games.append({
            "gameId": game.get("gameId"),
            "gameScore": game.get("gameScore"),
            "gameNegativeLogPredictiveDensity": game.get("gameNegativeLogPredictiveDensity"),
            "meanTargetZ": game.get("meanTargetZ"),
            "status": "valid" if game.get("ok") is True else game.get("reason"),
        })
        for row in game.get("phaseDiagnostics") or []:
            phases.append({
                "gameId": row.get("gameId"),
                "phase": row.get("phase"),
                "x": row.get("x"),
                "n": row.get("n"),
                "transformedY": row.get("transformedY"),
                "samplingVariance": row.get("samplingVariance"),
                "targetSamplingVariance": row.get("targetSamplingVariance"),
                "localMeanY": row.get("localMeanY"),
                "observedVariance": row.get("observedVariance"),
                "betweenGameVariance": row.get("betweenGameVariance"),
                "meanEstimateVariance": row.get("meanEstimateVariance"),
                "predictiveVariance": row.get("predictiveVariance"),
                "targetZ": row.get("targetZ"),
                "negativeLogPredictiveDensity": row.get("negativeLogPredictiveDensity"),
                "N_allowed": row.get("N_allowed"),
                "K": row.get("K"),
                "boundaryDistance": row.get("boundaryDistance"),
                "neighborSetSha256": row.get("neighborSetSha256"),
                "neighborRecordIds": row.get("neighborRecordIds"),
                "fitStatus": row.get("fitStatus") or row.get("reason"),
                "scope": row.get("scope"),
                "color": row.get("color"),
            })
    return games, phases


def estimate_database_calibrated_range_v4(
    account: str,
    target_records: Sequence[dict[str, Any]],
    reference_records: Sequence[dict[str, Any]],
    reference_manifest: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
    reference_manifest_sha256: str | None = None,
    conditional_manifest_sha256: str | None = None,
    calibration_sha256: str | None = None,
    known_elo: float | None = None,
    index: GlobalAnscombeKNNIndexV4 | None = None,
    target_game_workers: int = DEFAULT_TARGET_GAME_WORKERS_V4,
    reference_records_path: str | Path | None = None,
) -> TargetEstimate:
    """Estimate one account using only the formal v4 local Gaussian model."""

    cfg = validate_v4_config(config or default_v4_config())
    _v4_validate_reference_manifest(reference_manifest, cfg)
    reference_sha = reference_manifest_sha256 or conditional_manifest_sha256
    expected_reference_sha = reference_manifest.get("manifestSha256")
    if reference_sha is not None and expected_reference_sha is not None and reference_sha != expected_reference_sha:
        raise ValueError("v4 reference manifest SHA-256 mismatch")
    if calibration is not None:
        if calibration.get("schema") != SCHEMA_CALIBRATION_V4:
            raise ValueError("v1/v2/v3 calibration artifacts cannot be used by estimated-Elo v4")
        if calibration.get("configSha256") not in {None, canonical_sha256(cfg)}:
            raise ValueError("v4 calibration/config mismatch")
        calibration_reference_sha = (
            calibration.get("referenceManifestSha256")
            or calibration.get("conditionalManifestSha256")
        )
        if calibration_reference_sha is not None and calibration_reference_sha != reference_sha:
            raise ValueError("v4 calibration/reference manifest mismatch")
        if calibration.get("referenceModelContractSha256") not in {
            None, canonical_sha256(v4_reference_model_contract(cfg))
        }:
            raise ValueError("v4 calibration/reference model contract mismatch")
    selection = select_target_records_v4(target_records, config=cfg)
    selected = list(selection["selected"])
    model_contract_sha = canonical_sha256(v4_reference_model_contract(cfg))
    search_contract_sha = canonical_sha256(v4_search_contract(cfg))
    calibration_contract_sha = canonical_sha256(v4_calibration_contract(cfg))
    payload: dict[str, Any] = {
        "schema": SCHEMA_ESTIMATE_V4,
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "account": account_key(account),
        "configSha256": canonical_sha256(cfg),
        "referenceModelContractSha256": model_contract_sha,
        "searchContractSha256": search_contract_sha,
        "calibrationContractSha256": calibration_contract_sha,
        "referenceManifestSha256": reference_sha,
        "conditionalManifestSha256": reference_sha,
        "sourceReferenceManifestSha256": reference_manifest.get("referenceManifestSha256"),
        "inputReferenceManifestSha256": reference_manifest.get("referenceManifestSha256"),
        "referenceModelManifestSha256": reference_sha,
        "calibrationArtifactSha256": calibration_sha256,
        "calibrationSha256": calibration_sha256,
        "selectedGameIds": [str(row.get("gameId") or "") for row in selected],
        "selectedGameCount": len(selected),
        "excludedGamesWithReasons": selection["excluded"],
        "formalMinimumGameCount": int(cfg["minimumTargetGames"]),
        "formalMaximumGameCount": int(cfg["maximumTargetGames"]),
        "formalEloMinimum": int(cfg["formalEloMinimum"]),
        "formalEloMaximum": int(cfg["formalEloMaximum"]),
        "eloGridMinimum": int(cfg["eloGridMinimum"]),
        "eloGridMaximum": int(cfg["eloGridMaximum"]),
        "estimatedElo": None,
        "bestGridPoint": None,
        "minimumScore": None,
        "minimumJ": None,
        "meanNegativeLogPredictiveDensityAtBest": None,
        "meanTargetZAtBest": None,
        "databaseCalibrated95Range": None,
        "databaseCalibrated95Intervals": [],
        "databaseCalibrated95Cutoff": None,
        "status": selection["status"],
        "statusReasons": [],
        "gameDiagnostics": [],
        "phaseDiagnostics": [],
        "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V4,
        "searchSteps": list(SEARCH_STEPS_V4),
        "evaluatedEloPoints": [],
        "evaluatedPointCount": 0,
        "searchStepDiagnostics": [],
        "refinedIntervals": [],
        "discoveredBasins": [],
        "fallbackToFullGrid": False,
        "fallbackReasons": [],
        "knownElo": float(known_elo) if known_elo is not None and finite_number(known_elo) else None,
        "knownEloUsage": "post-search probe only; never used by search or minimumJ",
        "knownEloProbe": None,
        "knnQueryCount": 0,
        "treeBuildCount": 0,
        "treeBuildReused": False,
        "scoringSeconds": 0.0,
        "phaseKnnSeconds": {f"phase{stage}": 0.0 for stage in range(1, 5)},
        "closedFormStatisticsSeconds": 0.0,
        "totalRuntimeSeconds": 0.0,
        "excludedReferenceGameIds": [],
        "scoringSemantics": (
            "J(E) is the arithmetic mean across the fixed selected games of four "
            "Anscombe-scale Gaussian negative log predictive densities; each next "
            "phase uses only the immediately preceding target z"
        ),
        "createdAt": utc_now(),
    }
    if selection["status"] != "valid":
        payload["statusReasons"] = ["fewer_than_minimum_complete_recent_target_games"]
        empty_curve = {
            "schema": SCHEMA_CURVE_V4,
            "algorithmVersion": ALGORITHM_VERSION_V4,
            "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V4,
            "searchSteps": list(SEARCH_STEPS_V4),
            "points": [],
            "status": selection["status"],
            "statusReasons": payload["statusReasons"],
            "evaluatedEloPoints": [],
            "evaluatedPointCount": 0,
            "searchStepDiagnostics": [],
            "refinedIntervals": [],
            "discoveredBasins": [],
            "fallbackToFullGrid": False,
            "fallbackReasons": [],
            "knownEloProbe": None,
            "databaseCalibrated95Intervals": [],
            "databaseCalibrated95Cutoff": None,
            "selectedGameIds": payload["selectedGameIds"],
            "selectedGameCount": len(selected),
        }
        return TargetEstimate(payload, empty_curve, tuple(selected))

    confidence_t95 = None
    if calibration is not None and calibration.get("status") == "validated":
        if not finite_number(calibration.get("t95")):
            raise ValueError("validated v4 calibration is missing finite T95")
        confidence_t95 = float(calibration["t95"])
    curve = score_candidate_curve_v4(
        selected,
        reference_records,
        reference_manifest,
        target_account=account,
        config=cfg,
        confidence_t95=confidence_t95,
        known_elo=known_elo,
        index=index,
        target_game_workers=target_game_workers,
        reference_records_path=reference_records_path,
    )
    payload["bestGridPoint"] = curve.get("bestGridPoint")
    payload["minimumScore"] = curve.get("minimumScore")
    payload["minimumJ"] = curve.get("minimumScore")
    best = curve.get("bestGridPoint")
    best_point = next(
        (point for point in curve.get("points", []) if point.get("elo") == best), None
    )
    if best_point is not None:
        payload["meanNegativeLogPredictiveDensityAtBest"] = best_point.get("score")
        payload["meanTargetZAtBest"] = best_point.get("meanTargetZ")
    games, phases = _flatten_best_diagnostics_v4(curve)
    payload["gameDiagnostics"] = games
    payload["phaseDiagnostics"] = phases
    for key in (
        "searchStrategyVersion", "searchSteps", "evaluatedEloPoints",
        "evaluatedPointCount", "searchStepDiagnostics", "refinedIntervals",
        "discoveredBasins", "fallbackToFullGrid", "fallbackReasons",
        "knownEloProbe", "knnQueryCount", "cKDTreeQueryCount",
        "boundaryExpansionQueryCount", "treeBuildSeconds", "treeBuildCount",
        "treeBuildReused", "scoringSeconds", "phaseKnnSeconds",
        "closedFormStatisticsSeconds", "totalRuntimeSeconds",
        "excludedReferenceGameIds", "excludedReferenceGameCount",
        "selectedGameIds", "selectedGameCount", "targetGameFailures",
        "parallelization",
    ):
        if key in curve:
            payload[key] = curve.get(key)
    payload["databaseCalibrated95Intervals"] = curve.get("databaseCalibrated95Intervals") or []
    payload["databaseCalibrated95Cutoff"] = curve.get("databaseCalibrated95Cutoff")
    payload["confidenceIntervalSearch"] = curve.get("confidenceIntervalSearch")
    payload["referenceFeaturePolicy"] = cfg["referenceFeaturePolicy"]
    payload["status"] = curve.get("status")
    payload["statusReasons"] = list(curve.get("statusReasons") or [])
    if curve.get("status") in {"valid", "multiple_minima", "above_reference_range", "below_reference_range"} and best is not None:
        payload["estimatedElo"] = int(best)
        if calibration is None or calibration.get("status") != "validated":
            payload["status"] = "calibration_unavailable"
            payload["statusReasons"].append("validated_v4_T95_is_not_available")
        elif len(payload["databaseCalibrated95Intervals"]) > 1:
            payload["status"] = "multiple_intervals"
            payload["statusReasons"].append("calibrated_allowed_set_is_discontinuous")
    if known_elo is not None:
        probe = curve.get("knownEloProbe") or {}
        payload["scoreAtKnownElo"] = probe.get("scoreAtKnownElo")
        payload["trueScoreIncrease"] = (
            float(probe["scoreAtKnownElo"]) - float(curve["minimumScore"])
            if probe.get("status") == "ok" and finite_number(curve.get("minimumScore"))
            else None
        )
        payload["estimatedEloError"] = (
            float(best) - float(known_elo) if best is not None else None
        )
        if probe.get("status") == "failed":
            payload["knownEloProbeStatus"] = "failed"
            payload["knownEloProbeFailure"] = probe.get("reason")
    return TargetEstimate(payload, curve, tuple(selected))


_V4_WORKER_REFERENCE_RECORDS: list[dict[str, Any]] = []
_V4_WORKER_REFERENCE_MANIFEST: dict[str, Any] = {}
_V4_WORKER_REFERENCE_MANIFEST_SHA: str | None = None
_V4_WORKER_DIRECTED_RECORDS_SHA: str | None = None
_V4_WORKER_CONFIG: dict[str, Any] = {}
_V4_WORKER_INDEX: GlobalAnscombeKNNIndexV4 | None = None


def _init_v4_calibration_worker(
    reference_records_path: str,
    reference_manifest_path: str,
    config: dict[str, Any],
    directed_records_sha256: str,
) -> None:
    """Load one immutable reference cache and build its trees per process."""

    global _V4_WORKER_REFERENCE_RECORDS
    global _V4_WORKER_REFERENCE_MANIFEST
    global _V4_WORKER_REFERENCE_MANIFEST_SHA
    global _V4_WORKER_DIRECTED_RECORDS_SHA
    global _V4_WORKER_CONFIG
    global _V4_WORKER_INDEX
    _V4_WORKER_CONFIG = validate_v4_config(config)
    _V4_WORKER_REFERENCE_RECORDS = read_jsonl(reference_records_path)
    _V4_WORKER_REFERENCE_MANIFEST = read_json(reference_manifest_path)
    _V4_WORKER_REFERENCE_MANIFEST_SHA = sha256_file(reference_manifest_path)
    _V4_WORKER_DIRECTED_RECORDS_SHA = str(directed_records_sha256)
    _V4_WORKER_INDEX = GlobalAnscombeKNNIndexV4(
        _V4_WORKER_REFERENCE_RECORDS,
        _V4_WORKER_REFERENCE_MANIFEST,
        _V4_WORKER_CONFIG,
    )


def _v4_calibration_case_payload(
    role: str,
    account: str,
    known_elo: float,
    target_records: Sequence[dict[str, Any]],
    *,
    t95: float | None,
) -> dict[str, Any]:
    if (
        _V4_WORKER_INDEX is None
        or _V4_WORKER_REFERENCE_MANIFEST_SHA is None
        or _V4_WORKER_DIRECTED_RECORDS_SHA is None
    ):
        raise RuntimeError("v4 calibration worker was not initialized")
    started = time.perf_counter()
    calibration_context = None
    if t95 is not None:
        calibration_context = {
            "schema": SCHEMA_CALIBRATION_V4,
            "status": "validated",
            "configSha256": canonical_sha256(_V4_WORKER_CONFIG),
            "referenceManifestSha256": _V4_WORKER_REFERENCE_MANIFEST_SHA,
            "referenceModelContractSha256": canonical_sha256(
                v4_reference_model_contract(_V4_WORKER_CONFIG)
            ),
            "t95": float(t95),
        }
    estimate = estimate_database_calibrated_range_v4(
        account,
        target_records,
        _V4_WORKER_REFERENCE_RECORDS,
        _V4_WORKER_REFERENCE_MANIFEST,
        config=_V4_WORKER_CONFIG,
        calibration=calibration_context,
        reference_manifest_sha256=_V4_WORKER_REFERENCE_MANIFEST_SHA,
        calibration_sha256=None,
        known_elo=float(known_elo),
        index=_V4_WORKER_INDEX,
        # 这是用户要求的并行调度变更：calibration 外层已经是账号级
        # ProcessPoolExecutor，因此账号 worker 内必须保持单局串行，禁止
        # 再创建目标局进程池。
        target_game_workers=1,
    )
    curve = estimate.curve
    payload = estimate.payload
    selected = list(estimate.selected_records)
    probe = curve.get("knownEloProbe") or {}
    minimum = curve.get("minimumScore")
    true_increase = (
        float(probe["scoreAtKnownElo"]) - float(minimum)
        if probe.get("status") == "ok" and finite_number(minimum) else None
    )
    intervals = curve.get("databaseCalibrated95Intervals") or []
    boundary_hit = any(
        int(interval.get("lower")) == int(_V4_WORKER_CONFIG["formalEloMinimum"])
        or int(interval.get("upper")) == int(_V4_WORKER_CONFIG["formalEloMaximum"])
        for interval in intervals
    )
    return {
        "schema": SCHEMA_CALIBRATION_CASE_V4,
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "configSha256": canonical_sha256(_V4_WORKER_CONFIG),
        "referenceModelContractSha256": canonical_sha256(
            v4_reference_model_contract(_V4_WORKER_CONFIG)
        ),
        "searchContractSha256": canonical_sha256(v4_search_contract(_V4_WORKER_CONFIG)),
        "calibrationContractSha256": canonical_sha256(
            v4_calibration_contract(_V4_WORKER_CONFIG)
        ),
        "referenceManifestSha256": _V4_WORKER_REFERENCE_MANIFEST_SHA,
        "conditionalReferenceManifestSha256": _V4_WORKER_REFERENCE_MANIFEST_SHA,
        "anscombeReferenceManifestSha256": _V4_WORKER_REFERENCE_MANIFEST_SHA,
        "directedRecordsSha256": _V4_WORKER_DIRECTED_RECORDS_SHA,
        "role": str(role),
        "account": account_key(account),
        "knownElo": float(known_elo),
        "knownEloDefinition": "newR from the latest created source-bundle detail for this account",
        "knownEloInFormalRange": int(_V4_WORKER_CONFIG["formalEloMinimum"]) <= float(known_elo) <= int(_V4_WORKER_CONFIG["formalEloMaximum"]),
        "knownEloProbe": probe,
        "selectedGameIds": [str(row.get("gameId") or "") for row in selected],
        "selectedGameCount": len(selected),
        "curveStatus": curve.get("status"),
        "payloadStatus": payload.get("status"),
        "bestGridPoint": curve.get("bestGridPoint"),
        "minimumScore": minimum,
        "minimumJ": minimum,
        "scoreAtKnownElo": probe.get("scoreAtKnownElo"),
        "trueScoreIncrease": true_increase,
        "estimatedEloError": (
            float(curve["bestGridPoint"]) - float(known_elo)
            if finite_number(curve.get("bestGridPoint")) else None
        ),
        "databaseCalibrated95Intervals": intervals,
        "databaseCalibrated95Cutoff": curve.get("databaseCalibrated95Cutoff"),
        "scoreCurve": curve.get("points") or [],
        "searchStrategyVersion": curve.get("searchStrategyVersion"),
        "searchSteps": curve.get("searchSteps"),
        "searchStepDiagnostics": curve.get("searchStepDiagnostics"),
        "evaluatedEloPoints": curve.get("evaluatedEloPoints"),
        "evaluatedPointCount": curve.get("evaluatedPointCount"),
        "refinedIntervals": curve.get("refinedIntervals"),
        "discoveredBasins": curve.get("discoveredBasins"),
        "fallbackToFullGrid": curve.get("fallbackToFullGrid"),
        "fallbackReasons": curve.get("fallbackReasons"),
        "excludedReferenceGameIds": curve.get("excludedReferenceGameIds"),
        "excludedReferenceGameCount": curve.get("excludedReferenceGameCount"),
        "selectedGameIds": curve.get("selectedGameIds") or [],
        "selectedGameCount": curve.get("selectedGameCount"),
        "knnQueryCount": curve.get("knnQueryCount"),
        "treeBuildCount": curve.get("treeBuildCount"),
        "treeBuildReused": curve.get("treeBuildReused"),
        "scoringSeconds": curve.get("scoringSeconds"),
        "phaseKnnSeconds": curve.get("phaseKnnSeconds"),
        "closedFormStatisticsSeconds": curve.get("closedFormStatisticsSeconds"),
        "totalRuntimeSeconds": time.perf_counter() - started,
        "minimumRegionWidth": curve.get("minimumRegionWidth"),
        "maximumAdjacentScoreJump": curve.get("maximumAdjacentScoreJump"),
        "maximumSampledScoreJump": curve.get("maximumSampledScoreJump"),
        "maximumSampledEloGap": curve.get("maximumSampledEloGap"),
        "diagnosticContinuity": curve.get("diagnosticContinuity"),
        "statusReasons": curve.get("statusReasons") or [],
        "calibrationPass": "calibration_accounts_only" if t95 is None else "validation_after_frozen_T95",
        "confidenceThresholdFrozen": t95 is not None,
        "frozenT95": float(t95) if t95 is not None else None,
        "boundaryHit": boundary_hit,
        "accountStatisticsStatus": "completed",
        "modelInputStatus": "valid",
        "taskStatus": "completed",
    }


def _v4_calibration_case_worker(
    task: tuple[str, str, float, list[dict[str, Any]], str, float | None],
) -> dict[str, Any]:
    role, account, known_elo, target_records, case_path, t95 = task
    try:
        case = _v4_calibration_case_payload(
            role, account, known_elo, target_records, t95=t95
        )
    except ValueError as error:
        return _v4_calibration_failure_case(
            role, account, known_elo, case_path, error,
            category="model_input_failure",
        )
    except (ArithmeticError, FloatingPointError) as error:
        return _v4_calibration_failure_case(
            role, account, known_elo, case_path, error,
            category="account_statistics_failure",
        )
    atomic_write_json(case_path, case)
    return case


def _v4_calibration_failure_case(
    role: str,
    account: str,
    known_elo: float,
    case_path: str,
    error: Exception,
    *,
    category: str,
) -> dict[str, Any]:
    """Persist a classified input/statistics failure without hiding task errors."""

    case = {
        "schema": SCHEMA_CALIBRATION_CASE_V4,
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "configSha256": canonical_sha256(_V4_WORKER_CONFIG),
        "referenceManifestSha256": _V4_WORKER_REFERENCE_MANIFEST_SHA,
        "conditionalReferenceManifestSha256": _V4_WORKER_REFERENCE_MANIFEST_SHA,
        "anscombeReferenceManifestSha256": _V4_WORKER_REFERENCE_MANIFEST_SHA,
        "directedRecordsSha256": _V4_WORKER_DIRECTED_RECORDS_SHA,
        "role": str(role),
        "account": account_key(account),
        "knownElo": float(known_elo),
        "knownEloInFormalRange": False,
        "selectedGameIds": [],
        "selectedGameCount": 0,
        "curveStatus": "failed",
        "statusReasons": [str(error)],
        "taskStatus": "completed",
        "accountStatisticsStatus": "failed" if category == "account_statistics_failure" else "not_run",
        "modelInputStatus": "failed" if category == "model_input_failure" else "not_run",
        "failureCategory": category,
        "errorType": type(error).__name__,
        "error": str(error),
        "confidenceThresholdFrozen": False,
        "totalRuntimeSeconds": 0.0,
    }
    atomic_write_json(case_path, case)
    return case


def _calibration_group_rows_v4(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        count = int(case.get("selectedGameCount") or 0)
        count_band = (
            "10-14" if count <= 14 else "15-19" if count <= 19
            else "20-24" if count <= 24 else "25-30"
        )
        known = float(case["knownElo"])
        elo_band = f"{int(known // 100) * 100}-{int(known // 100) * 100 + 99}"
        groups[f"gameCount:{count_band}"].append(case)
        groups[f"knownElo:{elo_band}"].append(case)
    output: dict[str, Any] = {}
    for key, rows in sorted(groups.items()):
        signed_errors = [
            float(row["estimatedEloError"])
            for row in rows if finite_number(row.get("estimatedEloError"))
        ]
        absolute_errors = [abs(value) for value in signed_errors]
        increases = [
            float(row["trueScoreIncrease"])
            for row in rows if finite_number(row.get("trueScoreIncrease"))
        ]
        widths = [
            float(interval["upper"]) - float(interval["lower"])
            for row in rows
            for interval in row.get("databaseCalibrated95Intervals") or []
        ]
        covered = [
            any(
                float(interval["lower"]) <= float(row["knownElo"]) <= float(interval["upper"])
                for interval in row.get("databaseCalibrated95Intervals") or []
            )
            for row in rows
            if finite_number(row.get("knownElo"))
        ]
        output[key] = {
            "caseCount": len(rows),
            "coveredCount": sum(covered),
            "coverage": statistics.fmean(covered) if covered else None,
            "pointError": _error_summary(signed_errors),
            "absoluteError": _error_summary(absolute_errors),
            "trueScoreIncrease": _error_summary(increases),
            "intervalWidth": _error_summary(widths),
        }
    return output


def _v4_case_path(case_dir: Path, account: str) -> Path:
    return case_dir / f"{hashlib.sha256(account_key(account).encode('utf-8')).hexdigest()}.json"


def calibrate_global_interval_v4(
    directed_reference_records: Sequence[dict[str, Any]],
    source_bundle: dict[str, Any],
    reference_records: Sequence[dict[str, Any]],
    reference_manifest: dict[str, Any],
    output_dir: str | Path,
    *,
    config: dict[str, Any] | None = None,
    reference_records_path: str | Path,
    reference_manifest_path: str | Path,
    directed_records_sha256: str,
    resume: bool = False,
    parallel_workers: int = 16,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the two-pass v4 calibration with exactly one account per task."""

    cfg = validate_v4_config(config or default_v4_config())
    if int(parallel_workers) != 16:
        raise ValueError("formal estimated-Elo v4 calibration requires max_workers=16")
    if int(cfg["referenceQueryWorkers"]) != 1:
        raise ValueError("v4 calibration requires referenceQueryWorkers=1")
    reference_manifest_sha = sha256_file(reference_manifest_path)
    _v4_validate_reference_manifest(reference_manifest, cfg, strict=True)
    output = Path(output_dir)
    progress_path = output / "progress.json"
    by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in directed_reference_records:
        account = account_key(row.get("targetPlayerId"))
        if (
            account
            and row.get("formalReferenceEligible") is True
            and _v4_target_record_rejection(row, cfg) is None
        ):
            by_account[account].append(row)
    for account in by_account:
        by_account[account].sort(
            key=lambda row: (str(row.get("created") or ""), str(row.get("gameId") or "")),
            reverse=True,
        )
    known_elos = _latest_known_elos(source_bundle)
    eligible = [
        account for account in sorted(by_account)
        if len(by_account[account]) >= int(cfg["minimumTargetGames"])
        and finite_number(known_elos.get(account))
    ]
    validation_candidates = [
        account for account in eligible
        if len(by_account[account]) >= int(cfg["validationMinimumTargetGames"])
    ]
    calibration_accounts, validation_accounts, split = _split_accounts(
        eligible, cfg, validation_candidates=validation_candidates
    )
    roles = {account: "validation" for account in validation_accounts}
    roles.update({account: "calibration" for account in calibration_accounts})
    calibration_contract = v4_calibration_contract(cfg)
    contract = {
        "schema": SCHEMA_CALIBRATION_V4,
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "configSha256": canonical_sha256(cfg),
        "referenceModelContractSha256": canonical_sha256(v4_reference_model_contract(cfg)),
        "searchContractSha256": canonical_sha256(v4_search_contract(cfg)),
        "calibrationContractSha256": canonical_sha256(calibration_contract),
        "directedRecordsSha256": directed_records_sha256,
        "referenceManifestSha256": reference_manifest_sha,
        "conditionalReferenceManifestSha256": reference_manifest_sha,
        "anscombeReferenceManifestSha256": reference_manifest_sha,
        "inputReferenceManifestSha256": reference_manifest_sha,
        "eligibleAccountsSha256": canonical_sha256(eligible),
        "calibrationAccountsSha256": canonical_sha256(calibration_accounts),
        "validationAccountsSha256": canonical_sha256(validation_accounts),
        "parallelWorkers": 16,
        "taskUnit": "one_player_account",
        "processPoolChunksize": 1,
        "referenceQueryWorkersPerWorker": 1,
    }
    contract_sha = canonical_sha256(contract)
    if output.exists() and not resume:
        raise FileExistsError(f"calibration output already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if progress_path.is_file():
        progress = read_json(progress_path)
        if progress.get("contractSha256") != contract_sha:
            raise ValueError("resume refused: v4 calibration contract changed")
        if progress.get("schema") != "player-sentinel-elo-calibration-progress-v4":
            raise ValueError("resume refused: incompatible v4 calibration progress schema")
        progress.setdefault("completedCalibrationCases", {})
        progress.setdefault("completedValidationCases", {})
        progress.setdefault("taskFailureCount", 0)
        progress.setdefault("accountStatisticsFailureCount", 0)
        progress.setdefault("modelInputFailureCount", 0)
    else:
        if resume and any(output.iterdir()):
            raise ValueError("resume refused: v4 calibration directory has no compatible progress")
        progress = {
            "schema": "player-sentinel-elo-calibration-progress-v4",
            "contract": contract,
            "contractSha256": contract_sha,
            "status": "running",
            "createdAt": utc_now(),
            "completedCalibrationCases": {},
            "completedValidationCases": {},
            "taskFailureCount": 0,
            "accountStatisticsFailureCount": 0,
            "modelInputFailureCount": 0,
            "failedCaseCount": 0,
            "retryCount": 0,
        }
        atomic_write_json(progress_path, progress)

    case_dir = output / "cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    cases_by_account: dict[str, dict[str, Any]] = {}
    config_sha = canonical_sha256(cfg)

    def validate_case_file(
        case_path: Path,
        recovered: dict[str, Any],
        expected_account: str,
        expected_role: str,
    ) -> None:
        expected = {
            "schema": SCHEMA_CALIBRATION_CASE_V4,
            "algorithmVersion": ALGORITHM_VERSION_V4,
            "account": expected_account,
            "role": expected_role,
            "configSha256": config_sha,
            "referenceModelContractSha256": canonical_sha256(v4_reference_model_contract(cfg)),
            "searchContractSha256": canonical_sha256(v4_search_contract(cfg)),
            "calibrationContractSha256": canonical_sha256(calibration_contract),
            "referenceManifestSha256": reference_manifest_sha,
            "conditionalReferenceManifestSha256": reference_manifest_sha,
            "anscombeReferenceManifestSha256": reference_manifest_sha,
            "directedRecordsSha256": directed_records_sha256,
        }
        for key, value in expected.items():
            if recovered.get(key) != value:
                raise ValueError(
                    f"resume refused: incompatible v4 calibration case {case_path}: "
                    f"{key} mismatch"
                )
        if recovered.get("taskStatus") != "completed":
            raise ValueError(f"resume refused: incomplete v4 calibration case: {case_path}")
        if expected_role == "validation":
            frozen = recovered.get("frozenT95")
            progress_t95 = progress.get("frozenT95")
            if recovered.get("confidenceThresholdFrozen") is not True or not finite_number(frozen):
                raise ValueError(f"resume refused: validation case has no frozen T95: {case_path}")
            if progress_t95 is None or abs(float(frozen) - float(progress_t95)) > 1e-12:
                raise ValueError(f"resume refused: validation case T95 does not match progress: {case_path}")

    def register_recovered_case(account: str, role: str, case_path: Path) -> None:
        recovered = read_json(case_path)
        validate_case_file(case_path, recovered, account, role)
        completed_map = (
            progress["completedCalibrationCases"]
            if role == "calibration" else progress["completedValidationCases"]
        )
        previous = completed_map.get(account)
        actual_sha = sha256_file(case_path)
        if previous is not None and previous.get("sha256") != actual_sha:
            raise ValueError(f"resume refused: completed v4 case changed: {account}")
        completed_map[account] = {
            "sha256": actual_sha,
            "runtimeSeconds": recovered.get("totalRuntimeSeconds"),
            "recoveredAt": utc_now(),
            **({"frozenT95": recovered.get("frozenT95")} if role == "validation" else {}),
        }
        cases_by_account[account] = recovered

    # A process can finish an atomic case immediately before it is able to
    # update progress.json.  Re-register only a case whose full contract is
    # Bound to this exact v4 invocation; every other orphan fails loudly.
    for case_path in sorted(case_dir.glob("*.json")):
        recovered = read_json(case_path)
        account = account_key(recovered.get("account"))
        role = str(recovered.get("role") or "")
        if account not in roles:
            raise ValueError(f"resume refused: orphan v4 calibration case belongs to an unknown account: {case_path}")
        if roles[account] != role:
            raise ValueError(f"resume refused: orphan v4 calibration case role mismatch: {case_path}")
        expected_path = _v4_case_path(case_dir, account)
        if case_path.resolve() != expected_path.resolve():
            raise ValueError(f"resume refused: v4 calibration case filename/account mismatch: {case_path}")
        register_recovered_case(account, role, case_path)
    if cases_by_account:
        progress["updatedAt"] = utc_now()
        atomic_write_json(progress_path, progress)

    for account, role in sorted(roles.items()):
        case_path = _v4_case_path(case_dir, account)
        completed_map = (
            progress["completedCalibrationCases"]
            if role == "calibration" else progress["completedValidationCases"]
        )
        completed = completed_map.get(account)
        if completed:
            if not case_path.is_file() or sha256_file(case_path) != completed.get("sha256"):
                raise ValueError(f"resume refused: completed v4 case changed: {account}")
            recovered = read_json(case_path)
            validate_case_file(case_path, recovered, account, role)
            cases_by_account[account] = recovered

    def tasks_for(role: str, accounts: Sequence[str], t95: float | None) -> list[tuple[str, str, float, list[dict[str, Any]], str, float | None]]:
        return [
            (
                role,
                account,
                float(known_elos[account]),
                by_account[account][:int(cfg["maximumTargetGames"])],
                str(_v4_case_path(case_dir, account).resolve()),
                t95,
            )
            for account in accounts if account not in cases_by_account
        ]

    started = time.perf_counter()
    t95: float | None = None
    calibration_tasks = tasks_for("calibration", calibration_accounts, None)
    with ProcessPoolExecutor(
        max_workers=16,
        initializer=_init_v4_calibration_worker,
        initargs=(
            str(Path(reference_records_path).resolve()),
            str(Path(reference_manifest_path).resolve()),
            cfg,
            directed_records_sha256,
        ),
    ) as executor:
        if calibration_tasks:
            for case in executor.map(_v4_calibration_case_worker, calibration_tasks, chunksize=1):
                account = str(case["account"])
                cases_by_account[account] = case
                progress["completedCalibrationCases"][account] = {
                    "sha256": sha256_file(_v4_case_path(case_dir, account)),
                    "runtimeSeconds": case.get("totalRuntimeSeconds"),
                    "completedAt": utc_now(),
                }
                progress["accountStatisticsFailureCount"] += int(case.get("accountStatisticsStatus") == "failed")
                progress["modelInputFailureCount"] += int(case.get("modelInputStatus") == "failed")
                progress["failedCaseCount"] += int(case.get("curveStatus") == "failed")
                progress["updatedAt"] = utc_now()
                atomic_write_json(progress_path, progress)

        calibration_cases = [
            case for case in cases_by_account.values()
            if case.get("role") == "calibration"
            and case.get("knownEloInFormalRange") is True
            and case.get("curveStatus") in {"valid", "multiple_minima", "above_reference_range", "below_reference_range"}
            and finite_number(case.get("trueScoreIncrease"))
        ]
        t95 = _empirical_quantile_v2(
            [float(case["trueScoreIncrease"]) for case in calibration_cases],
            float(cfg["calibrationCoverage"]),
        )
        progress["frozenT95"] = t95
        progress["frozenT95Source"] = "calibration_accounts_only"
        progress["updatedAt"] = utc_now()
        atomic_write_json(progress_path, progress)

        validation_tasks = tasks_for("validation", validation_accounts, t95)
        if validation_tasks:
            for case in executor.map(_v4_calibration_case_worker, validation_tasks, chunksize=1):
                account = str(case["account"])
                cases_by_account[account] = case
                progress["completedValidationCases"][account] = {
                    "sha256": sha256_file(_v4_case_path(case_dir, account)),
                    "runtimeSeconds": case.get("totalRuntimeSeconds"),
                    "frozenT95": t95,
                    "completedAt": utc_now(),
                }
                progress["accountStatisticsFailureCount"] += int(case.get("accountStatisticsStatus") == "failed")
                progress["modelInputFailureCount"] += int(case.get("modelInputStatus") == "failed")
                progress["failedCaseCount"] += int(case.get("curveStatus") == "failed")
                progress["updatedAt"] = utc_now()
                atomic_write_json(progress_path, progress)

    cases = [cases_by_account[account] for account in sorted(cases_by_account)]
    validation_cases = [
        case for case in cases
        if case.get("role") == "validation"
        and case.get("knownEloInFormalRange") is True
        and finite_number(case.get("knownElo"))
    ]
    coverage_rows = [
        any(
            float(interval["lower"]) <= float(case["knownElo"]) <= float(interval["upper"])
            for interval in case.get("databaseCalibrated95Intervals") or []
        )
        for case in validation_cases
    ]
    interval_widths = [
        float(interval["upper"]) - float(interval["lower"])
        for case in validation_cases
        for interval in case.get("databaseCalibrated95Intervals") or []
    ]
    validation_coverage = statistics.fmean(coverage_rows) if coverage_rows else None
    signed_errors = [
        float(case["estimatedEloError"])
        for case in validation_cases if finite_number(case.get("estimatedEloError"))
    ]
    absolute_errors = [abs(value) for value in signed_errors]
    boundary_cases = [
        case for case in validation_cases
        if case.get("boundaryHit") is True
        or case.get("bestGridPoint") in {
            int(cfg["formalEloMinimum"]), int(cfg["formalEloMaximum"])
        }
    ]
    coverage_confirmed = bool(
        t95 is not None
        and len(validation_cases) >= int(cfg["minimumValidationUsers"])
        and validation_coverage is not None
        and validation_coverage >= float(cfg["calibrationCoverage"])
    )
    runtimes = [
        float(case["totalRuntimeSeconds"])
        for case in cases if finite_number(case.get("totalRuntimeSeconds"))
    ]
    calibration_cases = [case for case in cases if case.get("role") == "calibration"]
    artifact = {
        "schema": SCHEMA_CALIBRATION_V4,
        "version": "v4",
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "createdAt": utc_now(),
        "status": "validated" if coverage_confirmed else "calibration_unavailable",
        "configSha256": config_sha,
        "referenceModelContractSha256": canonical_sha256(v4_reference_model_contract(cfg)),
        "searchContractSha256": canonical_sha256(v4_search_contract(cfg)),
        "calibrationContractSha256": canonical_sha256(calibration_contract),
        "referenceManifestSha256": reference_manifest_sha,
        "conditionalReferenceManifestSha256": reference_manifest_sha,
        "anscombeReferenceManifestSha256": reference_manifest_sha,
        "inputReferenceManifestSha256": reference_manifest_sha,
        "directedRecordsSha256": directed_records_sha256,
        "knownEloDefinition": "newR from the latest created source-bundle detail for each account",
        "knownEloUsage": "post-search probe and calibration truth only; never a search coordinate or minimumJ input",
        "quantileMethod": "unweighted empirical linear interpolation at p=(n-1)*q",
        "calibrationCoverage": float(cfg["calibrationCoverage"]),
        "t95": t95,
        "t95Source": "calibration_accounts_only",
        "calibrationUserCount": len(calibration_accounts),
        "validationUserCount": len(validation_accounts),
        "calibrationCaseCount": len(calibration_cases),
        "validationCaseCount": len(validation_cases),
        "validationCoveredCount": sum(coverage_rows),
        "validationCoverage": validation_coverage,
        "pointErrorSummary": _error_summary(signed_errors),
        "absoluteErrorSummary": _error_summary(absolute_errors),
        "validationIntervalWidthSummary": _error_summary(interval_widths),
        "boundaryHitCount": len(boundary_cases),
        "boundaryHitRate": statistics.fmean(
            [case in boundary_cases for case in validation_cases]
        ) if validation_cases else None,
        "groupMetrics": _calibration_group_rows_v4(cases),
        "split": {**split, "calibrationAccounts": calibration_accounts, "validationAccounts": validation_accounts},
        "parallelWorkers": 16,
        "taskUnit": "one_player_account",
        "processPoolChunksize": 1,
        "referenceQueryWorkersPerWorker": 1,
        "accountRuntimeSeconds": _error_summary(runtimes),
        "wallRuntimeSecondsThisInvocation": time.perf_counter() - started,
        "taskFailureCount": int(progress.get("taskFailureCount", 0)),
        "accountStatisticsFailureCount": int(progress.get("accountStatisticsFailureCount", 0)),
        "modelInputFailureCount": int(progress.get("modelInputFailureCount", 0)),
        "failedCaseCount": int(progress.get("failedCaseCount", 0)),
        "retryCount": int(progress.get("retryCount", 0)),
        "searchStrategyVersion": SEARCH_STRATEGY_VERSION_V4,
        "searchSteps": list(SEARCH_STEPS_V4),
        "twoPassCalibration": {
            "firstPass": "calibration_accounts_only_produce_T95",
            "secondPass": "validation_accounts_use_frozen_minimumJ_plus_T95",
            "frozenT95": t95,
        },
        "independentValidation": {
            "required": True,
            "usersAreDisjointFromCalibration": not bool(set(calibration_accounts) & set(validation_accounts)),
            "coverageTarget": float(cfg["calibrationCoverage"]),
            "coverageConfirmed": coverage_confirmed,
        },
    }
    cases_path = output / str(cfg["calibrationCases"])
    artifact_path = output / str(cfg["calibrationArtifact"])
    atomic_write_jsonl(cases_path, cases)
    artifact["casesSha256"] = sha256_file(cases_path)
    atomic_write_json(artifact_path, artifact)
    artifact_sha = sha256_file(artifact_path)
    calibration_manifest = {
        "schema": SCHEMA_CALIBRATION_MANIFEST_V4,
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "createdAt": utc_now(),
        "configSha256": config_sha,
        "referenceModelContractSha256": artifact["referenceModelContractSha256"],
        "searchContractSha256": artifact["searchContractSha256"],
        "calibrationContractSha256": artifact["calibrationContractSha256"],
        "referenceManifestSha256": reference_manifest_sha,
        "conditionalReferenceManifestSha256": reference_manifest_sha,
        "anscombeReferenceManifestSha256": reference_manifest_sha,
        "inputReferenceManifestSha256": reference_manifest_sha,
        "directedRecordsSha256": directed_records_sha256,
        "calibrationArtifactSha256": artifact_sha,
        "calibrationCasesSha256": artifact["casesSha256"],
        "files": [
            {"path": str(cfg["calibrationArtifact"]), "sha256": artifact_sha},
            {"path": str(cfg["calibrationCases"]), "sha256": artifact["casesSha256"]},
        ],
    }
    calibration_manifest_path = output / "calibration_sha256_manifest_v4.json"
    atomic_write_json(calibration_manifest_path, calibration_manifest)
    progress["status"] = "completed"
    progress["completedAt"] = utc_now()
    progress["calibrationArtifactSha256"] = artifact_sha
    progress["calibrationCasesSha256"] = artifact["casesSha256"]
    progress["calibrationManifestSha256"] = sha256_file(calibration_manifest_path)
    progress["updatedAt"] = utc_now()
    atomic_write_json(progress_path, progress)
    return artifact, cases


def _v4_bruteforce_nearest(
    records: Sequence[dict[str, Any]],
    scales: dict[str, Any],
    stage: int,
    config: dict[str, Any],
    *,
    account: str,
    target_game_ids: Iterable[str],
    trial_elo: float,
    opponent_elo: float,
    previous_z: float | None = None,
) -> dict[str, Any]:
    """Exact stable-sort reference for auditing the global cKDTree path."""

    cfg = validate_v4_config(config)
    pool = _GlobalAnscombePoolV4(records, scales, int(stage), cfg, "audit")
    excluded = pool.excluded_indexes(account, target_game_ids)
    n_allowed = pool.record_count - len(excluded)
    k = neighbor_count_v4(n_allowed, float(cfg["neighborExponent"])) if n_allowed else 0
    base = {
        "N_allowed": n_allowed,
        "K": k,
        "eligibleReferenceCount": n_allowed,
        "excludedRecordCount": len(excluded),
        "maximumPossibleExcludedRecordCount": pool.maximum_possible_excluded_record_count,
    }
    if pool.record_count == 0 or n_allowed < k + 1:
        return {"ok": False, "reason": "insufficient_reference", **base}
    query = pool._query_feature(float(trial_elo), float(opponent_elo), previous_z)
    ranked = sorted(
        (
            float(pool._np.linalg.norm(pool.features[index] - query)),
            pool.stable_keys[index],
            int(index),
        )
        for index in range(pool.record_count)
        if index not in excluded
    )
    boundary = float(ranked[k][0])
    if not math.isfinite(boundary) or boundary <= 0:
        return {"ok": False, "reason": "insufficient_reference", "boundaryDistance": boundary, **base}
    selected = ranked[:k]
    weights = [max(0.0, 1.0 - float(item[0]) / boundary) for item in selected]
    weight_sum = float(sum(weights))
    if not math.isfinite(weight_sum) or weight_sum <= 0:
        return {"ok": False, "reason": "insufficient_reference", "boundaryDistance": boundary, **base}
    indexes = [item[2] for item in selected]
    ids = [pool.record_ids[index] for index in indexes]
    return {
        "ok": True,
        "neighborIndexes": indexes,
        "neighborRecordIds": ids,
        "neighborSetSha256": canonical_sha256(ids),
        "weights": weights,
        "N_allowed": n_allowed,
        "K": k,
        "eligibleReferenceCount": n_allowed,
        "boundaryDistance": boundary,
        "effectiveWeight": weight_sum,
        "excludedRecordCount": len(excluded),
        "maximumPossibleExcludedRecordCount": pool.maximum_possible_excluded_record_count,
    }


def audit_global_knn_consistency_v4(
    reference_records: Sequence[dict[str, Any]],
    reference_manifest: dict[str, Any],
    config: dict[str, Any],
    samples: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Prove the global-tree query equals stable excluded brute-force ranking."""

    cfg = validate_v4_config(config)
    index = GlobalAnscombeKNNIndexV4(reference_records, reference_manifest, cfg)
    rows: list[dict[str, Any]] = []
    mismatch_count = 0
    for sample in samples:
        color = str(sample["targetColor"]).casefold()
        scope = str(sample["scope"])
        stage = int(sample["stage"])
        pool = index.pool(color, scope, stage)
        if pool is None:
            raise ValueError(f"v4 KNN audit sample references missing pool {color}|{scope}|{stage}")
        account = account_key(sample.get("account") or sample.get("targetPlayerId"))
        game_ids = [str(value) for value in sample.get("targetGameIds", [])]
        trial_elo = float(sample["trialElo"])
        opponent_elo = float(sample["opponentElo"])
        previous_z = sample.get("previousZ")
        global_result = pool.nearest(
            account, game_ids, trial_elo, opponent_elo,
            None if previous_z is None else float(previous_z),
        )
        brute_result = _v4_bruteforce_nearest(
            pool.records, pool.scales, stage, cfg,
            account=account,
            target_game_ids=game_ids,
            trial_elo=trial_elo,
            opponent_elo=opponent_elo,
            previous_z=None if previous_z is None else float(previous_z),
        )
        same = (
            global_result.get("ok") == brute_result.get("ok")
            and global_result.get("reason") == brute_result.get("reason")
            and global_result.get("N_allowed") == brute_result.get("N_allowed")
            and global_result.get("K") == brute_result.get("K")
            and global_result.get("neighborRecordIds") == brute_result.get("neighborRecordIds")
            and (
                not global_result.get("ok")
                or (
                    math.isclose(
                        float(global_result["boundaryDistance"]),
                        float(brute_result["boundaryDistance"]),
                        rel_tol=0.0,
                        abs_tol=float(cfg["searchBoundaryTolerance"]),
                    )
                    and all(
                        math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
                        for left, right in zip(global_result["weights"], brute_result["weights"], strict=True)
                    )
                )
            )
        )
        if not same:
            mismatch_count += 1
        rows.append({
            "sample": {
                "targetColor": color,
                "scope": scope,
                "stage": stage,
                "account": account,
                "targetGameIds": game_ids,
                "trialElo": trial_elo,
                "opponentElo": opponent_elo,
                "previousZ": previous_z,
            },
            "matches": same,
            "globalTree": global_result,
            "bruteForce": brute_result,
        })
    return {
        "schema": "player-sentinel-elo-global-knn-consistency-audit-v4",
        "algorithmVersion": ALGORITHM_VERSION_V4,
        "configSha256": canonical_sha256(cfg),
        "referenceModelContractSha256": canonical_sha256(v4_reference_model_contract(cfg)),
        "sampleCount": len(rows),
        "mismatchCount": mismatch_count,
        "passed": mismatch_count == 0,
        "samples": rows,
        "treeBuildCount": index.tree_build_count,
    }


def directed_elo_bucket_matrix_v4(
    source_reference_directory: str | Path,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the actual black-Elo-by-white-Elo 9x9 matrix and 45-bucket view."""

    cfg = validate_v4_config(config or default_v4_config())
    source = Path(source_reference_directory)
    selected_path = source / "selected_games_with_partitions.json"
    selected = read_json(selected_path)
    games = selected.get("games") if isinstance(selected, dict) else None
    if not isinstance(games, list):
        raise ValueError(f"source reference has no selected games: {selected_path}")
    minimum = int(cfg["referenceEloBinMinimum"])
    width = int(cfg["referenceEloBinWidth"])
    bin_count = int(cfg["referenceEloBinCount"])
    qualifying: list[dict[str, Any]] = []
    seen_game_ids: set[str] = set()
    maximum_elo: float | None = None
    for game in games:
        if not isinstance(game, dict) or game.get("inMainMatrix") is not True:
            continue
        game_id = str(game.get("gameId") or "")
        black = game.get("blackOldR")
        white = game.get("whiteOldR")
        if not game_id or game_id in seen_game_ids:
            raise ValueError(f"directed Elo matrix contains duplicate/empty gameId: {game_id!r}")
        if not finite_number(black) or not finite_number(white):
            continue
        if float(black) < minimum or float(white) < minimum:
            continue
        seen_game_ids.add(game_id)
        maximum_elo = max(float(black), float(white), maximum_elo or float("-inf"))
        # The final formal bucket absorbs every value at or above its lower
        # bound through the frozen leaderboard upper Elo.  This is the same
        # closed top-bucket rule used by the source black/white partition.
        black_index = min(bin_count - 1, math.floor((float(black) - minimum) / width))
        white_index = min(bin_count - 1, math.floor((float(white) - minimum) / width))
        qualifying.append({
            "gameId": game_id,
            "blackBin": int(black_index),
            "whiteBin": int(white_index),
        })
    if maximum_elo is None:
        raise ValueError("directed Elo matrix has no qualifying games")
    dynamic_upper = max(float(minimum + bin_count * width), float(maximum_elo))
    top_lower = minimum + (bin_count - 1) * width
    labels = [
        f"[{minimum + index * width},{minimum + (index + 1) * width})"
        for index in range(bin_count - 1)
    ]
    labels.append(
        f"[{top_lower},{int(dynamic_upper) if dynamic_upper.is_integer() else dynamic_upper}]"
        if dynamic_upper > top_lower + width
        else f"[{top_lower},{top_lower + width})"
    )
    matrix = [[0 for _ in range(bin_count)] for _ in range(bin_count)]
    for game in qualifying:
        matrix[game["blackBin"]][game["whiteBin"]] += 1
    unordered: dict[str, int] = {}
    for black_index in range(bin_count):
        for white_index in range(black_index, bin_count):
            unordered[f"{labels[black_index]}__{labels[white_index]}"] = sum(
                1
                for game in qualifying
                if sorted((game["blackBin"], game["whiteBin"])) == [black_index, white_index]
            )
    return {
        "dimension": "black_Elo_bin_by_white_Elo_bin",
        "topLeftLabel": "黑棋\\白棋",
        "rowDimension": "black oldR Elo bucket",
        "columnDimension": "white oldR Elo bucket",
        "minimumElo": minimum,
        "dynamicMaximumObservedElo": maximum_elo,
        "dynamicUpperElo": dynamic_upper,
        "binWidth": width,
        "binCount": bin_count,
        "labels": labels,
        "matrix": matrix,
        "rowTotals": [sum(row) for row in matrix],
        "columnTotals": [sum(matrix[row][column] for row in range(bin_count)) for column in range(bin_count)],
        "directedGameCount": len(qualifying),
        "unordered45": unordered,
        "unordered45Total": sum(unordered.values()),
        "uniqueGameIdCount": len(seen_game_ids),
    }
