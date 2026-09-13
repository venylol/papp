"""Minimal Othello replay board that preserves explicit pass events."""

from __future__ import annotations

import re


MOVE_RE = re.compile(r"^[a-h][1-8]$", re.IGNORECASE)


class OthelloBoard:
    directions = (
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1), (0, 1),
        (1, -1), (1, 0), (1, 1),
    )

    def __init__(self) -> None:
        self.board = [["-" for _ in range(8)] for _ in range(8)]
        self.board[3][3] = "O"
        self.board[3][4] = "X"
        self.board[4][3] = "X"
        self.board[4][4] = "O"
        self.current = "X"

    @staticmethod
    def opponent(color: str) -> str:
        return "O" if color == "X" else "X"

    def captures(self, row: int, col: int, color: str) -> list[tuple[int, int]]:
        if self.board[row][col] != "-":
            return []
        other = self.opponent(color)
        captured: list[tuple[int, int]] = []
        for row_step, col_step in self.directions:
            next_row = row + row_step
            next_col = col + col_step
            line: list[tuple[int, int]] = []
            while (
                0 <= next_row < 8
                and 0 <= next_col < 8
                and self.board[next_row][next_col] == other
            ):
                line.append((next_row, next_col))
                next_row += row_step
                next_col += col_step
            if (
                line
                and 0 <= next_row < 8
                and 0 <= next_col < 8
                and self.board[next_row][next_col] == color
            ):
                captured.extend(line)
        return captured

    def legal_moves(self, color: str | None = None) -> list[str]:
        side = color or self.current
        return [
            f"{chr(ord('a') + col)}{row + 1}"
            for row in range(8)
            for col in range(8)
            if self.captures(row, col, side)
        ]

    def apply_move(self, move: str) -> str:
        text = str(move).strip().lower()
        played = self.current
        if text == "-":
            if self.legal_moves(played):
                raise ValueError(f"pass is illegal for {played}: legal moves are available")
            other = self.opponent(played)
            if not self.legal_moves(other):
                raise ValueError("pass is invalid after the game has ended")
            self.current = other
            return played
        if not MOVE_RE.fullmatch(text):
            raise ValueError(f"invalid move coordinate: {move!r}")
        row = int(text[1]) - 1
        col = ord(text[0]) - ord("a")
        flips = self.captures(row, col, played)
        if not flips:
            raise ValueError(f"illegal move {text} for side {played}")
        self.board[row][col] = played
        for flip_row, flip_col in flips:
            self.board[flip_row][flip_col] = played
        self.current = self.opponent(played)
        return played

    def to_setboard_str(self) -> str:
        return "".join(value for row in self.board for value in row) + self.current
