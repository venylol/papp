#!/usr/bin/env python3
"""Independent Egaroucid stone-loss analyzer for the local tournament state."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import queue
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl

PAPP_ROOT = Path(__file__).resolve().parents[2]
THIRD_PARTY_ROOT = PAPP_ROOT / "third_party"
TOOLKIT_SRC = THIRD_PARTY_ROOT / "player-analysis-toolkit" / "src"
if str(TOOLKIT_SRC) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_SRC))

try:
    import agent_match_image_helper as match_helper
except ModuleNotFoundError as exc:
    if exc.name != "agent_match_image_helper":
        raise
    # The FTD/Agent field helper is intentionally not part of PAPP.  Bundle
    # and transcript modes remain standalone; live once/watch mode reports a
    # clear dependency error when it is requested.
    match_helper = None

from player_analysis_toolkit.egaroucid_worker_budget import (
    reserve_background_egaroucid_workers,
    reserve_egaroucid_workers,
    validate_background_egaroucid_workers,
    validate_egaroucid_workers,
    worker_budget_info,
)


DEFAULT_STATE_PATH = PAPP_ROOT / "data" / "checkin-state.json"
DEFAULT_CACHE_DIR = PAPP_ROOT / "data" / "ega-analysis"
DEFAULT_ENGINE_EXE = (
    PAPP_ROOT
    / "vendor"
    / "engines"
    / "Egaroucid_for_Console_7_8_1_Windows_SIMD"
    / "Egaroucid_for_Console_7_8_1_SIMD.exe"
)
MOVE_RE = re.compile(r"^[a-h][1-8]$", re.IGNORECASE)
_LOCK_HANDLE: Any = None


def require_agent_helpers() -> Any:
    if match_helper is None:
        raise RuntimeError(
            "live status/once/watch mode requires the excluded Agent/FTD "
            "field helper; use analyze-bundle or analyze-transcript in PAPP"
        )
    return match_helper


def detail_moves(detail: dict[str, Any]) -> list[Any]:
    position = detail.get("position") if isinstance(detail, dict) else None
    moves = position.get("moves") if isinstance(position, dict) else None
    return moves if isinstance(moves, list) else []


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def norm(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def account_key(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def player_loss_key(name: Any, account: Any) -> str:
    account = account_key(account)
    if account:
        return account
    name_key = re.sub(r"\s+", " ", norm(name).lower())
    return f"name:{name_key}" if name_key else ""


def is_bye_name(value: Any) -> bool:
    return norm(value).lower() == "bye"


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_metadata(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}


class OthelloBoard:
    directions = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    ]

    def __init__(self) -> None:
        self.board = [["-" for _ in range(8)] for _ in range(8)]
        self.board[3][3] = "O"
        self.board[3][4] = "X"
        self.board[4][3] = "X"
        self.board[4][4] = "O"
        self.current = "X"
        self.normalize_turn()

    @staticmethod
    def opponent(color: str) -> str:
        return "O" if color == "X" else "X"

    def captures(self, row: int, col: int, color: str) -> list[tuple[int, int]]:
        if self.board[row][col] != "-":
            return []
        opponent = self.opponent(color)
        out: list[tuple[int, int]] = []
        for dr, dc in self.directions:
            rr = row + dr
            cc = col + dc
            line: list[tuple[int, int]] = []
            while 0 <= rr < 8 and 0 <= cc < 8 and self.board[rr][cc] == opponent:
                line.append((rr, cc))
                rr += dr
                cc += dc
            if line and 0 <= rr < 8 and 0 <= cc < 8 and self.board[rr][cc] == color:
                out.extend(line)
        return out

    def legal_moves(self, color: str | None = None) -> list[tuple[int, int]]:
        use_color = color or self.current
        return [
            (row, col)
            for row in range(8)
            for col in range(8)
            if self.captures(row, col, use_color)
        ]

    def normalize_turn(self) -> None:
        if self.legal_moves(self.current):
            return
        other = self.opponent(self.current)
        if self.legal_moves(other):
            self.current = other

    def apply_move(self, move: str) -> str:
        text = move.strip().lower()
        if not MOVE_RE.match(text):
            raise ValueError(f"bad move: {move}")
        row = int(text[1]) - 1
        col = ord(text[0]) - ord("a")
        flips = self.captures(row, col, self.current)
        if not flips:
            raise ValueError(f"illegal move {move} for {self.current}")
        side = self.current
        self.board[row][col] = self.current
        for rr, cc in flips:
            self.board[rr][cc] = self.current
        self.current = self.opponent(self.current)
        self.normalize_turn()
        return side

    def to_setboard_str(self) -> str:
        chars = []
        for row in range(8):
            for col in range(8):
                chars.append(self.board[row][col])
        chars.append(self.current)
        return "".join(chars)

    def final_scores(self) -> tuple[int, int]:
        black_discs = sum(1 for row in self.board for value in row if value == "X")
        white_discs = sum(1 for row in self.board for value in row if value == "O")
        empty = 64 - black_discs - white_discs
        if black_discs > white_discs:
            return black_discs + empty, white_discs
        if white_discs > black_discs:
            return black_discs, white_discs + empty
        return black_discs + empty // 2, white_discs + empty // 2


class PersistentEngine:
    def __init__(
        self,
        engine_exe: Path,
        level: int,
        threads: int,
        hash_level: int,
        stderr_log: Path,
        book_file: Path | None = None,
    ) -> None:
        if not engine_exe.exists():
            raise FileNotFoundError(f"Egaroucid console not found: {engine_exe}")
        self.engine_exe = engine_exe.resolve()
        self.level = int(level)
        self.threads = int(threads)
        self.hash_level = int(hash_level)
        self.book_file = book_file.resolve() if book_file and book_file.exists() else None
        args = [
            str(engine_exe),
            "-q",
            "-noboard",
            "-l",
            str(level),
            "-t",
            str(threads),
            "-hash",
            str(hash_level),
            "-noautocacheclear",
        ]
        if book_file and book_file.exists():
            args.extend(["-b", str(book_file)])
        self.stderr_handle = stderr_log.open("a", encoding="utf-8")
        self._output_queue: queue.Queue[str | None] = queue.Queue()
        self._buffer = ""
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            args,
            cwd=str(engine_exe.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
            encoding="utf-8",
            errors="replace",
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("failed to open Egaroucid pipes")
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        self._wait_for_prompt()

    def metadata(self) -> dict[str, Any]:
        return {
            "name": "Egaroucid for Console",
            "path": str(self.engine_exe),
            "fileMetadata": file_metadata(self.engine_exe),
            "level": self.level,
            "threads": self.threads,
            "hash": self.hash_level,
            "book": str(self.book_file) if self.book_file else "enabled-default",
        }

    def _read_output(self) -> None:
        assert self.proc.stdout is not None
        try:
            while True:
                chunk = self.proc.stdout.read(1)
                if chunk == "":
                    break
                self._output_queue.put(chunk)
        finally:
            self._output_queue.put(None)

    @staticmethod
    def _prompt_index(text: str) -> int:
        match = re.search(r"(?:\A|\r?\n)>\s", text)
        return match.end() if match else -1

    @staticmethod
    def _strip_prompt(text: str) -> str:
        return re.sub(r"(?:\A|\r?\n)>\s\Z", "", text)

    def _wait_for_prompt(self, timeout: float = 30.0) -> str:
        deadline = time.time() + timeout
        while True:
            idx = self._prompt_index(self._buffer)
            if idx >= 0:
                out = self._buffer[:idx]
                self._buffer = self._buffer[idx:]
                return self._strip_prompt(out)
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for Egaroucid prompt")
            try:
                chunk = self._output_queue.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"Egaroucid exited with code {self.proc.returncode}")
                continue
            if chunk is None:
                raise RuntimeError("Egaroucid output stream closed")
            self._buffer += chunk

    def command(self, text: str, timeout: float = 60.0) -> str:
        with self._lock:
            assert self.proc.stdin is not None
            self.proc.stdin.write(text.rstrip() + "\n")
            self.proc.stdin.flush()
            output = self._wait_for_prompt(timeout=timeout)
            try:
                self.stderr_handle.write(output)
                self.stderr_handle.flush()
            except Exception:
                pass
            return output

    def setboard(self, board: str) -> None:
        self.command(f"setboard {board}")

    def play(self, move: str) -> None:
        self.command(f"play {move}")

    def hint(self) -> dict[str, Any]:
        output = self.command("hint 1")
        table_lines = [line.strip() for line in output.splitlines() if line.strip().startswith("|")]
        if len(table_lines) < 2:
            raise RuntimeError(f"unexpected hint output: {output!r}")
        body = [part.strip() for part in table_lines[1].split("|")[1:-1]]
        if len(body) < 4:
            raise RuntimeError(f"unexpected hint output: {table_lines[1]}")
        depth_text = body[1]
        score_text = body[3].replace("+", "")
        return {
            "bestMove": body[2].lower(),
            "bestEval": int(score_text),
            "depth": depth_text,
        }

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.write("exit\n")
                self.proc.stdin.flush()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.stderr_handle.close()


@dataclass
class GameTask:
    round_no: int
    table: int
    pairing_index: int
    black_name: str
    white_name: str
    black_account: str
    white_account: str
    black_ftd_side: str
    white_ftd_side: str
    ftd_black_name: str
    ftd_white_name: str
    ftd_black_account: str
    ftd_white_account: str
    game_id: str
    detail: dict[str, Any]
    source: str
    ending_kind: str

    @property
    def key(self) -> str:
        base = self.game_id or f"r{self.round_no}-t{self.table}"
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", base)


def detail_from_pairing(pairing: dict[str, Any]) -> tuple[str, dict[str, Any], str, str]:
    audit = pairing.get("oqAutoAudit") if isinstance(pairing.get("oqAutoAudit"), dict) else {}
    game = audit.get("game") if isinstance(audit.get("game"), dict) else {}
    game_id = norm(game.get("gameId"))
    ending_kind = norm(audit.get("endingKind") or pairing.get("resultKind") or "")
    detail = audit.get("gameDetail") if isinstance(audit.get("gameDetail"), dict) else {}
    if detail:
        return game_id, detail, "oqAutoAudit.gameDetail", ending_kind
    availability_audit = pairing.get("oqGameAvailableAudit") if isinstance(pairing.get("oqGameAvailableAudit"), dict) else {}
    availability_game = availability_audit.get("game") if isinstance(availability_audit.get("game"), dict) else {}
    if not game_id:
        game_id = norm(availability_game.get("gameId"))
    if not ending_kind:
        ending_kind = norm(availability_audit.get("endingKind") or "")
    detail = availability_audit.get("gameDetail") if isinstance(availability_audit.get("gameDetail"), dict) else {}
    if detail:
        return game_id, detail, "oqGameAvailableAudit.gameDetail", ending_kind
    detail = pairing.get("gameDetail") if isinstance(pairing.get("gameDetail"), dict) else {}
    if detail:
        return game_id, detail, "pairing.gameDetail", ending_kind
    source_key = norm(pairing.get("sourceMessageKey"))
    if source_key.startswith("oq-auto:id:"):
        game_id = source_key.replace("oq-auto:id:", "", 1)
    return game_id, {}, "", ending_kind


def score_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 64 else None


def pairing_scores(pairing: dict[str, Any]) -> tuple[int | None, int | None]:
    return score_int(pairing.get("blackScore")), score_int(pairing.get("whiteScore"))


def parse_time_optional(value: Any) -> datetime | None:
    text = norm(value)
    if not text:
        return None
    return require_agent_helpers().parse_local_time_optional(text)


def round_time_window(round_item: dict[str, Any], pairing: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    start = parse_time_optional(round_item.get("roundStartAt"))
    result = parse_time_optional(pairing.get("resultTime"))
    if start and result:
        return start - timedelta(minutes=2), result + timedelta(minutes=12)
    if start:
        return start - timedelta(minutes=2), start + timedelta(minutes=55)
    if result:
        return result - timedelta(minutes=55), result + timedelta(minutes=12)
    return None, None


def find_account_for_name(state: dict[str, Any], ftd_name: str) -> str:
    helper = require_agent_helpers()
    mapping = helper.ftd_player_account_mapping_rows(state)
    row = mapping.get(helper.normalize_name_key(ftd_name))
    if row and norm(row.get("account")):
        return norm(row.get("account"))
    for player in helper.players_from_state(state):
        if helper.normalize_name_key(player.get("displayName")) == helper.normalize_name_key(ftd_name):
            return norm(player.get("account"))
    return ""


def build_table_info(pairing: dict[str, Any], state: dict[str, Any], pairing_index: int) -> dict[str, Any]:
    audit = pairing.get("oqAutoAudit") if isinstance(pairing.get("oqAutoAudit"), dict) else {}
    availability_audit = pairing.get("oqGameAvailableAudit") if isinstance(pairing.get("oqGameAvailableAudit"), dict) else {}
    black_account = (
        norm(audit.get("ftdBlackAccount"))
        or norm(availability_audit.get("ftdBlackAccount"))
        or find_account_for_name(state, pairing.get("black"))
    )
    white_account = (
        norm(audit.get("ftdWhiteAccount"))
        or norm(availability_audit.get("ftdWhiteAccount"))
        or find_account_for_name(state, pairing.get("white"))
    )
    return {
        "table": int(pairing.get("table") or pairing_index + 1),
        "black": norm(pairing.get("black")),
        "white": norm(pairing.get("white")),
        "blackAccount": black_account,
        "whiteAccount": white_account,
        "blackAccountKey": account_key(black_account),
        "whiteAccountKey": account_key(white_account),
        "pairing": pairing,
    }


def oq_game_summary(pairing: dict[str, Any]) -> dict[str, Any]:
    audit = pairing.get("oqAutoAudit") if isinstance(pairing.get("oqAutoAudit"), dict) else {}
    game = audit.get("game") if isinstance(audit.get("game"), dict) else {}
    if not game:
        availability_audit = pairing.get("oqGameAvailableAudit") if isinstance(pairing.get("oqGameAvailableAudit"), dict) else {}
        game = availability_audit.get("game") if isinstance(availability_audit.get("game"), dict) else {}
    return game


def actual_side_from_oq_account(account: Any, table_info: dict[str, Any], fallback: str) -> str:
    key = account_key(account)
    if key and key == table_info.get("blackAccountKey"):
        return "black"
    if key and key == table_info.get("whiteAccountKey"):
        return "white"
    return fallback


def task_from_pairing(
    state: dict[str, Any],
    pairing: dict[str, Any],
    pairing_index: int,
    round_no: int,
    game_id: str,
    detail: dict[str, Any],
    source: str,
    ending_kind: str,
) -> GameTask:
    table_info = build_table_info(pairing, state, pairing_index)
    game = oq_game_summary(pairing)
    oq_black_account = norm(game.get("blackName"))
    oq_white_account = norm(game.get("whiteName"))
    black_ftd_side = actual_side_from_oq_account(oq_black_account, table_info, "black")
    white_ftd_side = actual_side_from_oq_account(oq_white_account, table_info, "white")
    side_info = {
        "black": {"name": table_info["black"], "account": table_info["blackAccount"]},
        "white": {"name": table_info["white"], "account": table_info["whiteAccount"]},
    }
    black_info = side_info.get(black_ftd_side, side_info["black"])
    white_info = side_info.get(white_ftd_side, side_info["white"])
    return GameTask(
        round_no=round_no,
        table=table_info["table"],
        pairing_index=pairing_index,
        black_name=black_info["name"],
        white_name=white_info["name"],
        black_account=black_info["account"] or oq_black_account,
        white_account=white_info["account"] or oq_white_account,
        black_ftd_side=black_ftd_side,
        white_ftd_side=white_ftd_side,
        ftd_black_name=table_info["black"],
        ftd_white_name=table_info["white"],
        ftd_black_account=table_info["blackAccount"],
        ftd_white_account=table_info["whiteAccount"],
        game_id=game_id,
        detail=detail,
        source=source,
        ending_kind=ending_kind,
    )


def fetch_matching_detail_for_pairing(
    state: dict[str, Any],
    round_item: dict[str, Any],
    pairing: dict[str, Any],
    pairing_index: int,
    oq_base_url: str,
    oq_timeout: int,
    detail_cache: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any], str]:
    helper = require_agent_helpers()
    table_info = build_table_info(pairing, state, pairing_index)
    black_score, white_score = pairing_scores(pairing)
    if black_score is None or white_score is None:
        return "", {}, "skip-no-score"
    if not table_info["blackAccountKey"] or not table_info["whiteAccountKey"]:
        return "", {}, "skip-unmapped-accounts"
    start, end = round_time_window(round_item, pairing)
    if not start or not end:
        return "", {}, "skip-no-time-window"
    fetch = helper.fetch_oq_games_for_accounts(
        [table_info["blackAccount"], table_info["whiteAccount"]],
        "play",
        oq_base_url,
        oq_timeout,
        2,
    )
    candidates = helper.collect_table_oq_candidates(
        table_info,
        fetch.get("gamesByAccount") or {},
        start,
        end,
    )
    matched: list[tuple[str, dict[str, Any]]] = []
    for candidate in candidates:
        entry = candidate.get("entry") if isinstance(candidate, dict) else None
        if entry is None:
            continue
        try:
            entry, _ = helper.oq_entry_with_detail(entry, oq_base_url, oq_timeout, detail_cache)
            b_score, w_score, _reason, _account_scores = helper.oq_scores_for_ftd_pairing(entry, table_info)
        except Exception:
            continue
        if b_score == black_score and w_score == white_score:
            detail = helper.oq_stored_game_detail(entry)
            game_id = norm(helper.game_entry_summary(entry).get("gameId"))
            if detail:
                matched.append((game_id, detail))
    if len(matched) == 1:
        return matched[0][0], matched[0][1], "oq-fetch-score-matched"
    if len(matched) > 1:
        return "", {}, "skip-multiple-score-matched-games"
    return "", {}, "skip-no-score-matched-game"


def collect_prelim_tasks(state: dict[str, Any], round_limit: int, oq_base_url: str, oq_timeout: int) -> list[GameTask]:
    helper = state.get("scoreHelper") if isinstance(state.get("scoreHelper"), dict) else {}
    rounds = helper.get("rounds") if isinstance(helper.get("rounds"), list) else []
    tasks: list[GameTask] = []
    detail_cache: dict[str, dict[str, Any]] = {}
    for round_index, round_item in enumerate(rounds[:round_limit], start=1):
        if not isinstance(round_item, dict):
            continue
        pairings = round_item.get("ftdPairings") if isinstance(round_item.get("ftdPairings"), list) else []
        for pairing_index, pairing in enumerate(pairings):
            if not isinstance(pairing, dict):
                continue
            if is_bye_name(pairing.get("black")) or is_bye_name(pairing.get("white")):
                continue
            status = norm(pairing.get("status"))
            if status not in {"ready", "completed"}:
                continue
            result_kind = norm(pairing.get("resultKind"))
            if result_kind == "absence":
                continue
            game_id, detail, source, ending_kind = detail_from_pairing(pairing)
            if not detail and game_id:
                try:
                    OQClient = require_agent_helpers().load_oq_client_class()
                    if game_id not in detail_cache:
                        detail_cache[game_id] = OQClient(base_url=oq_base_url, timeout=oq_timeout).fetch_game_detail(game_id)
                    detail = detail_cache[game_id]
                    source = "oq-fetch-game-detail"
                except Exception:
                    detail = {}
            if not detail:
                game_id, detail, source = fetch_matching_detail_for_pairing(
                    state,
                    round_item,
                    pairing,
                    pairing_index,
                    oq_base_url,
                    oq_timeout,
                    detail_cache,
                )
            if not detail:
                continue
            tasks.append(task_from_pairing(state, pairing, pairing_index, round_index, game_id, detail, source, ending_kind))
    return tasks


def oq_moves(detail: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in detail_moves(detail):
        if not isinstance(item, dict):
            continue
        move = item.get("m")
        if isinstance(move, str) and MOVE_RE.match(move.strip()):
            out.append(move.strip().lower())
    return out


def oq_timed_events(task: GameTask) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    engine_ply = 0
    for source_move_index, item in enumerate(detail_moves(task.detail)):
        if not isinstance(item, dict):
            continue
        source_color = "black" if source_move_index % 2 == 0 else "white"
        move = norm(item.get("m")).lower()
        if MOVE_RE.match(move):
            event_type = "move"
            engine_ply += 1
            event_engine_ply: int | None = engine_ply
        elif move == "-":
            event_type = "pass"
            event_engine_ply = None
        else:
            event_type = "terminal_event"
            event_engine_ply = None
        player_name = task.black_name if source_color == "black" else task.white_name
        player_account = task.black_account if source_color == "black" else task.white_account
        thinking_time = item.get("t")
        try:
            thinking_time_ms = int(thinking_time) if thinking_time is not None else None
        except (TypeError, ValueError):
            thinking_time_ms = None
        event = {
            "sourceMoveIndex": source_move_index,
            "turnNumber": source_move_index + 1,
            "enginePly": event_engine_ply,
            "eventType": event_type,
            "move": move or None,
            "playerColor": source_color,
            "playerName": player_name,
            "playerAccount": player_account,
            "thinkingTimeMs": thinking_time_ms,
            "status": item.get("s"),
            "delay": item.get("delay"),
            "bestMove": None,
            "bestEval": None,
            "actualEval": None,
            "lossPositive": None,
            "lossClipped": None,
            "lossSignedUser": None,
            "engineJudge": None,
            "boardBefore": None,
            "legalMoveCount": None,
            "bestDepth": None,
            "nextDepth": None,
        }
        events.append(event)
    return events


def analyze_game(task: GameTask, engine: PersistentEngine) -> dict[str, Any]:
    events = oq_timed_events(task)
    move_events = [event for event in events if event["eventType"] == "move"]
    moves = [str(event["move"]) for event in move_events]
    board = OthelloBoard()
    engine.setboard(board.to_setboard_str())
    nodes: list[dict[str, Any]] = []
    by_side = {
        "black": {"name": task.black_name, "account": task.black_account, "color": "black", "nodes": []},
        "white": {"name": task.white_name, "account": task.white_account, "color": "white", "nodes": []},
    }

    for ply, event in enumerate(move_events, start=1):
        move = str(event["move"])
        side_before = board.current
        board_before = board.to_setboard_str()
        legal_move_count = len(board.legal_moves())
        player_color = "black" if side_before == "X" else "white"
        if event["playerColor"] != player_color:
            raise RuntimeError(
                f"OQ/source turn mismatch in {task.game_id} at sourceMoveIndex "
                f"{event['sourceMoveIndex']}: source={event['playerColor']} engine={player_color}"
            )
        player_name = task.black_name if player_color == "black" else task.white_name
        player_account = task.black_account if player_color == "black" else task.white_account
        current_hint = engine.hint()
        board.apply_move(move)
        engine.play(move)
        if not board.legal_moves("X") and not board.legal_moves("O"):
            black_score, white_score = board.final_scores()
            black_margin = black_score - white_score
            actual_eval = black_margin if side_before == "X" else -black_margin
            next_depth = "End"
        else:
            next_hint = engine.hint()
            if board.current == side_before:
                actual_eval = int(next_hint["bestEval"])
            else:
                actual_eval = -int(next_hint["bestEval"])
            next_depth = next_hint.get("depth", "")
        best_eval = int(current_hint["bestEval"])
        loss_positive = best_eval - actual_eval
        loss_clipped = max(0, loss_positive)
        engine_judge = "Mistake" if loss_clipped >= 4 else "Disagree" if loss_clipped > 0 else ""
        node = {
            "ply": ply,
            "plyGroup": math.ceil(ply / 2),
            "move": move,
            "playerColor": player_color,
            "playerName": player_name,
            "playerAccount": player_account,
            "sourceMoveIndex": event["sourceMoveIndex"],
            "thinkingTimeMs": event["thinkingTimeMs"],
            "boardBefore": board_before,
            "legalMoveCount": legal_move_count,
            "bestMove": current_hint["bestMove"],
            "bestEval": best_eval,
            "actualEval": actual_eval,
            "lossPositive": loss_positive,
            "lossClipped": loss_clipped,
            "lossSignedUser": actual_eval - best_eval,
            "engineJudge": engine_judge,
            "bestDepth": current_hint.get("depth", ""),
            "nextDepth": next_depth,
        }
        nodes.append(node)
        by_side[player_color]["nodes"].append(node)
        for key in (
            "bestMove", "bestEval", "actualEval", "lossPositive", "lossClipped",
            "lossSignedUser", "engineJudge", "boardBefore", "legalMoveCount",
            "bestDepth", "nextDepth",
        ):
            event[key] = node[key]

    players = []
    for value in by_side.values():
        key = player_loss_key(value["name"], value["account"])
        if not key:
            continue
        losses = [float(item["lossClipped"]) for item in value["nodes"]]
        players.append(
            {
                "key": key,
                "name": value["name"],
                "account": value["account"],
                "color": value["color"],
                "ftdSide": task.black_ftd_side if value["color"] == "black" else task.white_ftd_side,
                "nodeCount": len(losses),
                "totalLoss": round(sum(losses), 3),
                "averageLoss": round(sum(losses) / len(losses), 3) if losses else None,
            }
        )

    return {
        "schema": "ega-game-analysis-v1",
        "analyzedAt": now_iso(),
        "round": task.round_no,
        "table": task.table,
        "gameId": task.game_id,
        "source": task.source,
        "black": {"name": task.black_name, "account": task.black_account},
        "white": {"name": task.white_name, "account": task.white_account},
        "ftdBlack": {"name": task.ftd_black_name, "account": task.ftd_black_account},
        "ftdWhite": {"name": task.ftd_white_name, "account": task.ftd_white_account},
        "actualSideByFtdSide": {"black": task.black_ftd_side, "white": task.white_ftd_side},
        "engine": engine.metadata(),
        "sourceEventCount": len(events),
        "moveCount": len(moves),
        "passCount": sum(event["eventType"] == "pass" for event in events),
        "terminalEventCount": sum(event["eventType"] == "terminal_event" for event in events),
        "events": events,
        "nodes": nodes,
        "players": players,
    }


def game_side_player_summaries(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    stored_players = analysis.get("players") if isinstance(analysis.get("players"), list) else []
    ftd_side_by_color = {
        norm(player.get("color")).lower(): norm(player.get("ftdSide")).lower()
        for player in stored_players
        if isinstance(player, dict) and norm(player.get("color")) and norm(player.get("ftdSide"))
    }
    for color in ("black", "white"):
        side = analysis.get(color) if isinstance(analysis.get(color), dict) else {}
        key = player_loss_key(side.get("name"), side.get("account"))
        if not key:
            continue
        nodes = [
            node
            for node in analysis.get("nodes", [])
            if isinstance(node, dict) and norm(node.get("playerColor")).lower() == color
        ]
        events = [
            event
            for event in analysis.get("events", [])
            if isinstance(event, dict) and norm(event.get("playerColor")).lower() == color
        ]
        if not events:
            events = nodes
        total = sum(float(node.get("lossClipped") or 0) for node in nodes)
        count = len(nodes)
        move_times = [
            int(node["thinkingTimeMs"])
            for node in nodes
            if isinstance(node.get("thinkingTimeMs"), (int, float))
        ]
        event_times = [
            int(event["thinkingTimeMs"])
            for event in events
            if isinstance(event.get("thinkingTimeMs"), (int, float))
        ]
        out.append(
            {
                "key": key,
                "name": norm(side.get("name")),
                "account": norm(side.get("account")),
                "color": color,
                "ftdSide": ftd_side_by_color.get(color, color),
                "totalLoss": round(total, 3),
                "averageLoss": round(total / count, 3) if count else None,
                "nodeCount": count,
                "moveThinkingTimeCount": len(move_times),
                "moveThinkingTimeTotalMs": sum(move_times),
                "moveThinkingTimeAverageMs": round(sum(move_times) / len(move_times), 3) if move_times else None,
                "eventCount": len(events),
                "eventThinkingTimeCount": len(event_times),
                "eventThinkingTimeTotalMs": sum(event_times),
                "passCount": sum(norm(event.get("eventType")) == "pass" for event in events),
                "terminalEventCount": sum(norm(event.get("eventType")) == "terminal_event" for event in events),
            }
        )
    return out


def summarize_competition(cache_dir: Path, analyses: list[dict[str, Any]]) -> dict[str, Any]:
    players: dict[str, dict[str, Any]] = {}
    games = []
    for analysis in analyses:
        game_key = analysis.get("gameId") or f"r{analysis.get('round')}-t{analysis.get('table')}"
        game_summary = {
            "round": analysis.get("round"),
            "table": analysis.get("table"),
            "gameId": analysis.get("gameId") or "",
            "cacheFile": str((cache_dir / f"game_{analysis.get('round')}_{analysis.get('table')}_{analysis.get('gameId') or 'nogame'}.json").resolve()),
            "black": analysis.get("black") if isinstance(analysis.get("black"), dict) else {},
            "white": analysis.get("white") if isinstance(analysis.get("white"), dict) else {},
            "ftdBlack": analysis.get("ftdBlack") if isinstance(analysis.get("ftdBlack"), dict) else analysis.get("black") if isinstance(analysis.get("black"), dict) else {},
            "ftdWhite": analysis.get("ftdWhite") if isinstance(analysis.get("ftdWhite"), dict) else analysis.get("white") if isinstance(analysis.get("white"), dict) else {},
            "players": [],
        }
        for player in game_side_player_summaries(analysis):
            if not isinstance(player, dict):
                continue
            key = norm(player.get("key"))
            if not key:
                continue
            bucket = players.setdefault(
                key,
                {
                    "key": key,
                    "name": norm(player.get("name")),
                    "account": norm(player.get("account")),
                    "games": [],
                    "nodeCount": 0,
                    "totalLoss": 0.0,
                    "moveThinkingTimeCount": 0,
                    "moveThinkingTimeTotalMs": 0,
                    "eventCount": 0,
                    "eventThinkingTimeCount": 0,
                    "eventThinkingTimeTotalMs": 0,
                    "passCount": 0,
                    "terminalEventCount": 0,
                    "plyGroups": {str(i): {"sum": 0.0, "count": 0} for i in range(1, 31)},
                },
            )
            nodes = [
                node
                for node in analysis.get("nodes", [])
                if isinstance(node, dict) and norm(node.get("playerColor")).lower() == norm(player.get("color")).lower()
            ]
            total = sum(float(node.get("lossClipped") or 0) for node in nodes)
            count = len(nodes)
            bucket["nodeCount"] += count
            bucket["totalLoss"] += total
            for field in (
                "moveThinkingTimeCount", "moveThinkingTimeTotalMs", "eventCount",
                "eventThinkingTimeCount", "eventThinkingTimeTotalMs", "passCount",
                "terminalEventCount",
            ):
                bucket[field] += int(player.get(field) or 0)
            bucket["games"].append(
                {
                    "round": analysis.get("round"),
                    "table": analysis.get("table"),
                    "gameId": game_key,
                    "totalLoss": round(total, 3),
                    "averageLoss": round(total / count, 3) if count else None,
                    "nodeCount": count,
                    "moveThinkingTimeCount": int(player.get("moveThinkingTimeCount") or 0),
                    "moveThinkingTimeTotalMs": int(player.get("moveThinkingTimeTotalMs") or 0),
                    "moveThinkingTimeAverageMs": player.get("moveThinkingTimeAverageMs"),
                    "eventCount": int(player.get("eventCount") or 0),
                    "eventThinkingTimeCount": int(player.get("eventThinkingTimeCount") or 0),
                    "eventThinkingTimeTotalMs": int(player.get("eventThinkingTimeTotalMs") or 0),
                    "passCount": int(player.get("passCount") or 0),
                    "terminalEventCount": int(player.get("terminalEventCount") or 0),
                    "offlineFilled": False,
                }
            )
            for node in nodes:
                group = int(node.get("plyGroup") or 0)
                if 1 <= group <= 30:
                    item = bucket["plyGroups"][str(group)]
                    item["sum"] += float(node.get("lossClipped") or 0)
                    item["count"] += 1
            game_summary["players"].append(
                {
                    "key": key,
                    "name": bucket["name"],
                    "account": bucket["account"],
                    "color": norm(player.get("color")),
                    "ftdSide": norm(player.get("ftdSide")),
                    "totalLoss": round(total, 3),
                    "averageLoss": round(total / count, 3) if count else None,
                    "nodeCount": count,
                    "moveThinkingTimeCount": int(player.get("moveThinkingTimeCount") or 0),
                    "moveThinkingTimeTotalMs": int(player.get("moveThinkingTimeTotalMs") or 0),
                    "moveThinkingTimeAverageMs": player.get("moveThinkingTimeAverageMs"),
                    "eventCount": int(player.get("eventCount") or 0),
                    "eventThinkingTimeCount": int(player.get("eventThinkingTimeCount") or 0),
                    "eventThinkingTimeTotalMs": int(player.get("eventThinkingTimeTotalMs") or 0),
                    "passCount": int(player.get("passCount") or 0),
                    "terminalEventCount": int(player.get("terminalEventCount") or 0),
                }
            )
        games.append(game_summary)

    player_rows = []
    for bucket in players.values():
        node_count = int(bucket["nodeCount"])
        avg_game_loss = None
        valid_games = [game for game in bucket["games"] if game.get("nodeCount")]
        if valid_games:
            avg_game_loss = sum(float(game["totalLoss"]) for game in valid_games) / len(valid_games)
        ply_groups = {}
        for group, item in bucket["plyGroups"].items():
            count = int(item["count"])
            ply_groups[group] = {
                "averageLoss": round(float(item["sum"]) / count, 3) if count else None,
                "count": count,
            }
        player_rows.append(
            {
                "key": bucket["key"],
                "name": bucket["name"],
                "account": bucket["account"],
                "gameCount": len(valid_games),
                "nodeCount": node_count,
                "totalLoss": round(float(bucket["totalLoss"]), 3),
                "averageLoss": round(float(bucket["totalLoss"]) / node_count, 3) if node_count else None,
                "averageGameLoss": round(avg_game_loss, 3) if avg_game_loss is not None else None,
                "moveThinkingTimeCount": int(bucket["moveThinkingTimeCount"]),
                "moveThinkingTimeTotalMs": int(bucket["moveThinkingTimeTotalMs"]),
                "moveThinkingTimeAverageMs": round(
                    float(bucket["moveThinkingTimeTotalMs"]) / int(bucket["moveThinkingTimeCount"]), 3
                ) if int(bucket["moveThinkingTimeCount"]) else None,
                "eventCount": int(bucket["eventCount"]),
                "eventThinkingTimeCount": int(bucket["eventThinkingTimeCount"]),
                "eventThinkingTimeTotalMs": int(bucket["eventThinkingTimeTotalMs"]),
                "passCount": int(bucket["passCount"]),
                "terminalEventCount": int(bucket["terminalEventCount"]),
                "games": bucket["games"],
                "plyGroups": ply_groups,
            }
        )
    player_rows.sort(key=lambda row: (row["averageLoss"] is None, row["averageLoss"] or 999999, row["name"]))
    return {
        "schema": "ega-competition-summary-v1",
        "updatedAt": now_iso(),
        "playerCount": len(player_rows),
        "gameCount": len(games),
        "players": player_rows,
        "games": games,
    }


def update_state_summary(
    state_path: Path,
    state: dict[str, Any],
    summary: dict[str, Any],
    cache_dir: Path,
    round_limit: int,
    args: argparse.Namespace,
) -> None:
    pairing_loss_by_round: dict[str, dict[str, Any]] = {}
    for game in summary.get("games", []):
        if not isinstance(game, dict):
            continue
        round_key = str(game.get("round") or "")
        table_key = str(game.get("table") or "")
        if not round_key or not table_key:
            continue
        round_bucket = pairing_loss_by_round.setdefault(round_key, {})
        black_info = game.get("ftdBlack") if isinstance(game.get("ftdBlack"), dict) else game.get("black") if isinstance(game.get("black"), dict) else {}
        white_info = game.get("ftdWhite") if isinstance(game.get("ftdWhite"), dict) else game.get("white") if isinstance(game.get("white"), dict) else {}
        round_bucket[table_key] = {
            "round": game.get("round"),
            "table": game.get("table"),
            "gameId": game.get("gameId") or "",
            "cacheFile": game.get("cacheFile") or "",
            "blackName": norm(black_info.get("name")),
            "whiteName": norm(white_info.get("name")),
            "blackAccount": norm(black_info.get("account")),
            "whiteAccount": norm(white_info.get("account")),
            "players": game.get("players") if isinstance(game.get("players"), list) else [],
        }
    state["egaAnalysis"] = {
        "schema": "ega-analysis-state-v1",
        "updatedAt": summary.get("updatedAt") or now_iso(),
        "scope": "prelim-only",
        "roundLimit": round_limit,
        "summaryFile": str((cache_dir / "summary.json").resolve()),
        "gameCount": summary.get("gameCount", 0),
        "playerCount": summary.get("playerCount", 0),
        "topPlayers": summary.get("players", [])[:10],
        "pairingLossByRound": pairing_loss_by_round,
        "engine": {
            "name": "Egaroucid for Console",
            "path": str(Path(args.engine).resolve()),
            "level": int(args.level),
            "threads": int(args.threads),
            "hash": int(args.hash),
            "book": str(args.book or "enabled-default"),
        },
    }
    if bool(getattr(args, "direct_file", False)):
        if state_path.resolve() == DEFAULT_STATE_PATH.resolve():
            raise RuntimeError("--direct-file is fixture/test-only and cannot write the live shared state")
        atomic_write_json(state_path, state)
    else:
        require_agent_helpers().write_frontend_state(state_path, state, False)


def has_prelim_pairings(state: dict[str, Any], round_limit: int) -> bool:
    helper = state.get("scoreHelper") if isinstance(state.get("scoreHelper"), dict) else {}
    rounds = helper.get("rounds") if isinstance(helper.get("rounds"), list) else []
    return any(
        isinstance(round_item, dict)
        and isinstance(round_item.get("ftdPairings"), list)
        and round_item.get("ftdPairings")
        for round_item in rounds[:round_limit]
    )


def analyzed_game_path(cache_dir: Path, task: GameTask) -> Path:
    return cache_dir / f"game_{task.round_no}_{task.table}_{task.key}.json"


def cached_analysis_complete(task: GameTask, analysis: Any) -> bool:
    if not isinstance(analysis, dict):
        return False
    if analysis.get("schema") != "ega-game-analysis-v1":
        return False
    if int(analysis.get("round") or 0) != int(task.round_no):
        return False
    if int(analysis.get("table") or 0) != int(task.table):
        return False
    if norm(analysis.get("gameId")) != norm(task.game_id):
        return False
    expected_moves = oq_moves(task.detail)
    nodes = analysis.get("nodes")
    if not isinstance(nodes, list):
        return False
    if int(analysis.get("moveCount") or -1) != len(expected_moves):
        return False
    if len(nodes) != len(expected_moves):
        return False
    for index, node in enumerate(nodes, start=1):
        if not isinstance(node, dict):
            return False
        if int(node.get("ply") or 0) != index:
            return False
        if norm(node.get("move")).lower() != expected_moves[index - 1]:
            return False
        if norm(node.get("playerColor")).lower() not in {"black", "white"}:
            return False
        if not isinstance(node.get("lossClipped"), (int, float)):
            return False
    return True


def open_persistent_engine(
    args: argparse.Namespace, cache_dir: Path, worker_id: int | None = None
) -> PersistentEngine:
    stderr_name = (
        "egaroucid-stderr.log"
        if worker_id is None
        else f"egaroucid-stderr-worker-{worker_id:02d}.log"
    )
    return PersistentEngine(
        Path(args.engine),
        int(args.level),
        int(args.threads),
        int(args.hash),
        cache_dir / stderr_name,
        Path(args.book) if args.book else None,
    )


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    state_path = Path(args.state)
    cache_dir = Path(args.cache_dir)
    state = (
        read_json(state_path)
        if bool(getattr(args, "direct_file", False))
        else require_agent_helpers().read_frontend_state(state_path, False)
    )
    if not has_prelim_pairings(state, int(args.round_limit)):
        return {"ok": True, "status": "waiting-no-prelim-pairings", "analyzed": 0}

    tasks = collect_prelim_tasks(state, int(args.round_limit), args.oq_base_url, int(args.oq_timeout))
    analyses = []
    pending = []
    incomplete_cache_count = 0
    for task in tasks:
        path = analyzed_game_path(cache_dir, task)
        if path.exists():
            try:
                cached = read_json(path)
                if cached_analysis_complete(task, cached):
                    analyses.append(cached)
                else:
                    incomplete_cache_count += 1
                    pending.append(task)
            except Exception:
                incomplete_cache_count += 1
                pending.append(task)
        else:
            pending.append(task)
    analyzed_count = 0
    analyzed_node_count = 0
    engine_restart_count = 0
    node_restart = max(0, int(getattr(args, "node_restart", 0) or 0))
    persistent_engine = getattr(args, "command", "") == "watch"
    engine: PersistentEngine | None = None
    try:
        if pending:
            if persistent_engine:
                engine = getattr(args, "_persistent_engine", None)
                if engine is None:
                    engine = open_persistent_engine(args, cache_dir)
                    setattr(args, "_persistent_engine", engine)
                    setattr(args, "_node_count_since_restart", 0)
            else:
                engine = open_persistent_engine(args, cache_dir)
            for task in pending:
                current_node_count = (
                    int(getattr(args, "_node_count_since_restart", 0) or 0)
                    if persistent_engine
                    else analyzed_node_count
                )
                if node_restart and current_node_count >= node_restart and engine is not None:
                    engine.close()
                    engine_restart_count += 1
                    if persistent_engine:
                        setattr(args, "_node_count_since_restart", 0)
                    else:
                        analyzed_node_count = 0
                    engine = open_persistent_engine(args, cache_dir)
                    if persistent_engine:
                        setattr(args, "_persistent_engine", engine)
                analysis = analyze_game(task, engine)
                atomic_write_json(analyzed_game_path(cache_dir, task), analysis)
                analyses.append(analysis)
                analyzed_count += 1
                node_count = len(analysis.get("nodes", []))
                analyzed_node_count += node_count
                if persistent_engine:
                    setattr(
                        args,
                        "_node_count_since_restart",
                        int(getattr(args, "_node_count_since_restart", 0) or 0) + node_count,
                    )
                if int(args.max_games or 0) and analyzed_count >= int(args.max_games):
                    break
    finally:
        if engine is not None and not persistent_engine:
            engine.close()

    summary = summarize_competition(cache_dir, analyses)
    atomic_write_json(cache_dir / "summary.json", summary)
    if not args.no_state_update:
        update_state_summary(state_path, state, summary, cache_dir, int(args.round_limit), args)
    return {
        "ok": True,
        "status": "ok",
        "taskCount": len(tasks),
        "pendingBeforeRun": len(pending),
        "incompleteCacheCount": incomplete_cache_count,
        "analyzed": analyzed_count,
        "analyzedNodeCountSinceRestart": analyzed_node_count,
        "persistentNodeCountSinceRestart": int(getattr(args, "_node_count_since_restart", 0) or 0),
        "engineRestartCount": engine_restart_count,
        "nodeRestart": node_restart,
        "summaryFile": str((cache_dir / "summary.json").resolve()),
        "stateUpdated": not args.no_state_update,
    }


def run_status(args: argparse.Namespace) -> dict[str, Any]:
    state = (
        read_json(Path(args.state))
        if bool(getattr(args, "direct_file", False))
        else require_agent_helpers().read_frontend_state(Path(args.state), False)
    )
    if not has_prelim_pairings(state, int(args.round_limit)):
        return {"ok": True, "status": "waiting-no-prelim-pairings", "taskCount": 0, "pendingCount": 0}
    tasks = collect_prelim_tasks(state, int(args.round_limit), args.oq_base_url, int(args.oq_timeout))
    cache_dir = Path(args.cache_dir)
    pending = []
    incomplete_cache_count = 0
    for task in tasks:
        path = analyzed_game_path(cache_dir, task)
        if not path.exists():
            pending.append(task)
            continue
        try:
            if not cached_analysis_complete(task, read_json(path)):
                incomplete_cache_count += 1
                pending.append(task)
        except Exception:
            incomplete_cache_count += 1
            pending.append(task)
    return {
        "ok": True,
        "status": "ok",
        "taskCount": len(tasks),
        "pendingCount": len(pending),
        "incompleteCacheCount": incomplete_cache_count,
        "cacheDir": str(cache_dir.resolve()),
        "summaryFile": str((cache_dir / "summary.json").resolve()),
    }


def transcript_moves(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    return [compact[index : index + 2] for index in range(0, len(compact), 2) if MOVE_RE.match(compact[index : index + 2])]


def run_analyze_transcript(args: argparse.Namespace) -> dict[str, Any]:
    with reserve_background_egaroucid_workers(1, label="background transcript analysis"):
        return _run_analyze_transcript(args)


def _run_analyze_transcript(args: argparse.Namespace) -> dict[str, Any]:
    moves = transcript_moves(args.moves)
    if not moves:
        raise RuntimeError("--moves did not contain any Othello coordinates")
    detail = {"position": {"moves": [{"m": move} for move in moves]}}
    task = GameTask(
        round_no=0,
        table=0,
        pairing_index=0,
        black_name=args.black_name,
        white_name=args.white_name,
        black_account=args.black_account,
        white_account=args.white_account,
        black_ftd_side="black",
        white_ftd_side="white",
        ftd_black_name=args.black_name,
        ftd_white_name=args.white_name,
        ftd_black_account=args.black_account,
        ftd_white_account=args.white_account,
        game_id=args.game_id or "transcript",
        detail=detail,
        source="transcript",
        ending_kind="normal",
    )
    cache_dir = Path(args.cache_dir)
    engine = PersistentEngine(
        Path(args.engine),
        int(args.level),
        int(args.threads),
        int(args.hash),
        cache_dir / "egaroucid-stderr.log",
        Path(args.book) if args.book else None,
    )
    try:
        analysis = analyze_game(task, engine)
    finally:
        engine.close()
    black = next((p for p in analysis["players"] if p.get("color") == "black"), {})
    white = next((p for p in analysis["players"] if p.get("color") == "white"), {})
    return {
        "ok": True,
        "moveCount": analysis["moveCount"],
        "black": black,
        "white": white,
        "firstNode": analysis["nodes"][0] if analysis["nodes"] else None,
        "lastNode": analysis["nodes"][-1] if analysis["nodes"] else None,
    }


def engine_version_text(engine_exe: Path) -> str:
    result = subprocess.run(
        [str(engine_exe), "-v"],
        cwd=str(engine_exe.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )
    return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())


def bundle_task(
    detail: dict[str, Any],
    ordinal: int,
    index_by_id: dict[str, dict[str, Any]],
    tournament_by_id: dict[str, dict[str, Any]],
) -> GameTask:
    game_id = norm(detail.get("id"))
    players = detail.get("players") if isinstance(detail.get("players"), list) else []
    if len(players) != 2:
        raise RuntimeError(f"bundle game {game_id!r} does not have exactly two OQ players")
    black_name = norm(players[0].get("name") or players[0].get("id"))
    white_name = norm(players[1].get("name") or players[1].get("id"))
    black_account = norm(players[0].get("id") or players[0].get("name"))
    white_account = norm(players[1].get("id") or players[1].get("name"))
    tournament = tournament_by_id.get(game_id)
    if tournament:
        ftd_black_name = norm(tournament.get("ftdBlack"))
        ftd_white_name = norm(tournament.get("ftdWhite"))
        ftd_black_account = norm(tournament.get("ftdBlackAccount"))
        ftd_white_account = norm(tournament.get("ftdWhiteAccount"))
        black_key = account_key(black_account)
        white_key = account_key(white_account)
        black_ftd_side = "black" if black_key == account_key(ftd_black_account) else "white"
        white_ftd_side = "black" if white_key == account_key(ftd_black_account) else "white"
        expected_keys = {account_key(ftd_black_account), account_key(ftd_white_account)}
        if {black_key, white_key} != expected_keys:
            raise RuntimeError(f"bundle/tournament account mismatch for game {game_id}")
        round_no = int(tournament.get("round") or 0)
        table = int(tournament.get("table") or 0)
    else:
        ftd_black_name = black_name
        ftd_white_name = white_name
        ftd_black_account = black_account
        ftd_white_account = white_account
        black_ftd_side = "black"
        white_ftd_side = "white"
        round_no = 0
        table = ordinal
    final_status = norm(index_by_id.get(game_id, {}).get("finalStatus"))
    return GameTask(
        round_no=round_no,
        table=table,
        pairing_index=ordinal - 1,
        black_name=black_name,
        white_name=white_name,
        black_account=black_account,
        white_account=white_account,
        black_ftd_side=black_ftd_side,
        white_ftd_side=white_ftd_side,
        ftd_black_name=ftd_black_name,
        ftd_white_name=ftd_white_name,
        ftd_black_account=ftd_black_account,
        ftd_white_account=ftd_white_account,
        game_id=game_id,
        detail=detail,
        source="oq-account-bundle",
        ending_kind=final_status,
    )


def bundle_cached_analysis_complete(
    task: GameTask,
    analysis: Any,
    engine_metadata: dict[str, Any],
) -> bool:
    if not cached_analysis_complete(task, analysis):
        return False
    stored_engine = analysis.get("engine") if isinstance(analysis.get("engine"), dict) else {}
    for field in ("path", "fileMetadata", "level", "threads", "hash", "book"):
        if stored_engine.get(field) != engine_metadata.get(field):
            return False
    source_items = list(detail_moves(task.detail))
    events = analysis.get("events") if isinstance(analysis.get("events"), list) else []
    if len(events) != len(source_items):
        return False
    for index, (source, event) in enumerate(zip(source_items, events)):
        if int(event.get("sourceMoveIndex", -1)) != index:
            return False
        source_time = source.get("t") if isinstance(source, dict) else None
        if event.get("thinkingTimeMs") != source_time:
            return False
        if event.get("eventType") == "move":
            if not isinstance(event.get("boardBefore"), str) or len(event["boardBefore"]) != 65:
                return False
            if not isinstance(event.get("legalMoveCount"), int):
                return False
    return True


def run_analyze_bundle(args: argparse.Namespace) -> dict[str, Any]:
    validator = (
        validate_background_egaroucid_workers
        if args.background
        else validate_egaroucid_workers
    )
    reserver = (
        reserve_background_egaroucid_workers
        if args.background
        else reserve_egaroucid_workers
    )
    worker_count = validator(
        args.workers,
        label="background Level22 bundle" if args.background else "competition Level22 bundle",
    )
    with reserver(worker_count, label="background Level22 bundle" if args.background else "competition Level22 bundle"):
        return _run_analyze_bundle(args, worker_count)


def _run_analyze_bundle(args: argparse.Namespace, worker_count: int) -> dict[str, Any]:
    bundle_path = Path(args.bundle).resolve()
    tournament_path = Path(args.tournament_bundle).resolve() if args.tournament_bundle else None
    cache_dir = Path(args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    bundle = read_json(bundle_path)
    details = bundle.get("details") if isinstance(bundle.get("details"), list) else []
    index = bundle.get("index") if isinstance(bundle.get("index"), list) else []
    if not details or len(details) != len(index):
        raise RuntimeError("bundle index/details are missing or have different counts")
    index_by_id = {norm(item.get("id")): item for item in index if isinstance(item, dict)}
    if len(index_by_id) != len(details):
        raise RuntimeError("bundle game IDs are not unique")
    tournament_data = read_json(tournament_path) if tournament_path else {}
    tournament_games = tournament_data.get("games") if isinstance(tournament_data.get("games"), list) else []
    tournament_by_id = {
        norm(item.get("oqGameId")): item for item in tournament_games if isinstance(item, dict)
    }
    details = sorted(details, key=lambda detail: norm(detail.get("created")))
    tasks = [
        bundle_task(detail, ordinal, index_by_id, tournament_by_id)
        for ordinal, detail in enumerate(details, start=1)
    ]
    engine_exe = Path(args.engine).resolve()
    expected_engine = {
        "name": "Egaroucid for Console",
        "path": str(engine_exe),
        "fileMetadata": file_metadata(engine_exe),
        "level": int(args.level),
        "threads": int(args.threads),
        "hash": int(args.hash),
        "book": str(Path(args.book).resolve()) if args.book else "enabled-default",
    }
    version = engine_version_text(engine_exe)
    analyses: list[dict[str, Any]] = []
    pending: list[GameTask] = []
    for task in tasks:
        path = analyzed_game_path(cache_dir, task)
        if path.exists():
            try:
                cached = read_json(path)
                cached_worker_id = cached.get("bundleWorkerId")
                if (
                    bundle_cached_analysis_complete(task, cached, expected_engine)
                    and isinstance(cached_worker_id, int)
                    and 0 <= cached_worker_id < worker_count
                ):
                    analyses.append(cached)
                    continue
            except Exception:
                pass
        pending.append(task)

    analyzed = 0
    restart_count = 0
    node_restart = max(0, int(args.node_restart))
    task_queue: queue.Queue[GameTask | None] = queue.Queue()
    result_queue: queue.Queue[tuple[str, int, GameTask | None, Any]] = queue.Queue()
    for task in pending:
        task_queue.put(task)
    for _ in range(worker_count):
        task_queue.put(None)

    def worker_main(worker_id: int) -> None:
        engine: PersistentEngine | None = None
        worker_nodes = 0
        worker_restarts = 0
        try:
            while True:
                task = task_queue.get()
                if task is None:
                    break
                try:
                    if engine is None:
                        engine = open_persistent_engine(args, cache_dir, worker_id)
                    if node_restart and worker_nodes >= node_restart:
                        engine.close()
                        worker_restarts += 1
                        worker_nodes = 0
                        engine = open_persistent_engine(args, cache_dir, worker_id)
                    analysis = analyze_game(task, engine)
                    tournament = tournament_by_id.get(task.game_id)
                    analysis["created"] = norm(task.detail.get("created"))
                    analysis["finalStatus"] = norm(index_by_id[task.game_id].get("finalStatus"))
                    analysis["isTournamentGame"] = tournament is not None
                    analysis["tournament"] = tournament if tournament is not None else None
                    analysis["bundleWorkerId"] = worker_id
                    atomic_write_json(analyzed_game_path(cache_dir, task), analysis)
                    worker_nodes += len(analysis.get("nodes", []))
                    result_queue.put(("ok", worker_id, task, analysis))
                except BaseException as exc:
                    result_queue.put(("error", worker_id, task, exc))
                    break
        finally:
            if engine is not None:
                engine.close()
            result_queue.put(("done", worker_id, None, worker_restarts))

    workers = [
        threading.Thread(target=worker_main, args=(worker_id,), name=f"level22-worker-{worker_id:02d}")
        for worker_id in range(worker_count)
    ]
    for worker in workers:
        worker.start()
    done_workers = 0
    failures: list[str] = []
    while done_workers < worker_count:
        status, worker_id, task, payload = result_queue.get()
        if status == "done":
            restart_count += int(payload)
            done_workers += 1
            continue
        if status == "error":
            game_id = task.game_id if task is not None else "unknown"
            failures.append(f"worker {worker_id} game {game_id}: {payload}")
            continue
        analysis = payload
        analyses.append(analysis)
        analyzed += 1
        print(
            f"[{analyzed}/{len(pending)}] worker {worker_id:02d} completed bundle game {task.game_id}",
            flush=True,
        )
        atomic_write_json(
            cache_dir / "progress.json",
            {
                "schema": "ega-bundle-progress-v1",
                "updatedAt": now_iso(),
                "completedGameIds": sorted(item.get("gameId") for item in analyses),
                "completedCount": len(analyses),
                "totalCount": len(tasks),
                "workerCount": worker_count,
                "workerBudget": worker_budget_info(),
                "workerMode": "background" if args.background else "competition",
                "threadsPerConsole": int(args.threads),
                "engine": {**expected_engine, "version": version},
            },
        )
    for worker in workers:
        worker.join()
    if failures:
        raise RuntimeError("parallel bundle analysis failed: " + "; ".join(failures))

    analyses.sort(key=lambda item: norm(item.get("created")))
    summary = summarize_competition(cache_dir, analyses)
    summary.update(
        {
            "schema": "ega-account-bundle-summary-v1",
            "scope": "public-account-endpoint-exposed-games",
            "account": norm(bundle.get("account")),
            "sourceBundle": str(bundle_path),
            "sourceBundleMetadata": file_metadata(bundle_path),
            "tournamentBundle": str(tournament_path) if tournament_path else None,
            "tournamentBundleMetadata": file_metadata(tournament_path) if tournament_path else None,
            "tournamentGameCount": sum(bool(item.get("isTournamentGame")) for item in analyses),
            "engine": {**expected_engine, "version": version},
            "workerCount": worker_count,
            "workerBudget": worker_budget_info(),
            "workerMode": "background" if args.background else "competition",
            "threadsPerConsole": int(args.threads),
            "engineRestartCount": restart_count,
        }
    )
    atomic_write_json(cache_dir / "summary.json", summary)
    return {
        "ok": True,
        "gameCount": len(tasks),
        "analyzed": analyzed,
        "cached": len(tasks) - len(pending),
        "tournamentGameCount": summary["tournamentGameCount"],
        "summaryFile": str((cache_dir / "summary.json").resolve()),
        "engine": summary["engine"],
    }


def lock_path(cache_dir: Path) -> Path:
    return cache_dir / "worker.lock"


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except (OSError, SystemError):
        return False
    return True


def remove_stale_lock(cache_dir: Path) -> bool:
    path = lock_path(cache_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except Exception:
        return False
    pid = int(payload.get("pid") or 0)
    if process_exists(pid):
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return True


def acquire_lock(cache_dir: Path) -> None:
    global _LOCK_HANDLE
    path = lock_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(f"analysis worker already appears to be running: {path}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({"pid": os.getpid(), "startedAt": now_iso()}, ensure_ascii=False))
    handle.flush()
    os.fsync(handle.fileno())
    _LOCK_HANDLE = handle


def release_lock(cache_dir: Path) -> None:
    global _LOCK_HANDLE
    handle = _LOCK_HANDLE
    _LOCK_HANDLE = None
    if handle is not None:
        try:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass
    try:
        lock_path(cache_dir).unlink()
    except FileNotFoundError:
        pass
    except PermissionError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Egaroucid stone-loss analyzer for prelim OQ games")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "once", "watch"):
        p = sub.add_parser(name)
        p.add_argument("--state", default=str(DEFAULT_STATE_PATH))
        p.add_argument("--direct-file", action="store_true", help="Test/fixture mode only; bypasses the sole-writer server")
        p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
        p.add_argument("--engine", default=str(DEFAULT_ENGINE_EXE))
        p.add_argument("--level", type=int, default=22)
        p.add_argument("--threads", type=int, default=32)
        p.add_argument("--hash", type=int, default=26)
        p.add_argument("--book", default="", help="Optional explicit Egaroucid book file; default keeps engine book enabled")
        p.add_argument("--round-limit", type=int, default=7, help="Prelim rounds only")
        p.add_argument("--oq-base-url", default="http://questgames.net")
        p.add_argument("--oq-timeout", type=int, default=20)
        p.add_argument("--max-games", type=int, default=0)
        p.add_argument("--node-restart", type=int, default=1000, help="Restart the persistent Egaroucid engine after this many analyzed move nodes; 0 disables")
        p.add_argument("--no-state-update", action="store_true")
        if name == "watch":
            p.add_argument("--interval", type=float, default=20.0)
            p.add_argument("--idle-exit-after", type=int, default=0, help="Exit after N idle polls after all imported prelim games are analyzed; 0 disables")
    p_transcript = sub.add_parser("analyze-transcript")
    p_transcript.add_argument("--moves", required=True)
    p_transcript.add_argument("--black-name", default="Black")
    p_transcript.add_argument("--white-name", default="White")
    p_transcript.add_argument("--black-account", default="black")
    p_transcript.add_argument("--white-account", default="white")
    p_transcript.add_argument("--game-id", default="transcript")
    p_transcript.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p_transcript.add_argument("--engine", default=str(DEFAULT_ENGINE_EXE))
    p_transcript.add_argument("--level", type=int, default=22)
    p_transcript.add_argument("--threads", type=int, default=32)
    p_transcript.add_argument("--hash", type=int, default=26)
    p_transcript.add_argument("--book", default="")
    p_bundle = sub.add_parser("analyze-bundle")
    p_bundle.add_argument("--bundle", required=True)
    p_bundle.add_argument("--tournament-bundle", default="")
    p_bundle.add_argument("--cache-dir", required=True)
    p_bundle.add_argument("--engine", default=str(DEFAULT_ENGINE_EXE))
    p_bundle.add_argument("--level", type=int, default=22)
    p_bundle.add_argument("--threads", type=int, default=16)
    p_bundle.add_argument("--hash", type=int, default=25)
    p_bundle.add_argument("--workers", type=int)
    p_bundle.add_argument(
        "--background", action="store_true",
        help="use the background 50%% physical-memory budget instead of the competition cap of 2",
    )
    p_bundle.add_argument("--book", default="")
    p_bundle.add_argument("--node-restart", type=int, default=1000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cache_dir = Path(args.cache_dir)
    if args.command == "status":
        print(json.dumps(run_status(args), ensure_ascii=False, indent=2))
        return 0
    if args.command == "analyze-transcript":
        print(json.dumps(run_analyze_transcript(args), ensure_ascii=False, indent=2))
        return 0
    if args.command == "analyze-bundle":
        if args.workers is not None and int(args.workers) <= 0:
            raise ValueError("--workers must be positive")
        acquire_lock(cache_dir)
        try:
            print(json.dumps(run_analyze_bundle(args), ensure_ascii=False, indent=2))
            return 0
        finally:
            release_lock(cache_dir)
    acquire_lock(cache_dir)
    reservation = None
    try:
        reservation = reserve_egaroucid_workers(1, label=f"Egaroucid {args.command}")
        reservation.acquire()
        if args.command == "once":
            print(json.dumps(run_once(args), ensure_ascii=False, indent=2))
            return 0
        idle = 0
        try:
            while True:
                result = run_once(args)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if result.get("pendingBeforeRun", 0) == 0 and result.get("taskCount", 0) > 0:
                    idle += 1
                else:
                    idle = 0
                if int(args.idle_exit_after or 0) and idle >= int(args.idle_exit_after):
                    return 0
                time.sleep(max(2.0, float(args.interval or 20.0)))
        finally:
            engine = getattr(args, "_persistent_engine", None)
            if engine is not None:
                try:
                    engine.close()
                finally:
                    setattr(args, "_persistent_engine", None)
    finally:
        if reservation is not None:
            reservation.release()
        release_lock(cache_dir)


if __name__ == "__main__":
    raise SystemExit(main())
