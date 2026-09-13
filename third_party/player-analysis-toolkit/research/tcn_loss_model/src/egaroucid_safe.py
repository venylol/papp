"""Strict Egaroucid Console transactions for auditable hint computation.

The engine process is stateful.  A search is therefore exposed only as one locked
``setboard + hint + complete response read`` transaction.  Both command responses
must carry Egaroucid's native ASCII board and are retained verbatim by callers.
"""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROMPT_RE = re.compile(r"(?:\A|\r?\n)>\s")
BOARD_ROW_RE = re.compile(
    r"^\s*([1-8])\s+([.XO])\s+([.XO])\s+([.XO])\s+([.XO])\s+"
    r"([.XO])\s+([.XO])\s+([.XO])\s+([.XO])(?:\s+.*)?$",
    flags=re.IGNORECASE,
)
SIDE_RE = re.compile(r"\b(BLACK|WHITE)\s+to\s+move\b", flags=re.IGNORECASE)
HINT_ROW_RE = re.compile(r"^\|.*\|$")
MOVE_RE = re.compile(r"^[a-h][1-8]$")
DIRECTIONS = (
    (-1, -1), (-1, 0), (-1, 1), (0, -1),
    (0, 1), (1, -1), (1, 0), (1, 1),
)


@dataclass(frozen=True)
class HintTransaction:
    request_board_setboard: str
    setboard_response_board_setboard: str
    hint_response_board_setboard: str
    setboard_response_board_count: int
    hint_response_board_count: int
    hints: list[dict[str, Any]]
    setboard_raw_response: str
    hint_raw_response: str
    elapsed_seconds: float


