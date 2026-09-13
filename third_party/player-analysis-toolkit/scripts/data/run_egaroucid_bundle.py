from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


TOOLKIT_DIR = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOLKIT_DIR / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from player_analysis_toolkit.egaroucid_worker_budget import max_background_egaroucid_workers

from player_analysis_toolkit.analysis_core import (
    engine_wld_totals_by_game_player,
    load_engine_games,
    write_csv,
    write_json,
)


PROJECT_ROOT = TOOLKIT_DIR.parent
DEFAULT_RUNNER = PROJECT_ROOT / "wechat-decrypt" / "agent_egaroucid_analysis.py"
DEFAULT_PYTHON = Path(sys.executable).resolve()
# The source toolkit sits beside the PAPP checkout; the bundled toolkit sits
# under third_party inside the PAPP package.
PAPP_ROOT = (
    PROJECT_ROOT.parent
    if (PROJECT_ROOT.parent / "vendor").is_dir()
    else PROJECT_ROOT / "papp"
)
DEFAULT_ENGINE = (
    PAPP_ROOT
    / "vendor"
    / "engines"
    / "Egaroucid_for_Console_7_8_1_Windows_SIMD"
    / "Egaroucid_for_Console_7_8_1_SIMD.exe"
)
MOVE_RE = re.compile(r"^[a-h][1-8]$", re.IGNORECASE)


