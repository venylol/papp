from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

TOOLKIT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOLKIT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from player_analysis_toolkit.analysis_core import (
    PREDICTED_WLD_TOTAL_FIELD,
    MOVE_RE,
    account_key,
    build_personal_book,
    bundle_games,
    game_players,
    load_engine_games,
    loss_analysis,
    predicted_wld_totals_by_game_player,
    read_prediction_rows,
    read_json,
    reference_analysis,
    replay_game,
    rounded,
    source_events,
    standard_opening_query,
    summarize_opening,
    target_side,
    time_analysis,
    write_csv,
    write_json,
)


def bundle_summary(bundle: dict[str, Any], account: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    target = account_key(account)
    events: list[dict[str, Any]] = []
    games: list[dict[str, Any]] = []
    for detail in sorted(bundle_games(bundle), key=lambda item: str(item.get("created") or "")):
        game_id = str(detail.get("id") or "")
        players = game_players(detail)
        side = target_side(detail, account)
        if side is None:
            continue
        placed_ply = 0
        target_times: list[int] = []
        target_move_times: list[int] = []
        pass_count = 0
        terminal_count = 0
        for source_index, item in enumerate(source_events(detail)):
            player = players[source_index % 2]
            raw_move = item.get("m")
            if isinstance(raw_move, str) and MOVE_RE.fullmatch(raw_move.strip()):
                event_type = "move"
                placed_ply += 1
                board_ply: int | None = placed_ply
            elif raw_move == "-":
                event_type = "pass"
                board_ply = None
            else:
                event_type = "terminal_event"
                board_ply = None
            thinking = item.get("t")
            thinking_ms = int(thinking) if thinking is not None else None
            is_target = account_key(player.get("id")) == target
            if is_target and thinking_ms is not None:
                target_times.append(thinking_ms)
                if event_type == "move":
                    target_move_times.append(thinking_ms)
            if event_type == "pass":
                pass_count += 1
            elif event_type == "terminal_event":
                terminal_count += 1
            events.append({
                "gameId": game_id,
                "created": detail.get("created"),
                "sourceMoveIndex": source_index,
                "turnNumber": source_index + 1,
                "boardPly": board_ply,
                "playerColor": "black" if source_index % 2 == 0 else "white",
                "playerAccount": player.get("id"),
                "isTarget": is_target,
                "eventType": event_type,
                "move": raw_move,
                "thinkingTimeMs": thinking_ms,
            })
        games.append({
            "gameId": game_id,
            "created": detail.get("created"),
            "targetColor": side,
            "opponentAccount": players[1 if side == "black" else 0].get("id"),
            "sourceEventCount": len(source_events(detail)),
            "placedMoveCount": placed_ply,
            "passCount": pass_count,
            "terminalEventCount": terminal_count,
            "targetEventThinkingTimeTotalMs": sum(target_times),
            "targetEventThinkingTimeMeanMs": rounded(statistics.fmean(target_times), 3) if target_times else None,
            "targetPlacedMoveThinkingTimeTotalMs": sum(target_move_times),
            "targetPlacedMoveThinkingTimeMeanMs": rounded(statistics.fmean(target_move_times), 3) if target_move_times else None,
        })
    target_events = [item for item in events if item["isTarget"]]
    return ({
        "schema": "player-data-summary-v1",
        "account": account,
        "scopeNote": bundle.get("note"),
        "gameCount": len(games),
        "sourceEventCount": len(events),
        "placedMoveCount": sum(item["eventType"] == "move" for item in events),
        "passCount": sum(item["eventType"] == "pass" for item in events),
        "terminalEventCount": sum(item["eventType"] == "terminal_event" for item in events),
        "targetPlacedMoveCount": sum(item["eventType"] == "move" for item in target_events),
        "targetPassCount": sum(item["eventType"] == "pass" for item in target_events),
        "targetTerminalEventCount": sum(item["eventType"] == "terminal_event" for item in target_events),
        "definitions": {
            "sourceEventCount": "All position.moves items, including moves, explicit passes and terminal status events.",
            "placedMoveCount": "Only items whose m value is a board coordinate.",
            "thinkingTimeMs": "The original OQ position.moves[*].t value without transformation.",
        },
    }, events, games)


def command_summary(args: argparse.Namespace) -> dict[str, Any]:
    summary, events, games = bundle_summary(read_json(args.bundle), args.account)
    output_dir = Path(args.output_dir)
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "events.json", events)
    write_csv(output_dir / "events.csv", events)
    write_json(output_dir / "games.json", games)
    write_csv(output_dir / "games.csv", games)
    return summary