class EgaroucidTransactionError(RuntimeError):
    """A failed transaction with every response fragment available at failure time."""

    def __init__(
        self,
        message: str,
        *,
        request_board: str,
        setboard_raw_response: str,
        hint_raw_response: str,
        diagnostic: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.request_board = request_board
        self.setboard_raw_response = setboard_raw_response
        self.hint_raw_response = hint_raw_response
        self.diagnostic = diagnostic

    def evidence(self) -> dict[str, Any]:
        return {
            "requestBoard": self.request_board,
            "setboardRawResponse": self.setboard_raw_response,
            "hintRawResponse": self.hint_raw_response,
            "engineDiagnostic": self.diagnostic,
        }


def normalize_setboard(board: str) -> str:
    token = "".join(str(board).split()).upper().replace(".", "-")
    if len(token) != 65:
        raise ValueError(f"setboard token must contain 65 cells/side characters, got {len(token)}")
    if any(cell not in "-XO" for cell in token[:64]):
        raise ValueError("setboard board contains characters outside '-', 'X', and 'O'")
    if token[64] not in "XO":
        raise ValueError("setboard side suffix must be X or O")
    return token


def _parse_board_block(lines: list[str], start: int) -> tuple[str, set[int]]:
    rows: list[str] = []
    consumed: set[int] = set()
    side_values: list[str] = []
    for offset, expected_row in enumerate(range(1, 9)):
        line_index = start + offset
        if line_index >= len(lines):
            raise RuntimeError("native Console board ends before row 8")
        line = lines[line_index]
        match = BOARD_ROW_RE.match(line)
        if match is None or int(match.group(1)) != expected_row:
            raise RuntimeError(
                f"native Console board row sequence is incomplete at expected row {expected_row}: {line!r}"
            )
        rows.append("".join(match.groups()[1:]).upper().replace(".", "-"))
        consumed.add(line_index)
        side_values.extend(
            "X" if value.upper() == "BLACK" else "O"
            for value in SIDE_RE.findall(line)
        )
    if len(side_values) != 1:
        raise RuntimeError(
            f"native Console board must contain exactly one side-to-move value, got {side_values}"
        )
    return "".join(rows) + side_values[0], consumed


def parse_native_console_boards(output: str) -> list[str]:
    """Return all complete native boards while rejecting stray/duplicate row structure."""
    lines = output.splitlines()
    matched_rows = {index for index, line in enumerate(lines) if BOARD_ROW_RE.match(line)}
    boards: list[str] = []
    consumed: set[int] = set()
    index = 0
    while index < len(lines):
        match = BOARD_ROW_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        if int(match.group(1)) != 1:
            raise RuntimeError(f"stray or duplicate native Console board row: {lines[index]!r}")
        board, block_rows = _parse_board_block(lines, index)
        boards.append(board)
        consumed.update(block_rows)
        index += 8
    if not boards:
        raise RuntimeError(f"response lacks a complete native Console board: {output!r}")
    if consumed != matched_rows:
        raise RuntimeError("response contains unconsumed or duplicate native Console board rows")
    return boards


def parse_unique_native_console_board(output: str) -> tuple[str, int]:
    boards = parse_native_console_boards(output)
    unique = set(boards)
    if len(unique) != 1:
        raise RuntimeError(f"response contains conflicting native Console boards: {boards}")
    return boards[0], len(boards)


def parse_hint_rows(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        text = line.strip()
        if not HINT_ROW_RE.match(text):
            continue
        parts = [part.strip() for part in text.split("|")[1:-1]]
        if not parts or parts[0].lower() == "level":
            continue
        if len(parts) != 7:
            raise RuntimeError(f"unexpected hint table row shape: {line!r}")
        move = parts[2].lower()
        if not MOVE_RE.fullmatch(move):
            raise RuntimeError(f"invalid hint move token: {move!r}")
        try:
            score = int(parts[3].replace("+", ""))
            nodes = int(parts[5])
            nps = int(parts[6])
        except ValueError as exc:
            raise RuntimeError(f"invalid numeric hint field: {line!r}") from exc
        if not parts[1]:
            raise RuntimeError(f"empty hint depth: {line!r}")
        rows.append(
            {
                "level_text": parts[0],
                "depth": parts[1],
                "move": move,
                "score": score,
                "time": parts[4],
                "nodes": nodes,
                "nps": nps,
                "is_book": parts[0].lower() == "book",
            }
        )
    if not rows:
        raise RuntimeError(f"hint response contains no result rows: {output!r}")
    return rows


def legal_moves_from_setboard(board: str) -> list[str]:
    token = normalize_setboard(board)
    cells = token[:64]
    player = token[64]
    opponent = "O" if player == "X" else "X"
    legal: list[str] = []
    for row in range(8):
        for col in range(8):
            if cells[row * 8 + col] != "-":
                continue
            captures = False
            for dr, dc in DIRECTIONS:
                rr, cc = row + dr, col + dc
                seen_opponent = False
                while 0 <= rr < 8 and 0 <= cc < 8 and cells[rr * 8 + cc] == opponent:
                    seen_opponent = True
                    rr += dr
                    cc += dc
                if (
                    seen_opponent
                    and 0 <= rr < 8
                    and 0 <= cc < 8
                    and cells[rr * 8 + cc] == player
                ):
                    captures = True
                    break
            if captures:
                legal.append(f"{chr(ord('a') + col)}{row + 1}")
    return legal


def validate_hint_transaction(
    transaction: HintTransaction,
    legal_moves: list[str],
    requested_count: int,
) -> None:
    request_board = normalize_setboard(transaction.request_board_setboard)
    if transaction.setboard_response_board_setboard != request_board:
        raise RuntimeError("setboard response board differs from request board")
    if transaction.hint_response_board_setboard != request_board:
        raise RuntimeError("hint response board differs from request board")
    independently_legal = legal_moves_from_setboard(request_board)
    if set(independently_legal) != set(legal_moves) or len(independently_legal) != len(legal_moves):
        raise RuntimeError(
            f"source legal moves disagree with independently derived moves: "
            f"source={legal_moves} derived={independently_legal}"
        )
    expected = min(requested_count, len(independently_legal))
    if len(transaction.hints) != expected:
        raise RuntimeError(f"expected {expected} hint rows, got {len(transaction.hints)}")
    moves = [str(row["move"]).lower() for row in transaction.hints]
    if len(set(moves)) != len(moves):
        raise RuntimeError(f"hint response contains duplicate candidates: {moves}")
    illegal = [move for move in moves if move not in independently_legal]
    if illegal:
        raise RuntimeError(f"hint response contains illegal candidates: {illegal}")


class AtomicEgaroucid:
    """One persistent engine with an indivisible board-search transaction API."""

    def __init__(
        self,
        engine_exe: Path,
        *,
        level: int,
        threads: int,
        hash_level: int,
        use_book: bool,
    ) -> None:
        engine_exe = engine_exe.resolve()
        if not engine_exe.is_file():
            raise FileNotFoundError(f"Egaroucid Console not found: {engine_exe}")
        args = [
            str(engine_exe),
            "-l", str(level),
            "-t", str(threads),
            "-hash", str(hash_level),
            "-noautocacheclear",
        ]
        if not use_book:
            args.append("-nobook")
        if "-q" in args or "-noboard" in args:
            raise AssertionError("formal Console sessions must echo native boards")
        self.args = tuple(args)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._buffer = ""
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            args,
            cwd=str(engine_exe.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=0,
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("failed to open Egaroucid Console pipes")
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        self.startup_raw_response = self._wait_for_prompt(timeout=60.0)

    def _read_output(self) -> None:
        assert self.proc.stdout is not None
        try:
            while True:
                chunk = self.proc.stdout.read(1)
                if chunk == "":
                    break
                self._queue.put(chunk)
        finally:
            self._queue.put(None)

    def _wait_for_prompt(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while True:
            match = PROMPT_RE.search(self._buffer)
            if match is not None:
                response = self._buffer[: match.start()]
                self._buffer = self._buffer[match.end() :]
                return response
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for Egaroucid Console prompt")
            try:
                chunk = self._queue.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"Egaroucid Console exited with code {self.proc.returncode}")
                continue
            if chunk is None:
                raise RuntimeError("Egaroucid Console output stream closed")
            self._buffer += chunk

    def _command_unlocked(self, command: str, timeout: float) -> str:
        assert self.proc.stdin is not None
        self.proc.stdin.write(command.rstrip() + "\n")
        self.proc.stdin.flush()
        return self._wait_for_prompt(timeout=timeout)

    def hint_for_board(self, board: str, count: int, timeout: float) -> HintTransaction:
        request_board = normalize_setboard(board)
        if count <= 0:
            raise ValueError("hint count must be positive")
        started = time.monotonic()
        setboard_raw = ""
        hint_raw = ""
        try:
            with self._lock:
                setboard_raw = self._command_unlocked(f"setboard {request_board}", timeout=30.0)
                setboard_board, setboard_count = parse_unique_native_console_board(setboard_raw)
                if setboard_board != request_board:
                    raise RuntimeError(
                        f"setboard round-trip mismatch: request={request_board} response={setboard_board}"
                    )
                hint_raw = self._command_unlocked(f"hint {count}", timeout=timeout)
                hint_board, hint_board_count = parse_unique_native_console_board(hint_raw)
                if hint_board != request_board:
                    raise RuntimeError(
                        f"hint response board mismatch: request={request_board} response={hint_board}"
                    )
                hints = parse_hint_rows(hint_raw)
        except Exception as exc:
            raise EgaroucidTransactionError(
                f"atomic Egaroucid transaction failed: {exc!r}",
                request_board=request_board,
                setboard_raw_response=setboard_raw,
                hint_raw_response=hint_raw,
                diagnostic=self.diagnostic_snapshot(),
            ) from exc
        return HintTransaction(
            request_board_setboard=request_board,
            setboard_response_board_setboard=setboard_board,
            hint_response_board_setboard=hint_board,
            setboard_response_board_count=setboard_count,
            hint_response_board_count=hint_board_count,
            hints=hints,
            setboard_raw_response=setboard_raw,
            hint_raw_response=hint_raw,
            elapsed_seconds=time.monotonic() - started,
        )

    def diagnostic_snapshot(self) -> dict[str, Any]:
        return {
            "args": list(self.args),
            "returnCode": self.proc.poll(),
            "pendingBuffer": self._buffer,
            "queuedCharactersApproximate": self._queue.qsize(),
        }

    def close(self) -> None:
        try:
            if self.proc.stdin is not None and self.proc.poll() is None:
                self.proc.stdin.write("exit\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=5.0)
        except Exception:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5.0)

    def __enter__(self) -> "AtomicEgaroucid":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
