#!/usr/bin/env python3
"""Unified per-player sentinel analysis entry point.

The existing sentinel and estimated-Elo CLIs remain available for their
legacy and database-maintenance commands.  This script is the single
per-player analysis entry point used by ``run_player_investigation.py``: it
loads the completed legacy sentinel outputs, calculates the current v4
Anscombe/local-Gaussian Elo from the single frozen reference-z cache (and an
optional completed calibration artifact), and writes one combined analysis
payload for the final report.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any


TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOLKIT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from player_analysis_toolkit import sentinel_elo as elo  # noqa: E402
from player_analysis_toolkit.analysis_core import disc_loss  # noqa: E402


SCHEMA_UNIFIED = "player-sentinel-unified-analysis-v1"
SCHEMA_UNIFIED_V3 = "player-sentinel-unified-analysis-v2"
SCHEMA_UNIFIED_V4 = "player-sentinel-unified-analysis-v4"
SCHEMA_PLAYER_PHASE = "player-sentinel-phase-analysis-v1"
LEGACY_COMMANDS = {"acquire", "build-reference", "score", "scan", "freeze"}
ELO_COMMANDS = {
    "build-elo-reference", "prepare-conditional-elo-reference",
    "calibrate-elo", "audit-estimate-coverage", "estimate-elo",
    "audit-reference-z-sensitivity", "audit-knn-consistency",
}
PHASE_COUNT = 4
MINIMUM_PHASE_WIDTH = 6
PHASE_LABELS = ("前段", "第二阶段", "第三阶段", "后段")
WLD_START_PLY = 39
WLD_TOTAL_FIELD = "engine_wld_loss_total_from_ply39"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def require_directory(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def resolve_root_relative(value: str | Path) -> Path:
    raw = Path(value)
    return raw.resolve() if raw.is_absolute() else (TOOLKIT_ROOT / raw).resolve()


def _global_placement_ply(node: dict[str, Any]) -> int:
    raw = node.get("global_placement_ply", node.get("ply"))
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not float(raw).is_integer():
        raise ValueError("Level22 target node is missing an integer global placement ply")
    ply = int(raw)
    if not 1 <= ply <= 60:
        raise ValueError(f"global placement ply must be in [1, 60], got {ply}")
    return ply


def collect_actual_disc_loss_nodes(
    target_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    seen_game_ids: set[str] = set()
    for record in target_records:
        game_id = str(record.get("gameId") or "")
        if not game_id or game_id in seen_game_ids:
            raise ValueError(f"phase analysis requires unique non-empty game IDs, got {game_id!r}")
        seen_game_ids.add(game_id)
        source = require_file(Path(str(record.get("sourceLevel22File") or "")), "target Level22 game")
        source_sha256 = elo.sha256_file(source)
        if source_sha256 != record.get("sourceLevel22Sha256"):
            raise ValueError(f"target Level22 file hash changed: {source}")
        game = read_json(source)
        if str(game.get("gameId") or "") != game_id:
            raise ValueError(f"target Level22 game ID mismatch: {source}")
        target_color = str(record.get("targetColor") or "").strip().casefold()
        target_id = str(record.get("targetPlayerId") or "")
        if target_color not in {"black", "white"} or not target_id:
            raise ValueError(f"target direction is incomplete for game {game_id}")
        for node in game.get("nodes") or []:
            if not isinstance(node, dict):
                raise ValueError(f"Level22 nodes must be objects in game {game_id}")
            color = str(node.get("playerColor") or "").strip().casefold()
            if color != target_color:
                continue
            if elo.account_key(node.get("playerAccount")) != elo.account_key(target_id):
                raise ValueError(f"target-color Level22 node belongs to another account in game {game_id}")
            raw_loss = node.get("lossClipped")
            if not elo.finite_number(raw_loss):
                continue
            actual_disc_loss = disc_loss(raw_loss)
            nodes.append({
                "gameId": game_id,
                "ply": _global_placement_ply(node),
                "discLoss": actual_disc_loss,
            })
        source_files.append({"path": str(source), "sha256": source_sha256})
    return nodes, source_files


def _segment_costs(
    ply_statistics: dict[int, dict[str, float]], minimum_ply: int, maximum_ply: int,
) -> list[list[float]]:
    span = maximum_ply - minimum_ply + 1
    weights = [0.0] * span
    means = [0.0] * span
    for ply, stats in ply_statistics.items():
        index = ply - minimum_ply
        weights[index] = float(stats["weight"])
        means[index] = float(stats["meanLog1pDiscLoss"])
    prefix_weight = [0.0]
    prefix_weighted_mean = [0.0]
    prefix_weighted_square = [0.0]
    for weight, mean in zip(weights, means):
        prefix_weight.append(prefix_weight[-1] + weight)
        prefix_weighted_mean.append(prefix_weighted_mean[-1] + weight * mean)
        prefix_weighted_square.append(prefix_weighted_square[-1] + weight * mean * mean)
    costs = [[math.inf] * span for _ in range(span)]
    for start in range(span):
        for end in range(start, span):
            weight = prefix_weight[end + 1] - prefix_weight[start]
            if weight <= 0:
                continue
            weighted_mean = prefix_weighted_mean[end + 1] - prefix_weighted_mean[start]
            weighted_square = prefix_weighted_square[end + 1] - prefix_weighted_square[start]
            costs[start][end] = max(0.0, weighted_square - weighted_mean * weighted_mean / weight)
    return costs


def fit_four_phase_boundaries(
    nodes: list[dict[str, Any]],
    *,
    phase_count: int = PHASE_COUNT,
    minimum_phase_width: int = MINIMUM_PHASE_WIDTH,
) -> dict[str, Any] | None:
    if not nodes:
        return None
    losses_by_ply: dict[int, list[float]] = {}
    for node in nodes:
        ply = int(node["ply"])
        losses_by_ply.setdefault(ply, []).append(float(node["discLoss"]))
    minimum_ply = min(losses_by_ply)
    maximum_ply = max(losses_by_ply)
    span = maximum_ply - minimum_ply + 1
    if span < phase_count * minimum_phase_width:
        return None
    ply_statistics = {
        ply: {
            "validNodeCount": float(len(losses)),
            "meanLog1pDiscLoss": sum(math.log1p(loss) for loss in losses) / len(losses),
            "weight": math.sqrt(len(losses)),
        }
        for ply, losses in losses_by_ply.items()
    }
    costs = _segment_costs(ply_statistics, minimum_ply, maximum_ply)
    span_end = span - 1
    states: list[dict[int, tuple[float, tuple[int, ...]]]] = [dict() for _ in range(phase_count + 1)]
    for end in range(minimum_phase_width - 1, span):
        cost = costs[0][end]
        if math.isfinite(cost):
            states[1][end] = (cost, ())
    for stages in range(2, phase_count + 1):
        for end in range(stages * minimum_phase_width - 1, span):
            best: tuple[float, tuple[int, ...]] | None = None
            first_start = (stages - 1) * minimum_phase_width
            last_start = end - minimum_phase_width + 1
            for start in range(first_start, last_start + 1):
                previous = states[stages - 1].get(start - 1)
                segment_cost = costs[start][end]
                if previous is None or not math.isfinite(segment_cost):
                    continue
                boundaries = previous[1] + (minimum_ply + start - 1,)
                candidate = (previous[0] + segment_cost, boundaries)
                if best is None or candidate[0] < best[0] or (
                    candidate[0] == best[0] and candidate[1] < best[1]
                ):
                    best = candidate
            if best is not None:
                states[stages][end] = best
    fitted = states[phase_count].get(span_end)
    if fitted is None:
        return None
    return {
        "boundaries": list(fitted[1]),
        "fitLoss": fitted[0],
        "minimumObservedPly": minimum_ply,
        "maximumObservedPly": maximum_ply,
        "plyStatistics": ply_statistics,
    }


def summarize_wld_from_ply39(
    engine_directory: Path,
    account: str,
    target_records: list[dict[str, Any]],
    actual_loss_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    source = require_file(
        engine_directory / "engine_wld_loss_totals_from_ply39.json",
        "Level22 WLD totals",
    )
    value = read_json(source)
    if value.get("schema") != "player-engine-wld-loss-totals-v1" or value.get("wldFromPly") != WLD_START_PLY:
        raise ValueError("Level22 WLD totals must use the frozen inclusive ply-39 contract")
    target_by_game = {str(record["gameId"]): str(record["targetColor"]) for record in target_records}
    rows: list[dict[str, Any]] = []
    for row in value.get("gamePlayerTotals") or []:
        if elo.account_key(row.get("player_id")) != elo.account_key(account):
            continue
        game_id = str(row.get("game_id") or "")
        if game_id not in target_by_game or str(row.get("side") or "").casefold() != target_by_game[game_id]:
            raise ValueError(f"WLD target direction mismatch for game {game_id}")
        if not elo.finite_number(row.get(WLD_TOTAL_FIELD)):
            raise ValueError(f"WLD total is not finite for game {game_id}")
        rows.append(row)
    row_ids = [str(row.get("game_id") or "") for row in rows]
    if len(row_ids) != len(set(row_ids)) or set(row_ids) != set(target_by_game):
        raise ValueError("Level22 WLD totals do not cover each target game exactly once")
    total = sum(float(row[WLD_TOTAL_FIELD]) for row in rows)
    late_nodes = [node for node in actual_loss_nodes if int(node["ply"]) >= WLD_START_PLY]
    return {
        "startPly": WLD_START_PLY,
        "boundaryInclusive": True,
        "aggregationDenominator": "target_game_player_totals",
        "validGames": len(rows),
        "gamesWithValidNodes": len({str(node["gameId"]) for node in late_nodes}),
        "validNodes": len(late_nodes),
        WLD_TOTAL_FIELD: round(total, 12),
        "meanPerValidGame": round(total / len(rows), 12) if rows else None,
        "sourceArtifact": {"path": str(source), "sha256": elo.sha256_file(source)},
    }


def analyze_player_phase_nodes(
    nodes: list[dict[str, Any]],
    wld_from_ply39: dict[str, Any],
) -> dict[str, Any]:
    method = {
        "source": "level22_actual_disc_loss",
        "actualDiscLossField": "lossClipped",
        "negativeLossHandling": "disc_loss=max(0, lossClipped)",
        "phaseCount": PHASE_COUNT,
        "minimumPhaseWidth": MINIMUM_PHASE_WIDTH,
        "maximumPhaseWidth": None,
        "fitTransform": "log1p",
        "fitWeight": "sqrt(valid_node_count)",
        "usesTcn": False,
        "estimatesPhaseElo": False,
    }
    valid_games = len({str(node["gameId"]) for node in nodes})
    minimum_ply = min((int(node["ply"]) for node in nodes), default=None)
    maximum_ply = max((int(node["ply"]) for node in nodes), default=None)
    input_summary = {
        "validGames": valid_games,
        "validNodes": len(nodes),
        "minimumObservedPly": minimum_ply,
        "maximumObservedPly": maximum_ply,
    }
    fitted = fit_four_phase_boundaries(nodes)
    if fitted is None:
        span = 0 if minimum_ply is None or maximum_ply is None else maximum_ply - minimum_ply + 1
        reason = "observed_ply_span_below_24" if span < PHASE_COUNT * MINIMUM_PHASE_WIDTH else "unable_to_fit_four_nonempty_phases"
        return {
            "schema": SCHEMA_PLAYER_PHASE,
            "status": "insufficient_phase_data",
            "statusReasons": [reason],
            "method": method,
            "input": input_summary,
            "boundaries": [],
            "fitLoss": None,
            "phases": [],
            "strongestPhase": None,
            "strongestPhaseLabel": None,
            "weakestPhase": None,
            "weakestPhaseLabel": None,
            "wldFromPly39": wld_from_ply39,
        }
    boundaries = list(fitted["boundaries"])
    starts = [int(fitted["minimumObservedPly"])] + [boundary + 1 for boundary in boundaries]
    ends = boundaries + [int(fitted["maximumObservedPly"])]
    phases: list[dict[str, Any]] = []
    for phase_number, (start, end, label) in enumerate(zip(starts, ends, PHASE_LABELS), start=1):
        phase_nodes = [node for node in nodes if start <= int(node["ply"]) <= end]
        losses = [float(node["discLoss"]) for node in phase_nodes]
        count = len(losses)
        loss_eq0 = sum(loss == 0 for loss in losses)
        loss_ge4 = sum(loss >= 4 for loss in losses)
        loss_ge10 = sum(loss >= 10 for loss in losses)
        phases.append({
            "phase": phase_number,
            "label": label,
            "startPly": start,
            "endPly": end,
            "plyWidth": end - start + 1,
            "validGames": len({str(node["gameId"]) for node in phase_nodes}),
            "validNodes": count,
            "meanDiscLoss": round(sum(losses) / count, 12),
            "lossEq0Count": loss_eq0,
            "lossGe4Count": loss_ge4,
            "lossGe10Count": loss_ge10,
            "probabilityLossEq0": round(loss_eq0 / count, 12),
            "probabilityLossGe4": round(loss_ge4 / count, 12),
            "probabilityLossGe10": round(loss_ge10 / count, 12),
        })
    strongest = min(phases, key=lambda phase: (float(phase["meanDiscLoss"]), int(phase["phase"])))
    weakest = min(phases, key=lambda phase: (-float(phase["meanDiscLoss"]), int(phase["phase"])))
    return {
        "schema": SCHEMA_PLAYER_PHASE,
        "status": "ok",
        "statusReasons": [],
        "method": method,
        "input": input_summary,
        "boundaries": boundaries,
        "fitLoss": round(float(fitted["fitLoss"]), 12),
        "phases": phases,
        "strongestPhase": int(strongest["phase"]),
        "strongestPhaseLabel": str(strongest["label"]),
        "weakestPhase": int(weakest["phase"]),
        "weakestPhaseLabel": str(weakest["label"]),
        "wldFromPly39": wld_from_ply39,
    }


def build_player_phase_analysis(
    target_records: list[dict[str, Any]],
    bundle_path: Path,
    engine_directory: Path,
    account: str,
) -> dict[str, Any]:
    bundle = require_file(bundle_path, "target bundle")
    engine = require_directory(engine_directory, "target Level22 directory")
    audit_path = require_file(engine / "audit.json", "target Level22 audit")
    audit = read_json(audit_path)
    bundle_sha256 = elo.sha256_file(bundle)
    if audit.get("ok") is not True or audit.get("sourceBundleSha256") != bundle_sha256:
        raise ValueError("target Level22 audit does not match the selected account bundle")
    nodes, source_files = collect_actual_disc_loss_nodes(target_records)
    wld = summarize_wld_from_ply39(engine, account, target_records, nodes)
    analysis = analyze_player_phase_nodes(nodes, wld)
    analysis["sourceArtifacts"] = {
        "selectedBundle": {"path": str(bundle), "sha256": bundle_sha256},
        "level22Audit": {"path": str(audit_path), "sha256": elo.sha256_file(audit_path)},
        "level22Files": source_files,
    }
    return analysis


def player_phase_csv_rows(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for phase in analysis.get("phases") or []:
        rows.append({
            "recordType": "phase",
            "status": analysis.get("status"),
            **phase,
            "isStrongestPhase": phase.get("phase") == analysis.get("strongestPhase"),
            "isWeakestPhase": phase.get("phase") == analysis.get("weakestPhase"),
        })
    if not rows:
        rows.append({
            "recordType": "status",
            "status": analysis.get("status"),
            "statusReasons": analysis.get("statusReasons"),
        })
    wld = analysis["wldFromPly39"]
    rows.append({
        "recordType": "wld_global",
        "status": analysis.get("status"),
        "startPly": wld.get("startPly"),
        "validGames": wld.get("validGames"),
        "validNodes": wld.get("validNodes"),
        "gamesWithValidNodes": wld.get("gamesWithValidNodes"),
        WLD_TOTAL_FIELD: wld.get(WLD_TOTAL_FIELD),
        "meanPerValidGame": wld.get("meanPerValidGame"),
        "aggregationDenominator": wld.get("aggregationDenominator"),
    })
    return rows


def write_player_phase_outputs(output_dir: Path, analysis: dict[str, Any]) -> dict[str, Any]:
    json_path = output_dir / "player_phase_analysis.json"
    csv_path = output_dir / "player_phase_analysis.csv"
    elo.write_json(json_path, analysis, refuse_existing=False)
    elo.write_csv(csv_path, player_phase_csv_rows(analysis), refuse_existing=False)
    return {
        "json": {"path": str(json_path.resolve()), "sha256": elo.sha256_file(json_path)},
        "csv": {"path": str(csv_path.resolve()), "sha256": elo.sha256_file(csv_path)},
    }


def load_elo_reference(config_path: Path) -> dict[str, Any]:
    config_path = require_file(config_path, "sentinel Elo reference config")
    config = elo.load_config(config_path)
    derived = require_directory(
        resolve_root_relative(str(config["derivedReferenceDirectory"])),
        "sentinel Elo derived reference",
    )
    records_path = require_file(
        derived / str(config.get("directedPhaseRecords") or "directed_game_phase_records.jsonl"),
        "sentinel Elo directed phase records",
    )
    manifest_path = require_file(
        derived / str(config.get("referenceManifest") or "reference_sha256_manifest.json"),
        "sentinel Elo reference manifest",
    )
    manifest = read_json(manifest_path)
    if manifest.get("schema") != "player-sentinel-elo-reference-sha256-manifest-v1":
        raise ValueError("unsupported sentinel Elo reference manifest schema")
    for item in manifest.get("files", []):
        path = derived / str(item["path"])
        if not path.is_file() or elo.sha256_file(path) != item.get("sha256"):
            raise ValueError(f"sentinel Elo reference manifest mismatch: {path}")
    if config.get("schema") == elo.SCHEMA_CONFIG_V4:
        config = elo.validate_v4_config(config)
        conditional_dir = require_directory(
            resolve_root_relative(str(config["conditionalReferenceDirectory"])),
            "sentinel Elo v4 Anscombe reference",
        )
        conditional_records, conditional_manifest, conditional_manifest_sha = elo.load_anscombe_reference_v4(
            conditional_dir,
            config=config,
            expected_reference_manifest_sha256=elo.sha256_file(manifest_path),
        )
        calibration_dir = resolve_root_relative(str(config["calibrationDirectory"]))
        calibration_path = calibration_dir / str(config["calibrationArtifact"])
        calibration_manifest_path = calibration_dir / "calibration_sha256_manifest_v4.json"
        calibration = None
        calibration_sha = None
        calibration_manifest_sha = None
        if calibration_path.is_file():
            calibration = read_json(calibration_path)
            if calibration.get("schema") != elo.SCHEMA_CALIBRATION_V4:
                raise ValueError("unsupported sentinel Elo v4 calibration schema")
            if (
                calibration.get("referenceManifestSha256")
                not in {None, conditional_manifest_sha}
            ):
                raise ValueError("sentinel Elo v4 calibration/reference cache mismatch")
            if not calibration_manifest_path.is_file():
                raise FileNotFoundError(
                    f"sentinel Elo v4 calibration SHA-256 manifest not found: {calibration_manifest_path}"
                )
            calibration_manifest = read_json(calibration_manifest_path)
            if calibration_manifest.get("schema") != elo.SCHEMA_CALIBRATION_MANIFEST_V4:
                raise ValueError("unsupported sentinel Elo v4 calibration SHA-256 manifest")
            for item in calibration_manifest.get("files", []):
                artifact_path = calibration_dir / str(item["path"])
                if not artifact_path.is_file() or elo.sha256_file(artifact_path) != item.get("sha256"):
                    raise ValueError(f"sentinel Elo v4 calibration manifest mismatch: {artifact_path}")
            calibration_sha = elo.sha256_file(calibration_path)
            calibration_manifest_sha = elo.sha256_file(calibration_manifest_path)
        return {
            "config": config,
            "configPath": config_path,
            "derived": derived,
            "recordsPath": records_path,
            "manifestPath": manifest_path,
            "conditionalDirectory": conditional_dir,
            "conditionalRecordsPath": (
                conditional_dir / str(config["conditionalReferenceRecords"])
            ),
            "conditionalRecords": conditional_records,
            "conditionalManifest": conditional_manifest,
            "conditionalManifestSha256": conditional_manifest_sha,
            "referenceManifestSha256": elo.sha256_file(manifest_path),
            "sourceReferenceManifestSha256": elo.sha256_file(manifest_path),
            "referenceModelManifestSha256": conditional_manifest_sha,
            "referenceModelContractSha256": conditional_manifest.get(
                "referenceModelContractSha256"
            ),
            "calibrationDirectory": calibration_dir,
            "calibrationPath": calibration_path if calibration is not None else None,
            "manifestSha256": elo.sha256_file(manifest_path),
            "calibrationSha256": calibration_sha,
            "calibrationManifestSha256": calibration_manifest_sha,
            "calibration": calibration,
        }
    if config.get("schema") == elo.SCHEMA_CONFIG_V3:
        config = elo.validate_v3_config(config)
        conditional_dir = require_directory(
            resolve_root_relative(str(config["conditionalReferenceDirectory"])),
            "sentinel Elo v3 conditional reference",
        )
        calibration_dir = require_directory(
            resolve_root_relative(str(config["calibrationDirectory"])),
            "sentinel Elo v3 calibration directory",
        )
        conditional_records, conditional_manifest, conditional_manifest_sha = elo.load_conditional_reference_v3(
            conditional_dir,
            config=config,
            expected_reference_manifest_sha256=elo.sha256_file(manifest_path),
        )
        calibration_path = require_file(
            calibration_dir / str(config["calibrationArtifact"]),
            "sentinel Elo v3 calibration cache",
        )
        calibration = read_json(calibration_path)
        if calibration.get("schema") != elo.SCHEMA_CALIBRATION_V3:
            raise ValueError("unsupported sentinel Elo v3 calibration schema")
        if calibration.get("conditionalManifestSha256") != conditional_manifest_sha:
            raise ValueError("sentinel Elo v3 calibration/conditional cache mismatch")
        calibration_manifest_path = require_file(
            calibration_dir / "calibration_sha256_manifest_v3.json",
            "sentinel Elo v3 calibration SHA-256 manifest",
        )
        calibration_manifest = read_json(calibration_manifest_path)
        if calibration_manifest.get("schema") != elo.SCHEMA_CALIBRATION_MANIFEST_V3:
            raise ValueError("unsupported sentinel Elo v3 calibration SHA-256 manifest")
        for item in calibration_manifest.get("files", []):
            artifact_path = calibration_dir / str(item["path"])
            if not artifact_path.is_file() or elo.sha256_file(artifact_path) != item.get("sha256"):
                raise ValueError(f"sentinel Elo v3 calibration manifest mismatch: {artifact_path}")
        return {
            "config": config,
            "configPath": config_path,
            "derived": derived,
            "recordsPath": records_path,
            "manifestPath": manifest_path,
            "conditionalDirectory": conditional_dir,
            "conditionalRecords": conditional_records,
            "conditionalManifest": conditional_manifest,
            "conditionalManifestSha256": conditional_manifest_sha,
            "calibrationPath": calibration_path,
            "manifestSha256": elo.sha256_file(manifest_path),
            "calibrationSha256": elo.sha256_file(calibration_path),
            "calibrationManifestSha256": elo.sha256_file(calibration_manifest_path),
            "calibration": calibration,
        }
    if config.get("schema") == elo.SCHEMA_CONFIG_V2:
        conditional_dir = require_directory(
            resolve_root_relative(str(config["conditionalReferenceDirectory"])),
            "sentinel Elo conditional reference",
        )
        calibration_dir = require_directory(
            resolve_root_relative(str(config["calibrationDirectory"])),
            "sentinel Elo v2 calibration directory",
        )
        conditional_records, conditional_manifest, conditional_manifest_sha = elo.load_conditional_reference_v2(
            conditional_dir,
            config=config,
            expected_reference_manifest_sha256=elo.sha256_file(manifest_path),
        )
        calibration_path = require_file(
            calibration_dir / str(config["calibrationArtifact"]),
            "sentinel Elo v2 calibration cache",
        )
        calibration = read_json(calibration_path)
        if calibration.get("schema") != elo.SCHEMA_CALIBRATION_V2:
            raise ValueError("unsupported sentinel Elo v2 calibration schema")
        if calibration.get("conditionalManifestSha256") != conditional_manifest_sha:
            raise ValueError("sentinel Elo v2 calibration/conditional cache mismatch")
        calibration_manifest_path = require_file(
            calibration_dir / "calibration_sha256_manifest_v2.json",
            "sentinel Elo v2 calibration SHA-256 manifest",
        )
        calibration_manifest = read_json(calibration_manifest_path)
        if calibration_manifest.get("schema") != "player-sentinel-elo-calibration-sha256-manifest-v2":
            raise ValueError("unsupported sentinel Elo v2 calibration SHA-256 manifest")
        for item in calibration_manifest.get("files", []):
            artifact_path = calibration_dir / str(item["path"])
            if not artifact_path.is_file() or elo.sha256_file(artifact_path) != item.get("sha256"):
                raise ValueError(f"sentinel Elo v2 calibration manifest mismatch: {artifact_path}")
        return {
            "config": config,
            "configPath": config_path,
            "derived": derived,
            "recordsPath": records_path,
            "manifestPath": manifest_path,
            "conditionalDirectory": conditional_dir,
            "conditionalRecords": conditional_records,
            "conditionalManifest": conditional_manifest,
            "conditionalManifestSha256": conditional_manifest_sha,
            "calibrationPath": calibration_path,
            "manifestSha256": elo.sha256_file(manifest_path),
            "calibrationSha256": elo.sha256_file(calibration_path),
            "calibrationManifestSha256": elo.sha256_file(calibration_manifest_path),
            "calibration": calibration,
        }
    calibration_path = require_file(
        derived / str(config.get("calibrationArtifact") or "elo_calibration.json"),
        "sentinel Elo calibration cache",
    )
    calibration = read_json(calibration_path)
    if calibration.get("schema") != "player-sentinel-elo-calibration-v1":
        raise ValueError("unsupported sentinel Elo calibration schema")
    return {
        "config": config,
        "configPath": config_path,
        "derived": derived,
        "recordsPath": records_path,
        "manifestPath": manifest_path,
        "calibrationPath": calibration_path,
        "manifestSha256": elo.sha256_file(manifest_path),
        "calibrationSha256": elo.sha256_file(calibration_path),
        "calibration": calibration,
    }


def write_estimate_outputs(output_dir: Path, payload: dict[str, Any], curve: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    curve_name = "estimated_elo_curve.csv"
    games_name = "estimated_elo_games.csv"
    phase_name = "estimated_elo_phase_diagnostics.csv"
    payload["curveFile"] = curve_name
    is_v2 = payload.get("schema") == elo.SCHEMA_ESTIMATE_V2
    is_v3 = payload.get("schema") == elo.SCHEMA_ESTIMATE_V3
    is_v4 = payload.get("schema") == elo.SCHEMA_ESTIMATE_V4
    elo.write_csv(
        output_dir / curve_name,
        [
            {
                "elo": point.get("elo"),
                **({
                    "meanNegativeLogLikelihood": point.get("meanNegativeLogLikelihood"),
                    "score": point.get("score"),
                    "meanConditionalZ": point.get("meanConditionalZ"),
                    "validTargetGameCount": point.get("validTargetGameCount"),
                    "failureReasons": point.get("failureReasons"),
                    "evaluatedBySearch": True,
                } if is_v2 or is_v3 else {
                    **({
                        "meanNegativeLogPredictiveDensity": point.get("meanNegativeLogPredictiveDensity"),
                        "score": point.get("score"),
                        "meanTargetZ": point.get("meanTargetZ"),
                        "validGameCount": point.get("validGameCount"),
                        "targetGameCount": point.get("targetGameCount"),
                        "failureReasons": point.get("failureReasons"),
                        "evaluatedBySearch": True,
                    } if is_v4 else {
                        "candidateZ": point.get("candidateZ"),
                        "score": point.get("score"),
                    }),
                }),
            }
            for point in curve.get("points", [])
        ],
        refuse_existing=False,
    )
    elo.write_csv(output_dir / games_name, payload.get("gameDiagnostics", []), refuse_existing=False)
    elo.write_csv(output_dir / phase_name, payload.get("phaseDiagnostics", []), refuse_existing=False)
    payload["gamesFile"] = games_name
    payload["phaseDiagnosticsFile"] = phase_name
    elo.write_json(output_dir / "estimated_elo.json", payload, refuse_existing=False)


def legacy_summary(output_dir: Path) -> dict[str, Any]:
    paths = {
        "scores": output_dir / "per_game_reference_scores.json",
        "scan": output_dir / "sentinel_scan_results.json",
        "pseudoScan": output_dir / "pseudo_scan_summary.json",
        "selection": output_dir / "selection_manifest.json",
        "modelGroups": output_dir / "model_review_groups.json",
    }
    for label, path in paths.items():
        require_file(path, f"legacy sentinel {label} output")
    scores = read_json(paths["scores"])
    scan = read_json(paths["scan"])
    selection = read_json(paths["selection"])
    return {
        "classification": scan.get("classification"),
        "selectedK": scan.get("selectedK"),
        "reportedGameIds": scan.get("reportedGameIds", []),
        "modelReviewReady": selection.get("modelReviewReady"),
        "targetRecordCount": scores.get("targetRecordCount"),
        "calibratableGameCount": scores.get("calibratableGameCount"),
        "excludedReferenceGameCount": scores.get("excludedReferenceGameCount"),
        "selection": selection,
        "sentinelScan": scan,
        "outputArtifacts": {
            label: {
                "path": str(path.resolve()),
                "sha256": elo.sha256_file(path),
            }
            for label, path in paths.items()
        },
    }


def command_run(args: argparse.Namespace) -> int:
    output_dir = require_directory(args.output_dir.resolve(), "sentinel unified output directory")
    reference = load_elo_reference(args.elo_reference_config.resolve())
    config = reference["config"]
    calibration = reference["calibration"] or {}
    target_records = elo.target_records_from_inputs(
        args.bundle.resolve(),
        args.engine_dir.resolve(),
        args.offbook_records.resolve(),
        args.account,
        config=config,
    )
    phase_analysis = build_player_phase_analysis(
        target_records,
        args.bundle.resolve(),
        args.engine_dir.resolve(),
        args.account,
    )
    phase_artifacts = write_player_phase_outputs(output_dir, phase_analysis)
    if config.get("schema") == elo.SCHEMA_CONFIG_V4:
        source_bundle = read_json(args.bundle.resolve())
        known_elo = elo._latest_known_elos(source_bundle).get(elo.account_key(args.account))
        estimate = elo.estimate_database_calibrated_range_v4(
            args.account,
            target_records,
            reference["conditionalRecords"],
            reference["conditionalManifest"],
            config=config,
            calibration=reference["calibration"],
            reference_manifest_sha256=reference["conditionalManifestSha256"],
            calibration_sha256=reference["calibrationSha256"],
            known_elo=known_elo,
            reference_records_path=reference.get("conditionalRecordsPath"),
        )
    elif config.get("schema") == elo.SCHEMA_CONFIG_V3:
        source_bundle = read_json(args.bundle.resolve())
        known_elo = elo._latest_known_elos(source_bundle).get(elo.account_key(args.account))
        estimate = elo.estimate_database_calibrated_range_v3(
            args.account,
            target_records,
            reference["conditionalRecords"],
            reference["conditionalManifest"],
            config=config,
            calibration=calibration,
            conditional_manifest_sha256=reference["conditionalManifestSha256"],
            calibration_sha256=reference["calibrationSha256"],
            known_elo=known_elo,
        )
    elif config.get("schema") == elo.SCHEMA_CONFIG_V2:
        estimate = elo.estimate_database_calibrated_range_v2(
            args.account,
            target_records,
            reference["conditionalRecords"],
            reference["conditionalManifest"],
            config=config,
            calibration=calibration,
            conditional_manifest_sha256=reference["conditionalManifestSha256"],
            calibration_sha256=reference["calibrationSha256"],
        )
    else:
        records = elo.reference_records_from_directory(reference["derived"], config=config)
        estimate = elo.estimate_database_calibrated_range(
            args.account,
            target_records,
            records,
            config=config,
            calibration=calibration,
            reference_version=reference["derived"].name,
            reference_manifest_sha256=reference["manifestSha256"],
            calibration_version=reference["calibrationSha256"],
        )
    estimate_dir = output_dir / "estimated_elo"
    write_estimate_outputs(estimate_dir, estimate.payload, estimate.curve)
    unified = {
        "schema": (
            SCHEMA_UNIFIED_V4 if config.get("schema") == elo.SCHEMA_CONFIG_V4
            else SCHEMA_UNIFIED_V3 if config.get("schema") == elo.SCHEMA_CONFIG_V3
            else SCHEMA_UNIFIED
        ),
        "account": args.account,
        "createdAt": utc_now(),
        "offbookContracts": {
            "level22SentinelStatistical": {
                "labelSource": "first-log-time-or-abs6-with-post-fast-v5",
                "records": str(args.offbook_records.resolve()),
                "recordsSha256": elo.sha256_file(args.offbook_records),
                "role": "sentinel statistical/reference scoring and phase analysis",
            },
            "level18TcnFeature": {
                "schema": "offbook-ply-level18-hint6-v1",
                "role": "materialized later from assembled Level18 hint6_1_score for TCN input",
                "usedByThisUnifiedStatisticalStage": False,
            },
        },
        "legacySentinel": legacy_summary(output_dir),
        "estimatedElo": estimate.payload,
        "playerPhaseAnalysis": phase_analysis,
        "reference": {
            "version": reference["derived"].name,
            "manifestSha256": reference["manifestSha256"],
            "conditionalManifestSha256": reference.get("conditionalManifestSha256"),
            "sourceReferenceManifestSha256": reference.get("sourceReferenceManifestSha256"),
            "referenceModelManifestSha256": reference.get("referenceModelManifestSha256"),
            "calibrationSha256": reference.get("calibrationSha256"),
            "calibrationManifestSha256": reference.get("calibrationManifestSha256"),
            "calibrationStatus": calibration.get("status"),
            "referenceModelContractSha256": reference.get("referenceModelContractSha256"),
            "searchContractSha256": (
                calibration.get("searchContractSha256")
                if calibration else elo.canonical_sha256(elo.v4_search_contract(config))
            ),
            "calibrationContractSha256": (
                calibration.get("calibrationContractSha256")
                if calibration else elo.canonical_sha256(elo.v4_calibration_contract(config))
            ),
            "searchStrategyVersion": (
                calibration.get("searchStrategyVersion")
                if calibration else elo.SEARCH_STRATEGY_VERSION_V4
            ),
            "searchSteps": (
                calibration.get("searchSteps")
                if calibration else list(elo.SEARCH_STEPS_V4)
            ),
        },
        "estimatedEloArtifacts": {
            "directory": str(estimate_dir.resolve()),
            "sha256": elo.sha256_file(estimate_dir / "estimated_elo.json"),
        },
        "playerPhaseAnalysisArtifacts": phase_artifacts,
    }
    elo.write_json(output_dir / "sentinel_unified_analysis.json", unified, refuse_existing=False)
    print(json.dumps({
        "account": args.account,
        "legacyClassification": unified["legacySentinel"]["classification"],
        "reportedGameCount": len(unified["legacySentinel"]["reportedGameIds"]),
        "estimatedEloStatus": estimate.payload.get("status"),
        "estimatedElo": estimate.payload.get("estimatedElo"),
        "selectedGameCount": estimate.payload.get("selectedGameCount"),
        "playerPhaseAnalysisStatus": phase_analysis.get("status"),
        "playerPhaseAnalysisValidGameCount": phase_analysis.get("input", {}).get("validGames"),
        "output": str((output_dir / "sentinel_unified_analysis.json").resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="combine legacy sentinel outputs with estimated Elo")
    run.add_argument("--account", required=True)
    run.add_argument("--bundle", type=Path, required=True)
    run.add_argument("--engine-dir", type=Path, required=True)
    run.add_argument("--offbook-records", type=Path, required=True)
    run.add_argument("--elo-reference-config", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.set_defaults(handler=command_run)
    return parser


def load_script(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import compatibility CLI: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        build_parser().print_help()
        print(
            "\nCompatibility command families: "
            "acquire/build-reference/score/scan/freeze and "
            "build-elo-reference/calibrate-elo/estimate-elo."
        )
        return 0 if args else 2
    command = args[0]
    if command == "run":
        parsed = build_parser().parse_args(args)
        return int(parsed.handler(parsed))
    if command in LEGACY_COMMANDS:
        legacy = load_script(TOOLKIT_ROOT / "scripts" / "analysis" / "sentinel_analysis.py", "sentinel_legacy_cli")
        return int(legacy.main(args))
    if command in ELO_COMMANDS:
        elo_cli = load_script(TOOLKIT_ROOT / "scripts" / "analysis" / "sentinel_elo_analysis.py", "sentinel_elo_cli")
        return int(elo_cli.main(args))
    raise ValueError(f"unknown unified sentinel command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