def command_build_book(args: argparse.Namespace) -> dict[str, Any]:
    bundle = read_json(args.bundle)
    details = bundle_games(bundle)
    excluded = set(args.exclude_game_id or [])
    if args.color == "both":
        value = {
            "schema": "player-human-frequency-books-v1",
            "account": args.account,
            "books": {
                color: build_personal_book(details, args.account, color, excluded, args.max_ply)
                for color in ("black", "white")
            },
        }
    else:
        value = build_personal_book(details, args.account, args.color, excluded, args.max_ply)
    write_json(args.output, value)
    return value


def command_opening(args: argparse.Namespace) -> dict[str, Any]:
    value = summarize_opening(
        read_json(args.bundle), args.account, set(args.reported_game_id), args.max_ply
    )
    write_json(args.output, value)
    return value


def command_standard_opening(args: argparse.Namespace) -> dict[str, Any]:
    value = standard_opening_query(
        read_json(args.bundle),
        read_json(args.catalog),
        args.account,
        set(args.game_id or []),
        args.opening,
    )
    value["catalog"] = str(Path(args.catalog).resolve())
    if args.csv_output:
        rows = []
        for game in value["games"]:
            opening = game["opening"] or {}
            rows.append({
                "gameId": game["gameId"],
                "created": game["created"],
                "targetColor": game["targetColor"],
                "opponentAccount": game["opponentAccount"],
                "moveCount": game["moveCount"],
                "openingId": opening.get("id"),
                "openingName": opening.get("name"),
                "openingSequence": opening.get("sequence"),
                "openingPly": opening.get("ply"),
                "allOpeningNames": " | ".join(item["name"] for item in game["openingMatches"]),
                "moveSequence": game["moveSequence"],
            })
        write_csv(args.csv_output, rows)
        value["csvOutput"] = str(Path(args.csv_output).resolve())
    write_json(args.output, value)
    return value


def command_loss(args: argparse.Namespace) -> dict[str, Any]:
    value = loss_analysis(
        args.engine_dir,
        args.account,
        set(args.reported_game_id),
        args.bootstrap,
        args.model_bootstrap,
        args.seed,
        args.wld_from_ply,
    )
    write_json(args.output, value)
    return value


def command_time(args: argparse.Namespace) -> dict[str, Any]:
    value = time_analysis(args.engine_dir, args.account, set(args.reported_game_id))
    write_json(args.output, value)
    return value


def command_reference(args: argparse.Namespace) -> dict[str, Any]:
    value = reference_analysis(read_json(args.config), args.wld_from_ply)
    write_json(args.output, value)
    return value


def command_run_all(args: argparse.Namespace) -> dict[str, Any]:
    config = read_json(args.config)
    output_dir = Path(config["outputDirectory"])
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = config["bundle"]
    engine_dir = config["engineDirectory"]
    account = config["account"]
    reported = list(config["reportedGameIds"])
    summary, events, games = bundle_summary(read_json(bundle_path), account)
    write_json(output_dir / "data-summary.json", summary)
    write_json(output_dir / "events.json", events)
    write_csv(output_dir / "events.csv", events)
    write_json(output_dir / "games.json", games)
    write_csv(output_dir / "games.csv", games)
    opening = summarize_opening(read_json(bundle_path), account, set(reported), int(config.get("maxPly", 20)))
    write_json(output_dir / "opening-analysis.json", opening)
    loss = loss_analysis(
        engine_dir,
        account,
        set(reported),
        int(config.get("bootstrap", 10_000)),
        int(config.get("modelBootstrap", 1_000)),
        int(config.get("seed", 20260801)),
        config.get("wldFromPly"),
    )
    write_json(output_dir / "loss-analysis.json", loss)
    time = time_analysis(engine_dir, account, set(reported))
    write_json(output_dir / "time-analysis.json", time)
    result: dict[str, Any] = {
        "schema": "player-analysis-run-v1",
        "outputDirectory": str(output_dir.resolve()),
        "files": ["data-summary.json", "events.json", "events.csv", "games.json", "games.csv", "opening-analysis.json", "loss-analysis.json", "time-analysis.json"],
    }
    if config.get("referenceConfig"):
        reference_config = read_json(config["referenceConfig"])
        reference = reference_analysis(
            reference_config,
            config.get("wldFromPly", reference_config.get("wldFromPly")),
        )
        write_json(output_dir / "reference-analysis.json", reference)
        result["files"].append("reference-analysis.json")
    write_json(output_dir / "run-summary.json", result)
    return result


