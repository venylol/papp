#!/usr/bin/env python3
"""Build, calibrate, and estimate sentinel mode's database-calibrated Elo."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOLKIT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from player_analysis_toolkit import sentinel_elo as elo  # noqa: E402


DEFAULT_CONFIG_PATH = TOOLKIT_ROOT / "sentinel_elo_reference_config_v4_matchup600_20260911.json"
V3_CONFIG_PATH = TOOLKIT_ROOT / "sentinel_elo_reference_config_v3_20260829.json"
LEGACY_DEFAULT_CONFIG_PATH = TOOLKIT_ROOT / "sentinel_elo_reference_config.json"


def _config(path: Path) -> dict[str, Any]:
    if path.is_file():
        return elo.load_config(path)
    if path == DEFAULT_CONFIG_PATH:
        return elo.validate_v4_config(elo.default_v4_config())
    if path == V3_CONFIG_PATH:
        return elo.validate_v3_config(elo.default_v3_config())
    if path == LEGACY_DEFAULT_CONFIG_PATH:
        return elo.validate_config(elo.default_config())
    raise FileNotFoundError(path)


def _resolve_from_root(value: str | None, default: str | None = None) -> Path:
    raw = value if value is not None else default
    if raw is None:
        raise ValueError("a path argument is required")
    path = Path(raw)
    return path if path.is_absolute() else TOOLKIT_ROOT / path


def command_build(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    config = _config(config_path)
    source = _resolve_from_root(args.source_reference_dir, str(config["sourceReferenceDirectory"]))
    sentinel_dir = _resolve_from_root(args.sentinel_derived_dir, str(config["sentinelDerivedDirectory"]))
    output = _resolve_from_root(args.output_dir, str(config["derivedReferenceDirectory"]))
    audit = elo.build_elo_reference(
        source,
        sentinel_dir,
        output,
        config=config,
        config_path=config_path if config_path.is_file() else None,
        build_script_paths=(Path(__file__), TOOLKIT_ROOT / "src" / "player_analysis_toolkit" / "sentinel_elo.py"),
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


def command_prepare_conditional(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    config = _config(config_path)
    if config.get("schema") == elo.SCHEMA_CONFIG_V4:
        config = elo.validate_v4_config(config)
        reference_dir = _resolve_from_root(
            args.reference_dir, str(config["derivedReferenceDirectory"])
        )
        output_dir = _resolve_from_root(
            args.output_dir, str(config["conditionalReferenceDirectory"])
        )
        directed_path = reference_dir / str(config["directedPhaseRecords"])
        reference_manifest_path = reference_dir / str(config["referenceManifest"])
        records = elo.reference_records_from_directory(reference_dir, config=config)
        manifest = elo.prepare_anscombe_reference_v4(
            records,
            output_dir,
            config=config,
            input_records_path=directed_path,
            reference_manifest_sha256=(
                elo.sha256_file(reference_manifest_path)
                if reference_manifest_path.is_file() else None
            ),
            resume=bool(args.resume),
            maximum_records_per_pool=args.max_records_per_pool,
        )
        print(json.dumps({
            "status": "completed",
            "algorithmVersion": elo.ALGORITHM_VERSION_V4,
            "outputDirectory": str(output_dir.resolve()),
            "recordCount": manifest["recordCount"],
            "recordsSha256": manifest["recordsSha256"],
            "referenceModelContractSha256": manifest["referenceModelContractSha256"],
            "referenceManifestSha256": elo.sha256_file(output_dir / str(config["conditionalReferenceManifest"])),
        }, ensure_ascii=False, indent=2))
        return 0
    is_v3 = config.get("schema") == elo.SCHEMA_CONFIG_V3
    if is_v3:
        config = elo.validate_v3_config(config)
        preparation_config = elo.conditional_config_for_v3(config)
    else:
        preparation_config = elo.validate_v2_config(config)
    reference_dir = _resolve_from_root(
        args.reference_dir, str(preparation_config["derivedReferenceDirectory"])
    )
    output_dir = _resolve_from_root(
        args.output_dir, str(preparation_config["conditionalReferenceDirectory"])
    )
    directed_path = reference_dir / str(preparation_config["directedPhaseRecords"])
    reference_manifest_path = reference_dir / str(preparation_config["referenceManifest"])
    records = elo.reference_records_from_directory(reference_dir, config=preparation_config)
    manifest = elo.prepare_conditional_reference_v2(
        records,
        output_dir,
        config=preparation_config,
        input_records_path=directed_path,
        reference_manifest_sha256=(
            elo.sha256_file(reference_manifest_path)
            if reference_manifest_path.is_file() else None
        ),
        resume=bool(args.resume),
        maximum_records_per_pool=args.max_records_per_pool,
    )
    print(json.dumps({
        "status": "completed",
        "outputDirectory": str(output_dir.resolve()),
        "recordCount": manifest["recordCount"],
        "recordsSha256": manifest["recordsSha256"],
        "referenceModelContractSha256": elo.canonical_sha256(
            elo.v3_reference_model_contract(config)
        ) if is_v3 else None,
    }, ensure_ascii=False, indent=2))
    return 0


def command_calibrate(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    config = _config(config_path)
    reference_dir = _resolve_from_root(args.reference_dir, str(config["derivedReferenceDirectory"]))
    source_dir = _resolve_from_root(args.source_reference_dir, str(config["sourceReferenceDirectory"]))
    if config.get("schema") == elo.SCHEMA_CONFIG_V4:
        config = elo.validate_v4_config(config)
        reference_dir = _resolve_from_root(
            args.reference_dir, str(config["derivedReferenceDirectory"])
        )
        source_dir = _resolve_from_root(
            args.source_reference_dir, str(config["sourceReferenceDirectory"])
        )
        reference_cache_dir = _resolve_from_root(
            args.conditional_reference_dir, str(config["conditionalReferenceDirectory"])
        )
        output_dir = _resolve_from_root(
            args.output_dir, str(config["calibrationDirectory"])
        )
        records = elo.reference_records_from_directory(reference_dir, config=config)
        bundle = elo.read_json(source_dir / "selected_account_bundle.json")
        reference_records_path = reference_cache_dir / str(config["conditionalReferenceRecords"])
        reference_manifest_path = reference_cache_dir / str(config["conditionalReferenceManifest"])
        reference_records, reference_manifest, reference_cache_sha = elo.load_anscombe_reference_v4(
            reference_cache_dir,
            config=config,
            expected_reference_manifest_sha256=elo.sha256_file(
                reference_dir / str(config["referenceManifest"])
            ),
        )
        directed_records_path = reference_dir / str(config["directedPhaseRecords"])
        artifact, cases = elo.calibrate_global_interval_v4(
            records,
            bundle,
            reference_records,
            reference_manifest,
            output_dir,
            config=config,
            reference_records_path=reference_records_path,
            reference_manifest_path=reference_manifest_path,
            directed_records_sha256=elo.sha256_file(directed_records_path),
            resume=bool(args.resume),
            parallel_workers=16,
        )
        print(json.dumps({
            "calibration": artifact,
            "caseCount": len(cases),
            "outputDirectory": str(output_dir.resolve()),
            "algorithmVersion": elo.ALGORITHM_VERSION_V4,
            "referenceManifestSha256": reference_cache_sha,
        }, ensure_ascii=False, indent=2))
        return 0
    if config.get("schema") == elo.SCHEMA_CONFIG_V3:
        config = elo.validate_v3_config(config)
        conditional_dir = _resolve_from_root(
            args.conditional_reference_dir, str(config["conditionalReferenceDirectory"])
        )
        output_dir = _resolve_from_root(
            args.output_dir, str(config["calibrationDirectory"])
        )
        records = elo.reference_records_from_directory(reference_dir, config=config)
        bundle = elo.read_json(source_dir / "selected_account_bundle.json")
        conditional_records_path = conditional_dir / str(config["conditionalReferenceRecords"])
        conditional_manifest_path = conditional_dir / str(config["conditionalReferenceManifest"])
        conditional_records, conditional_manifest, _manifest_sha = elo.load_conditional_reference_v3(
            conditional_dir,
            config=config,
            expected_reference_manifest_sha256=elo.sha256_file(
                reference_dir / str(config["referenceManifest"])
            ),
        )
        directed_records_path = reference_dir / str(config["directedPhaseRecords"])
        artifact, cases = elo.calibrate_global_interval_v3(
            records,
            bundle,
            conditional_records,
            conditional_manifest,
            output_dir,
            config=config,
            conditional_records_path=conditional_records_path,
            conditional_manifest_path=conditional_manifest_path,
            directed_records_sha256=elo.sha256_file(directed_records_path),
            resume=bool(args.resume),
            parallel_workers=16,
        )
        print(json.dumps({
            "calibration": artifact,
            "caseCount": len(cases),
            "outputDirectory": str(output_dir.resolve()),
            "algorithmVersion": elo.ALGORITHM_VERSION_V3,
        }, ensure_ascii=False, indent=2))
        return 0
    if config.get("schema") == elo.SCHEMA_CONFIG_V2:
        conditional_dir = _resolve_from_root(
            args.conditional_reference_dir, str(config["conditionalReferenceDirectory"])
        )
        output_dir = _resolve_from_root(
            args.output_dir, str(config["calibrationDirectory"])
        )
        records = elo.reference_records_from_directory(reference_dir, config=config)
        bundle = elo.read_json(source_dir / "selected_account_bundle.json")
        conditional_records_path = conditional_dir / str(config["conditionalReferenceRecords"])
        conditional_manifest_path = conditional_dir / str(config["conditionalReferenceManifest"])
        conditional_records, conditional_manifest, _manifest_sha = elo.load_conditional_reference_v2(
            conditional_dir,
            config=config,
            expected_reference_manifest_sha256=elo.sha256_file(
                reference_dir / str(config["referenceManifest"])
            ),
        )
        directed_records_path = reference_dir / str(config["directedPhaseRecords"])
        artifact, cases = elo.calibrate_global_interval_v2(
            records,
            bundle,
            conditional_records,
            conditional_manifest,
            output_dir,
            config=config,
            conditional_records_path=conditional_records_path,
            conditional_manifest_path=conditional_manifest_path,
            directed_records_sha256=elo.sha256_file(directed_records_path),
            resume=bool(args.resume),
            parallel_workers=16,
        )
        print(json.dumps({
            "calibration": artifact,
            "caseCount": len(cases),
            "outputDirectory": str(output_dir.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0
    records = elo.reference_records_from_directory(reference_dir, config=config)
    bundle = elo.read_json(source_dir / "selected_account_bundle.json")
    directed_records_path = reference_dir / str(config.get("directedPhaseRecords") or "directed_game_phase_records.jsonl")
    artifact, cases = elo.calibrate_global_interval(
        records,
        bundle,
        config=config,
        reference_records_path=directed_records_path,
        parallel_workers=int(config.get("calibrationWorkers", elo.DEFAULT_CALIBRATION_WORKERS)),
    )
    calibration_path = reference_dir / str(config.get("calibrationArtifact") or "elo_calibration.json")
    cases_path = reference_dir / "elo_calibration_cases.jsonl"
    elo.write_json(calibration_path, artifact, refuse_existing=False)
    elo.calibration_cases_to_jsonl(cases_path, cases, refuse_existing=False)
    manifest = elo.update_reference_manifest(reference_dir, config=config)
    result = {
        "calibration": artifact,
        "caseCount": len(cases),
        "referenceManifest": manifest,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _empirical_quantile(values: list[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot calculate a quantile from an empty sample")
    position = (len(ordered) - 1) * float(probability)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return (
        ordered[lower] * (upper - position)
        + ordered[upper] * (position - lower)
    )


def command_audit_estimate_coverage(args: argparse.Namespace) -> int:
    """Audit all calibration users with account-level and threshold-level isolation."""

    config = _config(args.config.resolve())
    reference_dir = _resolve_from_root(
        args.reference_dir, str(config["derivedReferenceDirectory"])
    )
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")

    if config.get("schema") == elo.SCHEMA_CONFIG_V4:
        config = elo.validate_v4_config(config)
        calibration_dir = _resolve_from_root(
            args.calibration_dir, str(config["calibrationDirectory"])
        )
        calibration_path = calibration_dir / str(config["calibrationArtifact"])
        cases_path = calibration_dir / str(config["calibrationCases"])
        manifest_path = calibration_dir / "calibration_sha256_manifest_v4.json"
        calibration = elo.read_json(calibration_path)
        cases = elo.read_jsonl(cases_path)
        if calibration.get("schema") != elo.SCHEMA_CALIBRATION_V4:
            raise ValueError("audit requires a v4 calibration artifact")
        if elo.sha256_file(cases_path) != calibration.get("casesSha256"):
            raise ValueError("v4 calibration cases SHA-256 mismatch")
        if not manifest_path.is_file():
            raise FileNotFoundError(f"missing v4 calibration manifest: {manifest_path}")
        calibration_manifest = elo.read_json(manifest_path)
        if calibration_manifest.get("schema") != elo.SCHEMA_CALIBRATION_MANIFEST_V4:
            raise ValueError("unsupported v4 calibration SHA-256 manifest")
        for item in calibration_manifest.get("files", []):
            item_path = calibration_dir / str(item["path"])
            if not item_path.is_file() or elo.sha256_file(item_path) != item.get("sha256"):
                raise ValueError(f"sentinel Elo v4 calibration manifest mismatch: {item_path}")
        calibration_cases = [
            row for row in cases
            if row.get("role") == "calibration"
            and row.get("knownEloInFormalRange") is True
            and row.get("curveStatus") in {"valid", "multiple_minima", "above_reference_range", "below_reference_range"}
            and elo.finite_number(row.get("trueScoreIncrease"))
        ]
        recomputed_t95 = (
            _empirical_quantile(
                [float(row["trueScoreIncrease"]) for row in calibration_cases],
                float(config["calibrationCoverage"]),
            )
            if calibration_cases else None
        )
        stored_t95 = calibration.get("t95")
        if recomputed_t95 is not None and (
            not elo.finite_number(stored_t95)
            or not math.isclose(recomputed_t95, float(stored_t95), rel_tol=0.0, abs_tol=1e-12)
        ):
            raise ValueError("recomputed v4 global T95 does not match calibration artifact")
        validation = [row for row in cases if row.get("role") == "validation"]
        auditable_validation = [
            row for row in validation if row.get("knownEloInFormalRange") is True
        ]
        coverage_flags = [
            any(
                float(interval["lower"]) <= float(row["knownElo"]) <= float(interval["upper"])
                for interval in row.get("databaseCalibrated95Intervals") or []
            )
            for row in auditable_validation
        ]
        summary = {
            "schema": "player-sentinel-elo-coverage-audit-v4",
            "algorithmVersion": elo.ALGORITHM_VERSION_V4,
            "createdAt": elo.utc_now(),
            "ok": True,
            "calibrationStatus": calibration.get("status"),
            "calibrationSha256": elo.sha256_file(calibration_path),
            "calibrationCasesSha256": elo.sha256_file(cases_path),
            "calibrationManifestSha256": elo.sha256_file(manifest_path),
            "referenceModelContractSha256": calibration.get("referenceModelContractSha256"),
            "referenceManifestSha256": calibration.get("referenceManifestSha256"),
            "calibrationUserCount": calibration.get("calibrationUserCount"),
            "validationUserCount": len(validation),
            "auditableValidationUserCount": len(auditable_validation),
            "validationCoverage": sum(coverage_flags) / len(coverage_flags) if coverage_flags else None,
            "coverageConfirmed": (calibration.get("independentValidation") or {}).get("coverageConfirmed"),
            "storedT95": stored_t95,
            "recomputedT95": recomputed_t95,
            "pointErrorSummary": calibration.get("pointErrorSummary"),
            "absoluteErrorSummary": calibration.get("absoluteErrorSummary"),
            "validationIntervalWidthSummary": calibration.get("validationIntervalWidthSummary"),
            "boundaryHitCount": calibration.get("boundaryHitCount"),
            "fallbackToFullGridCount": sum(bool(row.get("fallbackToFullGrid")) for row in cases),
            "meanEvaluatedPointCount": (
                sum(int(row.get("evaluatedPointCount") or 0) for row in cases) / len(cases)
                if cases else None
            ),
            "searchStrategyVersion": calibration.get("searchStrategyVersion"),
            "searchSteps": calibration.get("searchSteps"),
            "workerContract": {
                "parallelWorkers": calibration.get("parallelWorkers"),
                "taskUnit": calibration.get("taskUnit"),
                "chunksize": calibration.get("processPoolChunksize"),
                "referenceQueryWorkersPerWorker": calibration.get("referenceQueryWorkersPerWorker"),
            },
            "failureCounts": {
                "taskFailureCount": calibration.get("taskFailureCount"),
                "accountStatisticsFailureCount": calibration.get("accountStatisticsFailureCount"),
                "modelInputFailureCount": calibration.get("modelInputFailureCount"),
                "failedCaseCount": calibration.get("failedCaseCount"),
            },
        }
        elo.write_json(output_dir / "estimate_coverage_audit_v4.json", summary)
        elo.write_csv(output_dir / "estimate_coverage_cases_v4.csv", cases)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if config.get("schema") == elo.SCHEMA_CONFIG_V3:
        config = elo.validate_v3_config(config)
        calibration_dir = _resolve_from_root(
            args.calibration_dir, str(config["calibrationDirectory"])
        )
        calibration_path = calibration_dir / str(config["calibrationArtifact"])
        cases_path = calibration_dir / str(config["calibrationCases"])
        manifest_path = calibration_dir / "calibration_sha256_manifest_v3.json"
        calibration = elo.read_json(calibration_path)
        cases = elo.read_jsonl(cases_path)
        if calibration.get("schema") != elo.SCHEMA_CALIBRATION_V3:
            raise ValueError("audit requires a v3 calibration artifact")
        if elo.sha256_file(cases_path) != calibration.get("casesSha256"):
            raise ValueError("v3 calibration cases SHA-256 mismatch")
        if manifest_path.is_file():
            calibration_manifest = elo.read_json(manifest_path)
            if calibration_manifest.get("schema") != elo.SCHEMA_CALIBRATION_MANIFEST_V3:
                raise ValueError("unsupported v3 calibration SHA-256 manifest")
            for item in calibration_manifest.get("files", []):
                item_path = calibration_dir / str(item["path"])
                if not item_path.is_file() or elo.sha256_file(item_path) != item.get("sha256"):
                    raise ValueError(f"sentinel Elo v3 calibration manifest mismatch: {item_path}")
        calibration_cases = [
            row for row in cases
            if row.get("role") == "calibration"
            and row.get("knownEloInFormalRange") is True
            and row.get("curveStatus") in {"valid", "multiple_minima"}
            and elo.finite_number(row.get("trueScoreIncrease"))
        ]
        t95 = None
        if calibration_cases:
            t95 = _empirical_quantile(
                [float(row["trueScoreIncrease"]) for row in calibration_cases],
                float(config["calibrationCoverage"]),
            )
            if not elo.finite_number(calibration.get("t95")) or not math.isclose(
                t95, float(calibration["t95"]), rel_tol=0.0, abs_tol=1e-12
            ):
                raise ValueError("recomputed v3 global T95 does not match the calibration artifact")
        validation = [row for row in cases if row.get("role") == "validation"]
        auditable_validation = [
            row for row in validation
            if row.get("knownEloInFormalRange") is True
        ]
        coverage_flags = [
            any(
                float(interval["lower"]) <= float(row["knownElo"]) <= float(interval["upper"])
                for interval in row.get("databaseCalibrated95Intervals") or []
            )
            for row in auditable_validation
        ]
        summary = {
            "schema": "player-sentinel-elo-coverage-audit-v3",
            "createdAt": elo.utc_now(),
            "ok": True,
            "calibrationStatus": calibration.get("status"),
            "calibrationSha256": elo.sha256_file(calibration_path),
            "calibrationCasesSha256": elo.sha256_file(cases_path),
            "calibrationManifestSha256": elo.sha256_file(manifest_path) if manifest_path.is_file() else None,
            "calibrationUserCount": calibration.get("calibrationUserCount"),
            "validationUserCount": len(validation),
            "auditableValidationUserCount": len(auditable_validation),
            "validationCoverage": (
                sum(coverage_flags) / len(coverage_flags) if coverage_flags else None
            ),
            "coverageConfirmed": (calibration.get("independentValidation") or {}).get("coverageConfirmed"),
            "storedT95": calibration.get("t95"),
            "recomputedT95": t95,
            "fallbackToFullGridCount": sum(bool(row.get("fallbackToFullGrid")) for row in cases),
            "fallbackToFullGridRate": (
                sum(bool(row.get("fallbackToFullGrid")) for row in cases) / len(cases)
                if cases else None
            ),
            "meanEvaluatedPointCount": (
                sum(int(row.get("evaluatedPointCount") or 0) for row in cases) / len(cases)
                if cases else None
            ),
            "searchStrategyVersion": calibration.get("searchStrategyVersion"),
            "searchSteps": calibration.get("searchSteps"),
            "workerContract": {
                "parallelWorkers": calibration.get("parallelWorkers"),
                "taskUnit": calibration.get("taskUnit"),
                "chunksize": calibration.get("processPoolChunksize"),
                "referenceQueryWorkersPerWorker": calibration.get("referenceQueryWorkersPerWorker"),
            },
            "leakageContract": {
                "knownEloUsedOnlyAfterEstimation": calibration.get("knownEloUsage"),
                "targetAccountSourceGamesExcludedBothDirections": True,
                "referenceFeaturePolicy": config.get("referenceFeaturePolicy"),
            },
        }
        elo.write_json(output_dir / "estimate_coverage_audit_v3.json", summary)
        elo.write_csv(output_dir / "estimate_coverage_cases_v3.csv", cases)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if config.get("schema") == elo.SCHEMA_CONFIG_V2:
        calibration_dir = _resolve_from_root(
            args.calibration_dir, str(config["calibrationDirectory"])
        )
        calibration_path = calibration_dir / str(config["calibrationArtifact"])
        cases_path = calibration_dir / str(config["calibrationCases"])
        calibration = elo.read_json(calibration_path)
        cases = elo.read_jsonl(cases_path)
        if calibration.get("schema") != elo.SCHEMA_CALIBRATION_V2:
            raise ValueError("audit requires a v2 calibration artifact")
        if elo.sha256_file(cases_path) != calibration.get("casesSha256"):
            raise ValueError("v2 calibration cases SHA-256 mismatch")
        validation = [row for row in cases if row.get("role") == "validation"]
        summary = {
            "schema": "player-sentinel-elo-coverage-audit-v2",
            "createdAt": elo.utc_now(),
            "ok": True,
            "calibrationStatus": calibration.get("status"),
            "calibrationSha256": elo.sha256_file(calibration_path),
            "calibrationCasesSha256": elo.sha256_file(cases_path),
            "validationUserCount": len(validation),
            "validationCoverage": calibration.get("validationCoverage"),
            "coverageConfirmed": (calibration.get("independentValidation") or {}).get("coverageConfirmed"),
            "validationErrorSummary": calibration.get("validationErrorSummary"),
            "validationIntervalWidthSummary": calibration.get("validationIntervalWidthSummary"),
            "groupMetrics": calibration.get("groupMetrics"),
            "workerContract": {
                "parallelWorkers": calibration.get("parallelWorkers"),
                "taskUnit": calibration.get("taskUnit"),
                "chunksize": calibration.get("processPoolChunksize"),
                "referenceQueryWorkersPerWorker": calibration.get("referenceQueryWorkersPerWorker"),
            },
            "leakageContract": {
                "knownEloUsedOnlyAfterEstimation": calibration.get("knownEloUsage"),
                "targetAccountSourceGamesExcludedBothDirections": True,
                "referenceFeaturePolicy": config.get("referenceFeaturePolicy"),
            },
        }
        elo.write_json(output_dir / "estimate_coverage_audit_v2.json", summary)
        elo.write_csv(output_dir / "estimate_coverage_cases_v2.csv", validation)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    calibration_path = reference_dir / str(
        config.get("calibrationArtifact") or "elo_calibration.json"
    )
    cases_path = reference_dir / str(
        config.get("calibrationCases") or "elo_calibration_cases.jsonl"
    )
    calibration = elo.read_json(calibration_path)
    cases = elo.read_jsonl(cases_path)
    if calibration.get("status") != "validated":
        raise ValueError("current calibration artifact is not validated")
    expected_case_count = int(calibration["calibrationUserCount"]) + int(
        calibration["validationUserCount"]
    )
    if len(cases) != expected_case_count:
        raise ValueError(
            f"calibration case count mismatch: expected {expected_case_count}, got {len(cases)}"
        )

    calibration_sources = {
        str(case["account"]): float(case["trueScoreIncrease"])
        for case in cases
        if case.get("role") == "calibration"
        and case.get("knownEloInFormalRange") is True
        and elo.finite_number(case.get("trueScoreIncrease"))
        and case.get("curveStatus") == "valid"
    }
    coverage = float(calibration["calibrationCoverage"])
    global_t95 = _empirical_quantile(list(calibration_sources.values()), coverage)
    if not math.isclose(global_t95, float(calibration["t95"]), rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("recomputed global T95 does not match the calibration artifact")

    rows: list[dict[str, Any]] = []
    for case in cases:
        account = str(case["account"])
        role = str(case["role"])
        if role == "calibration" and account in calibration_sources:
            threshold_values = [
                value for source_account, value in calibration_sources.items()
                if source_account != account
            ]
            threshold_method = "leave_this_account_out_of_calibration_t95"
        else:
            threshold_values = list(calibration_sources.values())
            threshold_method = "calibration_accounts_only_global_t95"
        account_safe_t95 = _empirical_quantile(threshold_values, coverage)

        has_point_estimate = (
            case.get("curveStatus") == "valid"
            and elo.finite_number(case.get("bestGridPoint"))
            and elo.finite_number(case.get("minimumScore"))
        )
        intervals = (
            elo.intervals_for_score_threshold(
                case.get("scoreCurve") or [],
                float(case["minimumScore"]) + account_safe_t95,
            )
            if has_point_estimate
            else []
        )
        has_single_interval = has_point_estimate and len(intervals) == 1
        interval = intervals[0] if has_single_interval else {}
        upper = interval.get("upper")
        latest_elo = float(case["knownElo"])
        latest_above_upper = (
            latest_elo > float(upper)
            if has_single_interval and elo.finite_number(upper)
            else None
        )
        rows.append({
            "account": account,
            "role": role,
            "selectedGameCount": int(case["selectedGameCount"]),
            "excludedReferenceGameCount": int(case["excludedReferenceGameCount"]),
            "latestElo": latest_elo,
            "latestEloDefinition": case.get("knownEloDefinition"),
            "curveStatus": case.get("curveStatus"),
            "hasPointEstimate": has_point_estimate,
            "estimatedElo": int(case["bestGridPoint"]) if has_point_estimate else None,
            "accountSafeT95": account_safe_t95,
            "thresholdMethod": threshold_method,
            "intervalCount": len(intervals),
            "databaseCalibrated95Intervals": intervals,
            "hasSingleInterval": has_single_interval,
            "rangeLower": interval.get("lower"),
            "rangeUpper": upper,
            "truncatedLower": interval.get("truncatedLower"),
            "truncatedUpper": interval.get("truncatedUpper"),
            "latestEloAboveUpper": latest_above_upper,
            "eloAboveUpperBy": (
                latest_elo - float(upper)
                if latest_above_upper is True else None
            ),
        })

    if len({row["account"] for row in rows}) != len(rows):
        raise ValueError("calibration cases contain duplicate accounts")
    minimum_games = int(config["minimumTargetGames"])
    if any(row["selectedGameCount"] < minimum_games for row in rows):
        raise ValueError("an audited account is below minimumTargetGames")

    point_rows = [row for row in rows if row["hasPointEstimate"]]
    interval_rows = [row for row in rows if row["hasSingleInterval"]]
    above_rows = [row for row in interval_rows if row["latestEloAboveUpper"] is True]
    above_reference_rows = [
        row for row in rows if row["curveStatus"] == "above_reference_range"
    ]
    valid_or_above_reference_rows = point_rows + above_reference_rows
    validation_interval_rows = [
        row for row in interval_rows if row["role"] == "validation"
    ]
    validation_above_rows = [
        row for row in validation_interval_rows if row["latestEloAboveUpper"] is True
    ]
    curve_status_counts = Counter(str(row["curveStatus"]) for row in rows)
    summary = {
        "schema": "player-sentinel-elo-leakage-safe-coverage-audit-v1",
        "createdAt": elo.utc_now(),
        "ok": True,
        "referenceDirectory": str(reference_dir.resolve()),
        "referenceVersion": reference_dir.name,
        "calibrationSha256": elo.sha256_file(calibration_path),
        "calibrationCasesSha256": elo.sha256_file(cases_path),
        "minimumTargetGames": minimum_games,
        "maximumTargetGames": int(config["maximumTargetGames"]),
        "eligiblePlayerCount": len(rows),
        "pointEstimatePlayerCount": len(point_rows),
        "singleIntervalPlayerCount": len(interval_rows),
        "pointEstimateWithoutSingleUpperCount": len(point_rows) - len(interval_rows),
        "aboveReferenceRangePlayerCount": len(above_reference_rows),
        "aboveReferenceRangeRateAmongAllEligiblePlayers": (
            len(above_reference_rows) / len(rows) if rows else None
        ),
        "aboveReferenceRangeRateAmongValidOrAboveRangePlayers": (
            len(above_reference_rows) / len(valid_or_above_reference_rows)
            if valid_or_above_reference_rows else None
        ),
        "latestEloAboveUpperCount": len(above_rows),
        "latestEloAboveUpperRateAmongSingleIntervalEstimates": (
            len(above_rows) / len(interval_rows) if interval_rows else None
        ),
        "latestEloAboveUpperRateAmongPointEstimates": (
            len(above_rows) / len(point_rows) if point_rows else None
        ),
        "validationSingleIntervalPlayerCount": len(validation_interval_rows),
        "validationLatestEloAboveUpperCount": len(validation_above_rows),
        "validationLatestEloAboveUpperRate": (
            len(validation_above_rows) / len(validation_interval_rows)
            if validation_interval_rows else None
        ),
        "curveStatusCounts": dict(sorted(curve_status_counts.items())),
        "calibrationCoverage": coverage,
        "globalT95": global_t95,
        "workerContract": {
            "calibrationWorkers": calibration.get("parallelWorkers"),
            "referenceQueryWorkersPerWorker": calibration.get(
                "referenceQueryWorkersPerWorker"
            ),
            "reusedExistingScoreCurves": True,
        },
        "leakageControls": {
            "referenceGameIsolation": (
                "existing calibration score curves exclude all reference gameIds "
                "belonging to the estimated account"
            ),
            "validationThresholdIsolation": (
                "validation accounts use T95 derived only from calibration accounts"
            ),
            "calibrationThresholdIsolation": (
                "each calibration account uses a recomputed T95 excluding that account"
            ),
        },
        "latestEloDefinition": calibration.get("knownEloDefinition"),
        "aboveUpperPlayers": sorted(
            above_rows,
            key=lambda row: float(row["eloAboveUpperBy"]),
            reverse=True,
        ),
        "caseCsv": "leakage_safe_estimate_cases.csv",
        "aboveUpperCsv": "latest_elo_above_upper.csv",
        "aboveReferenceRangeCsv": "above_reference_range_players.csv",
    }
    elo.write_json(output_dir / "leakage_safe_estimate_audit.json", summary)
    elo.write_csv(output_dir / "leakage_safe_estimate_cases.csv", rows)
    elo.write_csv(
        output_dir / "latest_elo_above_upper.csv",
        summary["aboveUpperPlayers"],
    )
    elo.write_csv(
        output_dir / "above_reference_range_players.csv",
        sorted(
            above_reference_rows,
            key=lambda row: float(row["latestElo"]),
            reverse=True,
        ),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _write_estimate_outputs(
    output_dir: Path,
    payload: dict[str, Any],
    curve: dict[str, Any],
) -> None:
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
    )
    elo.write_csv(output_dir / games_name, payload.get("gameDiagnostics", []))
    elo.write_csv(output_dir / phase_name, payload.get("phaseDiagnostics", []))
    payload["gamesFile"] = games_name
    payload["phaseDiagnosticsFile"] = phase_name
    elo.write_json(output_dir / "estimated_elo.json", payload)


def command_estimate(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    config = _config(config_path)
    reference_dir = _resolve_from_root(args.reference_dir, str(config["derivedReferenceDirectory"]))
    if config.get("schema") == elo.SCHEMA_CONFIG_V4:
        config = elo.validate_v4_config(config)
        conditional_dir = _resolve_from_root(
            args.conditional_reference_dir, str(config["conditionalReferenceDirectory"])
        )
        calibration_dir = _resolve_from_root(
            args.calibration_dir, str(config["calibrationDirectory"])
        )
        reference_manifest_path = reference_dir / str(config["referenceManifest"])
        reference_manifest_sha = elo.sha256_file(reference_manifest_path)
        reference_records, reference_manifest, reference_cache_sha = elo.load_anscombe_reference_v4(
            conditional_dir,
            config=config,
            expected_reference_manifest_sha256=reference_manifest_sha,
        )
        calibration_path = (
            args.calibration.resolve()
            if args.calibration else calibration_dir / str(config["calibrationArtifact"])
        )
        calibration = elo.read_json(calibration_path) if calibration_path.is_file() else None
        target_records = elo.target_records_from_inputs(
            args.bundle.resolve(), args.engine_dir.resolve(),
            args.offbook_records.resolve(), args.account, config=config,
        )
        source_bundle = elo.read_json(args.bundle.resolve())
        known_elo = elo._latest_known_elos(source_bundle).get(elo.account_key(args.account))
        estimate = elo.estimate_database_calibrated_range_v4(
            args.account,
            target_records,
            reference_records,
            reference_manifest,
            config=config,
            calibration=calibration,
            reference_manifest_sha256=reference_cache_sha,
            calibration_sha256=(elo.sha256_file(calibration_path) if calibration_path.is_file() else None),
            known_elo=known_elo,
            reference_records_path=(
                conditional_dir / str(config["conditionalReferenceRecords"])
            ),
        )
        _write_estimate_outputs(args.output_dir.resolve(), estimate.payload, estimate.curve)
        print(json.dumps({
            "account": args.account,
            "algorithmVersion": elo.ALGORITHM_VERSION_V4,
            "status": estimate.payload.get("status"),
            "estimatedElo": estimate.payload.get("estimatedElo"),
            "databaseCalibrated95Intervals": estimate.payload.get("databaseCalibrated95Intervals"),
            "selectedGameCount": estimate.payload.get("selectedGameCount"),
            "evaluatedPointCount": estimate.payload.get("evaluatedPointCount"),
            "fallbackToFullGrid": estimate.payload.get("fallbackToFullGrid"),
        }, ensure_ascii=False, indent=2))
        return 0
    if config.get("schema") == elo.SCHEMA_CONFIG_V3:
        config = elo.validate_v3_config(config)
        conditional_dir = _resolve_from_root(
            args.conditional_reference_dir, str(config["conditionalReferenceDirectory"])
        )
        calibration_dir = _resolve_from_root(
            args.calibration_dir, str(config["calibrationDirectory"])
        )
        reference_manifest_path = reference_dir / str(config["referenceManifest"])
        reference_manifest_sha = elo.sha256_file(reference_manifest_path)
        conditional_records, conditional_manifest, conditional_manifest_sha = elo.load_conditional_reference_v3(
            conditional_dir,
            config=config,
            expected_reference_manifest_sha256=reference_manifest_sha,
        )
        calibration_path = (
            args.calibration.resolve()
            if args.calibration else calibration_dir / str(config["calibrationArtifact"])
        )
        calibration = elo.read_json(calibration_path) if calibration_path.is_file() else None
        target_records = elo.target_records_from_inputs(
            args.bundle.resolve(), args.engine_dir.resolve(),
            args.offbook_records.resolve(), args.account, config=config,
        )
        source_bundle = elo.read_json(args.bundle.resolve())
        known_elo = elo._latest_known_elos(source_bundle).get(elo.account_key(args.account))
        estimate = elo.estimate_database_calibrated_range_v3(
            args.account,
            target_records,
            conditional_records,
            conditional_manifest,
            config=config,
            calibration=calibration,
            conditional_manifest_sha256=conditional_manifest_sha,
            calibration_sha256=(elo.sha256_file(calibration_path) if calibration_path.is_file() else None),
            known_elo=known_elo,
        )
        _write_estimate_outputs(args.output_dir.resolve(), estimate.payload, estimate.curve)
        print(json.dumps({
            "account": args.account,
            "status": estimate.payload.get("status"),
            "estimatedElo": estimate.payload.get("estimatedElo"),
            "databaseCalibrated95Intervals": estimate.payload.get("databaseCalibrated95Intervals"),
            "selectedGameCount": estimate.payload.get("selectedGameCount"),
            "evaluatedPointCount": estimate.payload.get("evaluatedPointCount"),
            "fallbackToFullGrid": estimate.payload.get("fallbackToFullGrid"),
        }, ensure_ascii=False, indent=2))
        return 0
    if config.get("schema") == elo.SCHEMA_CONFIG_V2:
        conditional_dir = _resolve_from_root(
            args.conditional_reference_dir, str(config["conditionalReferenceDirectory"])
        )
        calibration_dir = _resolve_from_root(
            args.calibration_dir, str(config["calibrationDirectory"])
        )
        reference_manifest_path = reference_dir / str(config["referenceManifest"])
        reference_manifest_sha = elo.sha256_file(reference_manifest_path)
        conditional_records, conditional_manifest, conditional_manifest_sha = elo.load_conditional_reference_v2(
            conditional_dir,
            config=config,
            expected_reference_manifest_sha256=reference_manifest_sha,
        )
        calibration_path = (
            args.calibration.resolve()
            if args.calibration else calibration_dir / str(config["calibrationArtifact"])
        )
        calibration = elo.read_json(calibration_path) if calibration_path.is_file() else None
        target_records = elo.target_records_from_inputs(
            args.bundle.resolve(), args.engine_dir.resolve(),
            args.offbook_records.resolve(), args.account, config=config,
        )
        estimate = elo.estimate_database_calibrated_range_v2(
            args.account,
            target_records,
            conditional_records,
            conditional_manifest,
            config=config,
            calibration=calibration,
            conditional_manifest_sha256=conditional_manifest_sha,
            calibration_sha256=(elo.sha256_file(calibration_path) if calibration_path.is_file() else None),
        )
        _write_estimate_outputs(args.output_dir.resolve(), estimate.payload, estimate.curve)
        print(json.dumps({
            "account": args.account,
            "status": estimate.payload.get("status"),
            "estimatedElo": estimate.payload.get("estimatedElo"),
            "databaseCalibrated95Intervals": estimate.payload.get("databaseCalibrated95Intervals"),
            "selectedGameCount": estimate.payload.get("selectedGameCount"),
        }, ensure_ascii=False, indent=2))
        return 0

    records = elo.reference_records_from_directory(reference_dir, config=config)
    calibration_path = args.calibration.resolve() if args.calibration else reference_dir / str(config.get("calibrationArtifact") or "elo_calibration.json")
    calibration = elo.read_json(calibration_path) if calibration_path.is_file() else None
    target_records = elo.target_records_from_inputs(
        args.bundle.resolve(),
        args.engine_dir.resolve(),
        args.offbook_records.resolve(),
        args.account,
        config=config,
    )
    reference_manifest_path = reference_dir / str(config.get("referenceManifest") or "reference_sha256_manifest.json")
    reference_manifest_sha = elo.sha256_file(reference_manifest_path) if reference_manifest_path.is_file() else None
    calibration_version = elo.sha256_file(calibration_path) if calibration_path.is_file() else None
    estimate = elo.estimate_database_calibrated_range(
        args.account,
        target_records,
        records,
        config=config,
        calibration=calibration,
        reference_version=reference_dir.name,
        reference_manifest_sha256=reference_manifest_sha,
        calibration_version=calibration_version,
    )
    _write_estimate_outputs(args.output_dir.resolve(), estimate.payload, estimate.curve)
    print(json.dumps({
        "account": args.account,
        "status": estimate.payload.get("status"),
        "estimatedElo": estimate.payload.get("estimatedElo"),
        "databaseCalibrated95Intervals": estimate.payload.get("databaseCalibrated95Intervals"),
        "selectedGameCount": estimate.payload.get("selectedGameCount"),
    }, ensure_ascii=False, indent=2))
    return 0


def command_audit_reference_z_sensitivity(args: argparse.Namespace) -> int:
    """Compare the unified cache with an exact account-exclusion rebuild."""

    raw_config = _config(args.config.resolve())
    if raw_config.get("schema") == elo.SCHEMA_CONFIG_V4:
        raise ValueError(
            "reference-z sensitivity rebuild is a legacy v2 diagnostic; "
            "v4 requires one frozen Anscombe cache and uses audit-knn-consistency"
        )
    config = elo.validate_v2_config(raw_config)
    reference_dir = _resolve_from_root(
        args.reference_dir, str(config["derivedReferenceDirectory"])
    )
    conditional_dir = _resolve_from_root(
        args.conditional_reference_dir, str(config["conditionalReferenceDirectory"])
    )
    rebuild_dir = args.rebuild_dir.resolve()
    output_dir = args.output_dir.resolve()
    report_path = output_dir / "reference_z_sensitivity.json"
    if report_path.exists() and not args.resume:
        raise FileExistsError(f"sensitivity report already exists: {report_path}")

    reference_manifest_path = reference_dir / str(config["referenceManifest"])
    reference_manifest_sha = elo.sha256_file(reference_manifest_path)
    directed_records = elo.reference_records_from_directory(reference_dir, config=config)
    unified_records, unified_manifest, unified_manifest_sha = elo.load_conditional_reference_v2(
        conditional_dir,
        config=config,
        expected_reference_manifest_sha256=reference_manifest_sha,
    )
    target_records = elo.target_records_from_inputs(
        args.bundle.resolve(), args.engine_dir.resolve(),
        args.offbook_records.resolve(), args.account, config=config,
    )
    calibration = None
    calibration_sha = None
    if args.calibration is not None and args.calibration.resolve().is_file():
        calibration = elo.read_json(args.calibration.resolve())
        calibration_sha = elo.sha256_file(args.calibration.resolve())
    unified_estimate = elo.estimate_database_calibrated_range_v2(
        args.account,
        target_records,
        unified_records,
        unified_manifest,
        config=config,
        calibration=calibration,
        conditional_manifest_sha256=unified_manifest_sha,
        calibration_sha256=calibration_sha,
    )

    _rebuilt_manifest, rebuild_audit = elo.rebuild_conditional_reference_for_account_v2(
        directed_records,
        args.account,
        rebuild_dir,
        config=config,
        reference_manifest_sha256=reference_manifest_sha,
        resume=bool(args.resume),
    )
    rebuilt_records, rebuilt_manifest, rebuilt_manifest_sha = elo.load_conditional_reference_v2(
        rebuild_dir,
        config=config,
        expected_reference_manifest_sha256=reference_manifest_sha,
    )
    # A formal calibration artifact is bound to the unified manifest and must
    # not be presented as calibrating this counterfactual rebuilt cache.
    rebuilt_estimate = elo.estimate_database_calibrated_range_v2(
        args.account,
        target_records,
        rebuilt_records,
        rebuilt_manifest,
        config=config,
        calibration=None,
        conditional_manifest_sha256=rebuilt_manifest_sha,
    )
    comparison = elo.reference_z_sensitivity_comparison_v2(
        args.account,
        unified_estimate,
        rebuilt_estimate,
        unified_manifest_sha256=unified_manifest_sha,
        rebuilt_manifest_sha256=rebuilt_manifest_sha,
        rebuild_audit=rebuild_audit,
    )
    comparison["calibrationHandling"] = (
        "formal calibration is used only for the unified estimate; rebuilt-cache intervals "
        "remain unavailable because that cache has a different manifest SHA-256"
    )
    elo.atomic_write_json(report_path, comparison)
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return 0


def command_audit_knn_consistency(args: argparse.Namespace) -> int:
    """Compare reusable global-tree queries with exact account-exclusion queries."""

    raw_config = _config(args.config.resolve())
    if raw_config.get("schema") == elo.SCHEMA_CONFIG_V4:
        config = elo.validate_v4_config(raw_config)
        conditional_dir = _resolve_from_root(
            args.conditional_reference_dir, str(config["conditionalReferenceDirectory"])
        )
        conditional_records, conditional_manifest, _manifest_sha = elo.load_anscombe_reference_v4(
            conditional_dir, config=config
        )
        samples = elo.read_json(args.samples.resolve())
        if not isinstance(samples, list):
            raise ValueError("KNN consistency samples must be a JSON array")
        report = elo.audit_global_knn_consistency_v4(
            conditional_records, conditional_manifest, config, samples
        )
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        elo.atomic_write_json(output_dir / "global_knn_consistency_v4.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    config = elo.validate_v3_config(raw_config)
    conditional_dir = _resolve_from_root(
        args.conditional_reference_dir, str(config["conditionalReferenceDirectory"])
    )
    conditional_records, conditional_manifest, _manifest_sha = elo.load_conditional_reference_v3(
        conditional_dir, config=config
    )
    samples = elo.read_json(args.samples.resolve())
    if not isinstance(samples, list):
        raise ValueError("KNN consistency samples must be a JSON array")
    report = elo.audit_global_knn_consistency_v3(
        conditional_records, conditional_manifest, config, samples
    )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    elo.atomic_write_json(output_dir / "global_knn_consistency_v3.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help="UTF-8 Elo reference configuration JSON",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-elo-reference", help="derive phase records from frozen Level22 files")
    build.add_argument("--source-reference-dir", type=str)
    build.add_argument("--sentinel-derived-dir", type=str)
    build.add_argument("--output-dir", type=str)
    build.set_defaults(handler=command_build)

    prepare = subparsers.add_parser(
        "prepare-conditional-elo-reference",
        help="build the resumable Anscombe y/v and reference z1/z2/z3/z4 cache",
    )
    prepare.add_argument("--reference-dir", type=str)
    prepare.add_argument("--output-dir", type=str)
    prepare.add_argument("--resume", action="store_true")
    prepare.add_argument("--max-records-per-pool", type=int, help="smoke-only deterministic pool cap")
    prepare.set_defaults(handler=command_prepare_conditional)

    calibrate = subparsers.add_parser("calibrate-elo", help="build leave-one-account-out calibration artifacts")
    calibrate.add_argument("--reference-dir", type=str)
    calibrate.add_argument("--source-reference-dir", type=str)
    calibrate.add_argument("--conditional-reference-dir", type=str)
    calibrate.add_argument("--output-dir", type=str)
    calibrate.add_argument("--resume", action="store_true")
    calibrate.set_defaults(handler=command_calibrate)

    audit = subparsers.add_parser(
        "audit-estimate-coverage",
        help="audit all >=minimumTargetGames calibration users without account leakage",
    )
    audit.add_argument("--reference-dir", type=str)
    audit.add_argument("--calibration-dir", type=str)
    audit.add_argument("--output-dir", type=Path, required=True)
    audit.set_defaults(handler=command_audit_estimate_coverage)

    estimate = subparsers.add_parser("estimate-elo", help="estimate one account from recent target Level22 records")
    estimate.add_argument("--account", required=True)
    estimate.add_argument("--bundle", type=Path, required=True)
    estimate.add_argument("--engine-dir", type=Path, required=True)
    estimate.add_argument("--offbook-records", type=Path, required=True)
    estimate.add_argument("--reference-dir", type=str)
    estimate.add_argument("--conditional-reference-dir", type=str)
    estimate.add_argument("--calibration-dir", type=str)
    estimate.add_argument("--calibration", type=Path)
    estimate.add_argument("--output-dir", type=Path, required=True)
    estimate.set_defaults(handler=command_estimate)

    sensitivity = subparsers.add_parser(
        "audit-reference-z-sensitivity",
        help="legacy v2-only account-exclusion rebuild diagnostic",
    )
    sensitivity.add_argument("--account", required=True)
    sensitivity.add_argument("--bundle", type=Path, required=True)
    sensitivity.add_argument("--engine-dir", type=Path, required=True)
    sensitivity.add_argument("--offbook-records", type=Path, required=True)
    sensitivity.add_argument("--reference-dir", type=str)
    sensitivity.add_argument("--conditional-reference-dir", type=str)
    sensitivity.add_argument("--rebuild-dir", type=Path, required=True)
    sensitivity.add_argument("--calibration", type=Path)
    sensitivity.add_argument("--output-dir", type=Path, required=True)
    sensitivity.add_argument("--resume", action="store_true")
    sensitivity.set_defaults(handler=command_audit_reference_z_sensitivity)

    knn_audit = subparsers.add_parser(
        "audit-knn-consistency",
        help="compare reusable global KNN queries with exact account-exclusion results",
    )
    knn_audit.add_argument("--conditional-reference-dir", type=str)
    knn_audit.add_argument("--samples", type=Path, required=True)
    knn_audit.add_argument("--output-dir", type=Path, required=True)
    knn_audit.set_defaults(handler=command_audit_knn_consistency)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
