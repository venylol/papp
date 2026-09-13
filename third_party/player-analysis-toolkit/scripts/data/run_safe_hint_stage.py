from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


FIXED_STAGE_CONTRACT = {
    "hint1": {"level": 2, "threads": 1, "useBook": False, "count": 1},
    "hint6": {"level": 18, "threads": 16, "useBook": True, "count": 6},
}


def existing_file(value: Path, label: str) -> Path:
    path = value.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def run_command(command: list[str], environment: dict[str, str]) -> None:
    completed = subprocess.run(command, env=environment, check=False)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, command)


def validate_audit(output_dir: Path, stage: str, expected: int, workers: int, hash_level: int) -> dict:
    manifest_path = output_dir / "run_manifest.json"
    audit_path = output_dir / "audit.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    fixed = FIXED_STAGE_CONTRACT[stage]
    expected_manifest = {
        "stage": stage,
        "workerCount": workers,
        "threads": fixed["threads"],
        "hashLevel": hash_level,
        "level": fixed["level"],
        "count": fixed["count"],
        "use_book": fixed["useBook"],
    }
    for key, expected_value in expected_manifest.items():
        if manifest.get(key) != expected_value:
            raise ValueError(f"safe {stage} manifest {key} mismatch: {manifest.get(key)!r} != {expected_value!r}")
    if (
        not bool(audit.get("ok"))
        or int(audit.get("rows") or 0) != expected
        or int(audit.get("boardMismatches") or 0) != 0
        or int(audit.get("legalityOrCompletenessErrors") or 0) != 0
    ):
        raise ValueError(f"safe {stage} full audit gate failed: {audit_path}")
    return {
        "schema": "player-investigation-safe-hint-stage-v1",
        "status": "completed",
        "stage": stage,
        "expectedPlacements": expected,
        "workerCount": workers,
        "threadsPerConsole": fixed["threads"],
        "engineLevel": fixed["level"],
        "hashLevel": hash_level,
        "hintCount": fixed["count"],
        "useBook": fixed["useBook"],
        "manifest": str(manifest_path),
        "audit": str(audit_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one safe hint stage and require the same full-audit gate as the server workflow."
    )
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--source-csv", type=Path, required=True)
    parser.add_argument("--stage", choices=tuple(FIXED_STAGE_CONTRACT), required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--hash-level", type=int, default=25)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workers <= 0 or args.batch_size <= 0 or args.timeout <= 0 or args.max_attempts <= 0:
        raise ValueError("workers, batch size, timeout, and max attempts must be positive")
    if args.expected <= 0:
        raise ValueError("--expected must be positive")
    runner = existing_file(args.runner, "safe runner")
    source = existing_file(args.source_csv, "source CSV")
    engine = existing_file(args.engine, "engine")
    output = args.output_dir.resolve()
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    command = [
        sys.executable,
        str(runner),
        "run",
        "--source-csv",
        str(source),
        "--stage",
        args.stage,
        "--engine",
        str(engine),
        "--output-dir",
        str(output),
        "--workers",
        str(args.workers),
        "--hash-level",
        str(args.hash_level),
        "--batch-size",
        str(args.batch_size),
        "--timeout",
        str(args.timeout),
        "--max-attempts",
        str(args.max_attempts),
    ]
    if args.resume:
        command.append("--resume")
    run_command(command, environment)
    run_command(
        [sys.executable, str(runner), "audit", "--output-dir", str(output), "--expected", str(args.expected)],
        environment,
    )
    result = validate_audit(output, args.stage, args.expected, args.workers, args.hash_level)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