def command_model_wld(args: argparse.Namespace) -> dict[str, Any]:
    value = predicted_wld_totals_by_game_player(
        read_prediction_rows(args.predictions), args.wld_from_ply
    )
    write_json(args.output, value)
    if args.csv_output:
        write_csv(args.csv_output, value["gamePlayerTotals"])
    if args.player_csv_output:
        write_csv(args.player_csv_output, value["playerTotals"])
    if args.markdown_output:
        lines = [
            "# 模型预期 WLD 损失汇总",
            "",
            "## 每局、每位棋手",
            "",
            f"| game_id | player_id | side | {PREDICTED_WLD_TOTAL_FIELD} |",
            "|---|---|---|---:|",
        ]
        for row in value["gamePlayerTotals"]:
            lines.append(
                f"| {row['game_id']} | {row.get('player_id') or ''} | {row.get('side') or ''} | "
                f"{row[PREDICTED_WLD_TOTAL_FIELD]} |"
            )
        lines.extend([
            "",
            "## 每位棋手总计",
            "",
            f"| player_id | side | {PREDICTED_WLD_TOTAL_FIELD} |",
            "|---|---|---:|",
        ])
        for row in value["playerTotals"]:
            lines.append(
                f"| {row.get('player_id') or ''} | {row.get('side') or ''} | "
                f"{row[PREDICTED_WLD_TOTAL_FIELD]} |"
            )
        target = Path(args.markdown_output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return value


def add_common_reported(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account", required=True)
    parser.add_argument("--reported-game-id", action="append", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable Othello Quest player investigation toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    summary = sub.add_parser("summary", help="summarize an OQ account bundle")
    summary.add_argument("--bundle", required=True)
    summary.add_argument("--account", required=True)
    summary.add_argument("--output-dir", required=True)
    summary.set_defaults(handler=command_summary)

    book = sub.add_parser("build-book", help="build a player-specific human frequency book")
    book.add_argument("--bundle", required=True)
    book.add_argument("--account", required=True)
    book.add_argument("--color", choices=("black", "white", "both"), default="both")
    book.add_argument("--exclude-game-id", action="append", default=[])
    book.add_argument("--max-ply", type=int, default=20)
    book.add_argument("--output", required=True)
    book.set_defaults(handler=command_build_book)

    opening = sub.add_parser("opening", help="compare reported games with same-color leave-one-out controls")
    opening.add_argument("--bundle", required=True)
    add_common_reported(opening)
    opening.add_argument("--max-ply", type=int, default=20)
    opening.add_argument("--output", required=True)
    opening.set_defaults(handler=command_opening)

    standard_opening = sub.add_parser(
        "standard-opening",
        help="identify or filter standard openings with an editable catalog",
    )
    standard_opening.add_argument("--bundle", required=True)
    standard_opening.add_argument("--account", required=True)
    standard_opening.add_argument(
        "--catalog",
        default=str(TOOLKIT_ROOT / "assets" / "standard_openings.json"),
    )
    standard_opening.add_argument("--game-id", action="append", default=[])
    standard_opening.add_argument("--opening", default="")
    standard_opening.add_argument("--output", required=True)
    standard_opening.add_argument("--csv-output", default="")
    standard_opening.set_defaults(handler=command_standard_opening)

    loss = sub.add_parser("loss", help="calculate loss comparisons from existing EG JSON")
    loss.add_argument("--engine-dir", required=True)
    add_common_reported(loss)
    loss.add_argument("--bootstrap", type=int, default=10_000)
    loss.add_argument("--model-bootstrap", type=int, default=1_000)
    loss.add_argument("--seed", type=int, default=20260801)
    loss.add_argument(
        "--wld-from-ply",
        type=int,
        choices=(39,),
        help="add engine WLD loss totals from inclusive pass-free global placement ply 39",
    )
    loss.add_argument("--output", required=True)
    loss.set_defaults(handler=command_loss)

    time = sub.add_parser("time", help="calculate arithmetic-mean time trends")
    time.add_argument("--engine-dir", required=True)
    add_common_reported(time)
    time.add_argument("--output", required=True)
    time.set_defaults(handler=command_time)

    reference = sub.add_parser("reference", help="compare target games with configured reference groups")
    reference.add_argument("--config", required=True)
    reference.add_argument(
        "--wld-from-ply",
        type=int,
        choices=(39,),
        help="override/add WLD totals from inclusive pass-free global placement ply 39",
    )
    reference.add_argument("--output", required=True)
    reference.set_defaults(handler=command_reference)

    run_all = sub.add_parser("run-all", help="run summary, opening, loss, time and optional reference analysis")
    run_all.add_argument("--config", required=True)
    run_all.set_defaults(handler=command_run_all)

    model_wld = sub.add_parser(
        "model-wld",
        help="sum expected_wld_loss by game and mover from model prediction CSV/JSON",
    )
    model_wld.add_argument("--predictions", required=True)
    model_wld.add_argument("--wld-from-ply", type=int, choices=(39,), required=True)
    model_wld.add_argument("--output", required=True, help="UTF-8 JSON output")
    model_wld.add_argument("--csv-output", default="", help="optional per-game/player UTF-8 CSV")
    model_wld.add_argument("--player-csv-output", default="", help="optional per-player UTF-8 CSV")
    model_wld.add_argument("--markdown-output", default="", help="optional UTF-8 Markdown totals report")
    model_wld.set_defaults(handler=command_model_wld)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = args.handler(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