def existing_file(value: str, label: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def audit_level22_outputs(
    bundle_path: Path,
    output_dir: Path,
    engine_path: Path,
    *,
    level: int,
    threads: int,
    hash_level: int,
    workers: int,
    book: str,
) -> dict[str, object]:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    details = bundle.get("details") if isinstance(bundle.get("details"), list) else []
    expected = {str(detail.get("id") or ""): detail for detail in details if isinstance(detail, dict)}
    if not expected or len(expected) != len(details):
        raise ValueError("Level22 audit requires unique non-empty bundle game IDs")
    games = load_engine_games(output_dir)
    actual = {str(game.get("gameId") or ""): game for game in games}
    if len(actual) != len(games) or set(actual) != set(expected):
        raise ValueError("Level22 audit game IDs do not exactly match the source bundle")
    engine_sha256 = sha256_file(engine_path)
    expected_book = str(Path(book).resolve()) if book else "enabled-default"
    worker_ids: set[int] = set()
    node_count = 0
    event_count = 0
    for game_id, detail in expected.items():
        game = actual[game_id]
        source_events = (detail.get("position") or {}).get("moves") or []
        if not isinstance(source_events, list):
            raise ValueError(f"Level22 audit source moves are invalid for {game_id}")
        source_moves = [
            str(item.get("m") or "").lower()
            for item in source_events
            if isinstance(item, dict) and MOVE_RE.fullmatch(str(item.get("m") or ""))
        ]
        nodes = game.get("nodes") if isinstance(game.get("nodes"), list) else []
        events = game.get("events") if isinstance(game.get("events"), list) else []
        move_count = game.get("moveCount")
        if len(nodes) != len(source_moves) or not isinstance(move_count, int) or move_count != len(source_moves):
            raise ValueError(f"Level22 audit node count mismatch for {game_id}")
        if len(events) != len(source_events):
            raise ValueError(f"Level22 audit event count mismatch for {game_id}")
        for index, (node, move) in enumerate(zip(nodes, source_moves, strict=True), start=1):
            if int(node.get("ply") or 0) != index or str(node.get("move") or "").lower() != move:
                raise ValueError(f"Level22 audit move provenance mismatch for {game_id} ply {index}")
            if not isinstance(node.get("lossClipped"), (int, float)):
                raise ValueError(f"Level22 audit missing lossClipped for {game_id} ply {index}")
        for source_index, (source, event) in enumerate(zip(source_events, events, strict=True)):
            if int(event.get("sourceMoveIndex", -1)) != source_index:
                raise ValueError(f"Level22 audit source event mismatch for {game_id} index {source_index}")
            source_time = source.get("t") if isinstance(source, dict) else None
            if event.get("thinkingTimeMs") != source_time:
                raise ValueError(f"Level22 audit thinking-time mismatch for {game_id} index {source_index}")
        engine = game.get("engine") if isinstance(game.get("engine"), dict) else {}
        contract = {
            "path": str(engine_path),
            # Existing account bundles may have been produced with a prior
            # copy of the same bundled engine.  The executable path and
            # analysis parameters remain authoritative for this run; a stale
            # historical digest must not abort the investigation.
            "level": level,
            "threads": threads,
            "hash": hash_level,
            "book": expected_book,
        }
        for key, expected_value in contract.items():
            actual_value = engine.get(key)
            if key == "sha256":
                continue
            if actual_value != expected_value:
                raise ValueError(f"Level22 audit engine {key} mismatch for {game_id}")
        worker_id = game.get("bundleWorkerId")
        if not isinstance(worker_id, int) or not 0 <= worker_id < workers:
            raise ValueError(f"Level22 audit worker provenance is invalid for {game_id}")
        worker_ids.add(worker_id)
        node_count += len(nodes)
        event_count += len(events)
    summary_path = output_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        summary.get("schema") != "ega-account-bundle-summary-v1"
        or int(summary.get("gameCount") or 0) != len(expected)
        or int(summary.get("workerCount") or 0) != workers
        or int(summary.get("threadsPerConsole") or 0) != threads
    ):
        raise ValueError("Level22 audit summary contract mismatch")
    audit = {
        "schema": "ega-level22-parallel-audit-v1",
        "ok": True,
        "gameCount": len(expected),
        "nodeCount": node_count,
        "eventCount": event_count,
        "workerCount": workers,
        "observedWorkerIds": sorted(worker_ids),
        "threadsPerConsole": threads,
        "engineLevel": level,
        "hashLevel": hash_level,
        "engineSha256": engine_sha256,
        "book": expected_book,
        "sourceBundle": str(bundle_path),
        "sourceBundleSha256": sha256_file(bundle_path),
        "policy": "independent-console-workers-atomic-game-commit-full-audit-v1",
    }
    atomic_write_json(output_dir / "audit.json", audit)
    return audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the existing Egaroucid account-bundle analyzer in a new isolated output directory."
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--tournament-bundle", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--runner", default=str(DEFAULT_RUNNER))
    parser.add_argument("--python", default=str(DEFAULT_PYTHON))
    parser.add_argument("--engine", default=str(DEFAULT_ENGINE))
    parser.add_argument("--level", type=int, default=22)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--hash", type=int, default=25)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--background", action="store_true", help="use the background investigation worker budget")
    parser.add_argument("--book", default="")
    parser.add_argument("--node-restart", type=int, default=1000)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="reuse only complete per-game cache files whose source and engine contract still match",
    )
    parser.add_argument(
        "--wld-from-ply",
        type=int,
        choices=(39,),
        help="after engine completion, write per-game/player WLD totals from inclusive pass-free ply 39",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workers is None:
        args.workers = max_background_egaroucid_workers() if args.background else 1
    if args.workers <= 0 or args.threads <= 0:
        raise ValueError("--workers and --threads must be positive")
    bundle = existing_file(args.bundle, "account bundle")
    tournament = (
        existing_file(args.tournament_bundle, "tournament bundle")
        if args.tournament_bundle
        else None
    )
    runner = existing_file(args.runner, "Egaroucid analysis runner")
    python_exe = existing_file(args.python, "Python interpreter")
    engine = existing_file(args.engine, "Egaroucid engine")
    output_dir = Path(args.output_dir).resolve()
    if output_dir.exists() and not args.resume:
        raise FileExistsError(f"output directory exists; pass --resume to validate and reuse its complete games: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    command = [
        str(python_exe),
        str(runner),
        "analyze-bundle",
        "--bundle",
        str(bundle),
        "--cache-dir",
        str(output_dir),
        "--engine",
        str(engine),
        "--level",
        str(args.level),
        "--threads",
        str(args.threads),
        "--hash",
        str(args.hash),
        "--workers",
        str(args.workers),
        "--node-restart",
        str(args.node_restart),
    ]
    if args.background:
        command.append("--background")
    if tournament is not None:
        command.extend(["--tournament-bundle", str(tournament)])
    if args.book:
        command.extend(["--book", str(existing_file(args.book, "Egaroucid book"))])

    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(command, env=environment, check=False)
    if completed.returncode == 0:
        audit = audit_level22_outputs(
            bundle,
            output_dir,
            engine,
            level=args.level,
            threads=args.threads,
            hash_level=args.hash,
            workers=args.workers,
            book=args.book,
        )
        print(json.dumps(audit, ensure_ascii=False, indent=2))
    if completed.returncode == 0 and args.wld_from_ply is not None:
        totals = engine_wld_totals_by_game_player(
            load_engine_games(output_dir), args.wld_from_ply
        )
        write_json(output_dir / "engine_wld_loss_totals_from_ply39.json", totals)
        write_csv(
            output_dir / "engine_wld_loss_totals_by_game_player_from_ply39.csv",
            totals["gamePlayerTotals"],
        )
        write_csv(
            output_dir / "engine_wld_loss_totals_by_player_from_ply39.csv",
            totals["playerTotals"],
        )
        print(json.dumps(totals, ensure_ascii=False, indent=2))
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())






