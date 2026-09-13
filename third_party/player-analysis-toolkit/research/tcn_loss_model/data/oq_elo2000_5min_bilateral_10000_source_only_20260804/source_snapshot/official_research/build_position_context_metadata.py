from __future__ import annotations

import argparse
import json
import math
import re
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd


DATA_PATH = Path("oq_reversi_5min_elo2000_hints/position_hints.csv")
OUT_PATH = Path("oq_reversi_5min_elo2000_hints/position_context_metadata.csv")
PROGRESS_PATH = Path("oq_reversi_5min_elo2000_hints/position_context_metadata_progress.json")

DIRECTIONS = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)
ORTHOGONAL_DIRECTIONS = ((-1, 0), (0, -1), (0, 1), (1, 0))

CORNER_STAR_PAIRS = ((0, 9), (7, 14), (56, 49), (63, 54))
X_SQUARES = {9, 14, 49, 54}
C_SQUARES = {1, 6, 8, 15, 48, 55, 57, 62}
XC_SQUARES = X_SQUARES | C_SQUARES
MOVE_COORD_RE = re.compile(r"\b([a-h][1-8])\b", re.IGNORECASE)
PRESSURE_EDGE_DEFINITIONS = (
    {
        "edge_squares": {8, 16, 24, 32, 40, 48},
        "inner_lane_squares": {17, 25, 33, 41},
    },
    {
        "edge_squares": {15, 23, 31, 39, 47, 55},
        "inner_lane_squares": {22, 30, 38, 46},
    },
    {
        "edge_squares": {1, 2, 3, 4, 5, 6},
        "inner_lane_squares": {10, 11, 12, 13},
    },
    {
        "edge_squares": {57, 58, 59, 60, 61, 62},
        "inner_lane_squares": {50, 51, 52, 53},
    },
)
EDGE_SQUARES = {idx for definition in PRESSURE_EDGE_DEFINITIONS for idx in definition["edge_squares"]}

FULL64 = (1 << 64) - 1


def write_progress(payload: dict[str, Any]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "updated_at_unix": time.time()}
    tmp = PROGRESS_PATH.with_suffix(PROGRESS_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PROGRESS_PATH)


def normalize_side(side: Any) -> str:
    text = str(side).strip().lower()
    if text in {"black", "x", "b"}:
        return "X"
    if text in {"white", "o", "w"}:
        return "O"
    return "X"


def opponent(side: str) -> str:
    return "O" if side == "X" else "X"


def normalize_board(board: Any) -> str:
    text = str(board).upper().replace(".", "-")
    chars = [(ch if ch in {"X", "O"} else "-") for ch in text[:64].ljust(64, "-")]
    return "".join(chars)


def in_bounds(row: int, col: int) -> bool:
    return 0 <= row < 8 and 0 <= col < 8


def idx_to_rc(idx: int) -> tuple[int, int]:
    return divmod(idx, 8)


def rc_to_idx(row: int, col: int) -> int:
    return row * 8 + col


def move_to_idx(move: Any) -> int | None:
    match = MOVE_COORD_RE.search(str(move))
    if not match:
        return None
    coord = match.group(1).lower()
    col = ord(coord[0]) - ord("a")
    row = int(coord[1]) - 1
    return rc_to_idx(row, col)


def move_flips(board: str, side: str, move_idx: int) -> list[int]:
    if board[move_idx] != "-":
        return []
    enemy = opponent(side)
    row, col = idx_to_rc(move_idx)
    flips: list[int] = []
    for dr, dc in DIRECTIONS:
        r, c = row + dr, col + dc
        captured: list[int] = []
        while in_bounds(r, c):
            idx = rc_to_idx(r, c)
            cell = board[idx]
            if cell == enemy:
                captured.append(idx)
                r += dr
                c += dc
                continue
            if cell == side and captured:
                flips.extend(captured)
            break
    return flips


def legal_moves(board: str, side: str) -> set[int]:
    return {idx for idx, cell in enumerate(board) if cell == "-" and move_flips(board, side, idx)}


def apply_move(board: str, side: str, move_idx: int) -> str:
    flips = move_flips(board, side, move_idx)
    if not flips:
        raise ValueError(f"illegal move {move_idx} for {side}")
    cells = list(board)
    cells[move_idx] = side
    for idx in flips:
        cells[idx] = side
    return "".join(cells)


def empty_regions(board: str, reference_side: str) -> list[dict[str, Any]]:
    side = reference_side
    opp = opponent(side)
    player_moves = legal_moves(board, side)
    opponent_moves = legal_moves(board, opp)
    visited: set[int] = set()
    regions: list[dict[str, Any]] = []
    region_id = 1
    for start, cell in enumerate(board):
        if cell != "-" or start in visited:
            continue
        stack = [start]
        visited.add(start)
        cells: list[int] = []
        while stack:
            idx = stack.pop()
            cells.append(idx)
            row, col = idx_to_rc(idx)
            for dr, dc in ORTHOGONAL_DIRECTIONS:
                r, c = row + dr, col + dc
                n = rc_to_idx(r, c)
                if in_bounds(r, c) and board[n] == "-" and n not in visited:
                    visited.add(n)
                    stack.append(n)

        boundary_colors: set[str] = set()
        boundary_squares: set[int] = set()
        for idx in cells:
            row, col = idx_to_rc(idx)
            for dr, dc in DIRECTIONS:
                r, c = row + dr, col + dc
                if not in_bounds(r, c):
                    continue
                n = rc_to_idx(r, c)
                if board[n] in {"X", "O"}:
                    boundary_colors.add(board[n])
                    boundary_squares.add(n)

        cell_set = set(cells)
        player_entries = player_moves & cell_set
        opponent_entries = opponent_moves & cell_set
        size = len(cells)
        all_boundary_discs_are_opponent = bool(boundary_squares) and boundary_colors == {opp}
        strict_independent = size % 2 == 1 and all_boundary_discs_are_opponent and not opponent_entries
        regions.append(
            {
                "region_id": region_id,
                "cells": sorted(cells),
                "size": size,
                "parity": "odd" if size % 2 else "even",
                "touches_edge": any(idx // 8 in {0, 7} or idx % 8 in {0, 7} for idx in cells),
                "player_can_enter_region": bool(player_entries),
                "opponent_can_enter_region": bool(opponent_entries),
                "strict_independent_odd_empty_flag": strict_independent,
            }
        )
        region_id += 1
    return regions


def independent_regions(board: str, side: str) -> list[dict[str, Any]]:
    return [r for r in empty_regions(board, side) if r["strict_independent_odd_empty_flag"]]


def disc_dispersion(board: str, idx: int) -> int:
    row, col = idx_to_rc(idx)
    total = 0
    for dr, dc in DIRECTIONS:
        r, c = row + dr, col + dc
        if in_bounds(r, c) and board[rc_to_idx(r, c)] == "-":
            total += 1
    return total


def disc_dispersion_excluding_xc_empty(board: str, idx: int) -> int:
    regular, _xc = disc_dispersion_split_xc_empty(board, idx)
    return regular


def disc_dispersion_split_xc_empty(board: str, idx: int) -> tuple[int, int]:
    row, col = idx_to_rc(idx)
    regular = 0
    xc = 0
    for dr, dc in DIRECTIONS:
        r, c = row + dr, col + dc
        if not in_bounds(r, c):
            continue
        n = rc_to_idx(r, c)
        if board[n] != "-":
            continue
        if n in XC_SQUARES:
            xc += 1
        else:
            regular += 1
    return regular, xc


def dispersion_summary(board: str, side: str) -> dict[str, float | int]:
    opp = opponent(side)

    def summarize(color: str) -> tuple[int, int, float, int]:
        values = [disc_dispersion(board, idx) for idx, cell in enumerate(board) if cell == color]
        if not values:
            return 0, 0, 0.0, 0
        total = int(sum(values))
        return len(values), total, total / len(values), int(max(values))

    side_count, side_total, side_avg, side_max = summarize(side)
    opp_count, opp_total, opp_avg, opp_max = summarize(opp)
    return {
        "side_dispersion_disc_count": side_count,
        "side_dispersion_total": side_total,
        "side_dispersion_avg": side_avg,
        "side_dispersion_max": side_max,
        "opponent_dispersion_disc_count": opp_count,
        "opponent_dispersion_total": opp_total,
        "opponent_dispersion_avg": opp_avg,
        "opponent_dispersion_max": opp_max,
        "dispersion_avg_diff": side_avg - opp_avg,
        "dispersion_total_diff": side_total - opp_total,
    }


def compact_move_dispersion_summary(board: str, side: str) -> dict[str, float | int]:
    values = []
    has_best_compact = False
    has_danger_compact = False
    for move_idx in legal_moves(board, side):
        flips = move_flips(board, side, move_idx)
        regular_sum = 0
        xc_sum = 0
        for idx in flips:
            regular, xc = disc_dispersion_split_xc_empty(board, idx)
            regular_sum += regular
            xc_sum += xc
        values.append(regular_sum)
        if regular_sum + xc_sum == 1:
            has_best_compact = has_best_compact or regular_sum == 1
            has_danger_compact = has_danger_compact or xc_sum == 1
    if not values:
        return {
            "legal_move_min_flipped_opponent_dispersion_excl_xc": math.nan,
            "has_best_compact_move": 0,
            "has_danger_compact_move": 0,
        }
    min_value = min(values)
    return {
        "legal_move_min_flipped_opponent_dispersion_excl_xc": int(min_value),
        "has_best_compact_move": int(has_best_compact),
        "has_danger_compact_move": int(has_danger_compact),
    }


def move_flip_count_and_dispersion(board: str, side: str, move: Any) -> tuple[float, float]:
    move_idx = move_to_idx(move)
    if move_idx is None or move_idx not in legal_moves(board, side):
        return math.nan, math.nan
    flips = move_flips(board, side, move_idx)
    dispersion = sum(disc_dispersion_excluding_xc_empty(board, idx) for idx in flips)
    return float(len(flips)), float(dispersion)


def hint6_dispersion_summary(board: str, side: str, hint6_1_move: Any, hint6_2_move: Any, hint6_3_move: Any) -> dict[str, float]:
    h1_flip, h1_disp = move_flip_count_and_dispersion(board, side, hint6_1_move)
    h2_flip, h2_disp = move_flip_count_and_dispersion(board, side, hint6_2_move)
    h3_flip, h3_disp = move_flip_count_and_dispersion(board, side, hint6_3_move)

    def diff(a: float, b: float) -> float:
        if math.isnan(a) or math.isnan(b):
            return math.nan
        return a - b

    flip_12_diff = diff(h1_flip, h2_flip)
    flip_13_diff = diff(h1_flip, h3_flip)
    disp_12_diff = diff(h1_disp, h2_disp)
    disp_13_diff = diff(h1_disp, h3_disp)
    return {
        "hint6_1_flipped_disc_count": h1_flip,
        "hint6_1_flipped_opponent_dispersion_excl_xc": h1_disp,
        "hint6_1_2_flipped_disc_count_diff": flip_12_diff,
        "hint6_1_3_flipped_disc_count_diff": flip_13_diff,
        "hint6_1_2_flipped_disc_count_abs_diff": abs(flip_12_diff) if not math.isnan(flip_12_diff) else math.nan,
        "hint6_1_3_flipped_disc_count_abs_diff": abs(flip_13_diff) if not math.isnan(flip_13_diff) else math.nan,
        "hint6_1_2_flipped_opponent_dispersion_excl_xc_diff": disp_12_diff,
        "hint6_1_3_flipped_opponent_dispersion_excl_xc_diff": disp_13_diff,
        "hint6_1_2_flipped_opponent_dispersion_excl_xc_abs_diff": abs(disp_12_diff) if not math.isnan(disp_12_diff) else math.nan,
        "hint6_1_3_flipped_opponent_dispersion_excl_xc_abs_diff": abs(disp_13_diff) if not math.isnan(disp_13_diff) else math.nan,
    }


def verify_black_hole_sequence(board: str, side: str, first: int, second: int) -> bool:
    if first not in legal_moves(board, side):
        return False
    next_board = apply_move(board, side, first)
    return second in legal_moves(next_board, side) and first not in legal_moves(next_board, opponent(side)) and second not in legal_moves(next_board, opponent(side))


def black_hole_counts(board: str, side: str) -> tuple[int, int]:
    side_moves = legal_moves(board, side)
    opp_moves = legal_moves(board, opponent(side))
    count = 0
    verified = 0
    for corner, star in CORNER_STAR_PAIRS:
        flag = corner in side_moves and star in side_moves and corner not in opp_moves and star not in opp_moves
        if not flag:
            continue
        count += 1
        if verify_black_hole_sequence(board, side, corner, star) or verify_black_hole_sequence(board, side, star, corner):
            verified += 1
    return count, verified


def pressure_definition_for_move(move_idx: int) -> dict[str, set[int]] | None:
    for definition in PRESSURE_EDGE_DEFINITIONS:
        if move_idx in definition["edge_squares"] or move_idx in definition["inner_lane_squares"]:
            return definition
    return None


def move_direction_count(board: str, side: str, move_idx: int) -> int:
    if board[move_idx] != "-":
        return 0
    enemy = opponent(side)
    row, col = idx_to_rc(move_idx)
    total = 0
    for dr, dc in DIRECTIONS:
        r, c = row + dr, col + dc
        seen_enemy = False
        while in_bounds(r, c):
            cell = board[rc_to_idx(r, c)]
            if cell == enemy:
                seen_enemy = True
                r += dr
                c += dc
                continue
            if cell == side and seen_enemy:
                total += 1
            break
    return total


def flat_inner_edge_pressure_count(board: str, side: str) -> int:
    count = 0
    for move_idx in legal_moves(board, side):
        zone = pressure_definition_for_move(move_idx)
        if zone is None:
            continue
        after = apply_move(board, side, move_idx)
        opp = opponent(side)
        opp_moves = legal_moves(after, opp)
        if not opp_moves or any(idx not in EDGE_SQUARES for idx in opp_moves):
            continue
        matching_edge = [idx for idx in opp_moves if idx in zone["edge_squares"]]
        matching_inner = [idx for idx in opp_moves if idx in zone["inner_lane_squares"]]
        if matching_inner:
            continue
        if matching_edge and all(move_direction_count(after, opp, idx) >= 2 for idx in matching_edge):
            count += 1
    return count


def parse_bits(board: str, color: str) -> int:
    bits = 0
    for idx, cell in enumerate(board):
        if cell == color:
            bits |= 1 << (63 - idx)
    return bits


def row_byte(board: str | int, row: int, color: str | None = None) -> int:
    value = 0
    if isinstance(board, str):
        for col in range(8):
            if board[row * 8 + col] == color:
                value |= 1 << (7 - col)
    else:
        for col in range(8):
            idx = row * 8 + col
            if board & (1 << (63 - idx)):
                value |= 1 << (7 - col)
    return value


def col_byte(bits: int, col: int) -> int:
    value = 0
    for row in range(8):
        idx = row * 8 + col
        if bits & (1 << (63 - idx)):
            value |= 1 << (7 - row)
    return value


def place_row_byte(mask: int, row: int, byte: int) -> int:
    for col in range(8):
        if byte & (1 << (7 - col)):
            mask |= 1 << (63 - (row * 8 + col))
    return mask


def place_col_byte(mask: int, col: int, byte: int) -> int:
    for row in range(8):
        if byte & (1 << (7 - row)):
            mask |= 1 << (63 - (row * 8 + col))
    return mask


def row_flip_including_move(move: int, player: int, opponent_bits: int) -> int:
    move_bit = 1 << move
    flip = move_bit
    for step in (-1, 1):
        x = move + step
        captured = 0
        while 0 <= x < 8 and (opponent_bits & (1 << x)):
            captured |= 1 << x
            x += step
        if 0 <= x < 8 and captured and (player & (1 << x)):
            flip |= captured
    return flip


_STABLE_EDGE_CACHE: dict[tuple[int, int], int] = {}


def stable_edge_byte(player: int, opponent_bits: int) -> int:
    key = (player & 0xFF, opponent_bits & 0xFF)
    cached = _STABLE_EDGE_CACHE.get(key)
    if cached is not None:
        return cached
    empties = (~(player | opponent_bits)) & 0xFF
    stable = (~empties) & 0xFF
    if empties == 0:
        _STABLE_EDGE_CACHE[key] = stable
        return stable
    _STABLE_EDGE_CACHE[key] = stable
    for x in range(8):
        move = 1 << x
        if not (empties & move):
            continue
        flip = row_flip_including_move(x, player, opponent_bits)
        stable &= (~flip) & 0xFF & stable_edge_byte(opponent_bits & (~flip & 0xFF), player | flip)
        flip = row_flip_including_move(x, opponent_bits, player)
        stable &= (~flip) & 0xFF & stable_edge_byte(player & (~flip & 0xFF), opponent_bits | flip)
    _STABLE_EDGE_CACHE[key] = stable
    return stable


def stable_edges(player_bits: int, opponent_bits: int) -> int:
    stable = 0
    stable = place_row_byte(stable, 7, stable_edge_byte(row_byte(player_bits, 7), row_byte(opponent_bits, 7)))
    stable = place_row_byte(stable, 0, stable_edge_byte(row_byte(player_bits, 0), row_byte(opponent_bits, 0)))
    stable = place_col_byte(stable, 0, stable_edge_byte(col_byte(player_bits, 0), col_byte(opponent_bits, 0)))
    stable = place_col_byte(stable, 7, stable_edge_byte(col_byte(player_bits, 7), col_byte(opponent_bits, 7)))
    return stable


def line_full_masks(empty_bits: int) -> tuple[int, int, int, int]:
    full_rows = full_cols = full_diag_a = full_diag_b = 0
    for row in range(8):
        indexes = [row * 8 + col for col in range(8)]
        if not any(empty_bits & (1 << (63 - idx)) for idx in indexes):
            for idx in indexes:
                full_rows |= 1 << (63 - idx)
    for col in range(8):
        indexes = [row * 8 + col for row in range(8)]
        if not any(empty_bits & (1 << (63 - idx)) for idx in indexes):
            for idx in indexes:
                full_cols |= 1 << (63 - idx)
    for diff in range(-7, 8):
        indexes = [row * 8 + col for row in range(8) for col in range(8) if row - col == diff]
        if not any(empty_bits & (1 << (63 - idx)) for idx in indexes):
            for idx in indexes:
                full_diag_a |= 1 << (63 - idx)
    for total in range(15):
        indexes = [row * 8 + col for row in range(8) for col in range(8) if row + col == total]
        if not any(empty_bits & (1 << (63 - idx)) for idx in indexes):
            for idx in indexes:
                full_diag_b |= 1 << (63 - idx)
    return full_rows, full_cols, full_diag_a, full_diag_b


def neighbor_axis_mask(stable_player: int, deltas: tuple[tuple[int, int], tuple[int, int]]) -> int:
    mask = 0
    for idx in range(64):
        bit = 1 << (63 - idx)
        if not (stable_player & bit):
            continue
        row, col = idx_to_rc(idx)
        for dr, dc in deltas:
            r, c = row + dr, col + dc
            if in_bounds(r, c):
                mask |= 1 << (63 - rc_to_idx(r, c))
    return mask


def sensei_stable_bits(player_bits: int, opponent_bits: int) -> int:
    stable = stable_edges(player_bits, opponent_bits)
    empties = (~(player_bits | opponent_bits)) & FULL64
    full_rows, full_cols, full_diag_a, full_diag_b = line_full_masks(empties)
    stable |= full_rows & full_cols & full_diag_a & full_diag_b
    stable_player = stable & player_bits
    non_edge = 0
    for row in range(1, 7):
        for col in range(1, 7):
            non_edge |= 1 << (63 - (row * 8 + col))
    while True:
        horizontal = neighbor_axis_mask(stable_player, ((0, -1), (0, 1))) | full_rows
        vertical = neighbor_axis_mask(stable_player, ((-1, 0), (1, 0))) | full_cols
        diag_a = neighbor_axis_mask(stable_player, ((-1, -1), (1, 1))) | full_diag_b
        diag_b = neighbor_axis_mask(stable_player, ((-1, 1), (1, -1))) | full_diag_a
        new_stable = horizontal & vertical & diag_a & diag_b & player_bits & non_edge & ~stable_player
        if not new_stable:
            break
        stable_player |= new_stable
    return stable | stable_player


def stable_summary(board: str, side: str, active: bool) -> dict[str, int]:
    if not active:
        return {
            "stable_active": 0,
            "side_stable_count": 0,
            "opponent_stable_count": 0,
            "stable_diff": 0,
            "stable_upper_bound": 64,
        }
    side_bits = parse_bits(board, side)
    opp_bits = parse_bits(board, opponent(side))
    side_stable = sensei_stable_bits(side_bits, opp_bits) & side_bits
    opp_stable = sensei_stable_bits(opp_bits, side_bits) & opp_bits
    side_count = side_stable.bit_count()
    opp_count = opp_stable.bit_count()
    return {
        "stable_active": 1,
        "side_stable_count": side_count,
        "opponent_stable_count": opp_count,
        "stable_diff": side_count - opp_count,
        "stable_upper_bound": 64 - 2 * opp_count,
    }


def shared_region_parity(board: str, side: str, own_regions: list[dict[str, Any]], opp_regions: list[dict[str, Any]], active: bool) -> dict[str, int]:
    if not active:
        return {
            "ply40_shared_region_count": 0,
            "ply40_largest_shared_region_size": 0,
            "ply40_shared_region_side_has_parity": 0,
        }
    excluded = {idx for region in own_regions + opp_regions for idx in region["cells"]}
    own_moves = legal_moves(board, side)
    opp_moves = legal_moves(board, opponent(side))
    visited: set[int] = set()
    shared_sizes: list[int] = []
    for start, cell in enumerate(board):
        if cell != "-" or start in excluded or start in visited:
            continue
        stack = [start]
        visited.add(start)
        cells: list[int] = []
        while stack:
            idx = stack.pop()
            cells.append(idx)
            row, col = idx_to_rc(idx)
            for dr, dc in ORTHOGONAL_DIRECTIONS:
                r, c = row + dr, col + dc
                if not in_bounds(r, c):
                    continue
                n = rc_to_idx(r, c)
                if board[n] == "-" and n not in excluded and n not in visited:
                    visited.add(n)
                    stack.append(n)
        cell_set = set(cells)
        if own_moves & cell_set and opp_moves & cell_set:
            shared_sizes.append(len(cells))
    largest = max(shared_sizes, default=0)
    return {
        "ply40_shared_region_count": len(shared_sizes),
        "ply40_largest_shared_region_size": largest,
        "ply40_shared_region_side_has_parity": int(largest % 2 == 1 and largest > 0),
    }


def compute_one(item: tuple[str, str, int, str, str, str]) -> dict[str, Any]:
    board_raw, side_raw, ply_raw, hint6_1_move, hint6_2_move, hint6_3_move = item
    board = normalize_board(board_raw)
    side = normalize_side(side_raw)
    ply = int(ply_raw) if not pd.isna(ply_raw) else 0
    opp = opponent(side)
    regions = empty_regions(board, side)
    own_independent = [r for r in regions if r["strict_independent_odd_empty_flag"]]
    opponent_independent = independent_regions(board, opp)
    odd_count = sum(1 for r in regions if r["parity"] == "odd")
    even_count = sum(1 for r in regions if r["parity"] == "even")
    largest = max((r["size"] for r in regions), default=0)
    smallest = min((r["size"] for r in regions), default=0)
    side_black_hole_count, side_verified_black_hole_count = black_hole_counts(board, side)
    opponent_black_hole_count, opponent_verified_black_hole_count = black_hole_counts(board, opp)
    pressure_count = flat_inner_edge_pressure_count(board, side)
    ply40_active = ply >= 40
    side_disc_count = board.count(side)
    opponent_disc_count = board.count(opp)

    out: dict[str, Any] = {
        "board": board,
        "side_to_move": side_raw,
        "normalized_side_to_move": side,
        "ply": ply,
        "hint6_1_move": hint6_1_move,
        "hint6_2_move": hint6_2_move,
        "hint6_3_move": hint6_3_move,
        "side_disc_count": side_disc_count,
        "opponent_disc_count": opponent_disc_count,
        "side_minus_opponent_disc_count": side_disc_count - opponent_disc_count,
        "empty_region_count": len(regions),
        "odd_empty_region_count": odd_count,
        "even_empty_region_count": even_count,
        "largest_empty_region_size": largest,
        "smallest_empty_region_size": smallest,
        "own_independent_odd_empty": int(bool(own_independent)),
        "opponent_independent_odd_empty": int(bool(opponent_independent)),
        "own_independent_odd_empty_count": len(own_independent),
        "opponent_independent_odd_empty_count": len(opponent_independent),
        "own_independent_odd_empty_total_size": sum(r["size"] for r in own_independent),
        "opponent_independent_odd_empty_total_size": sum(r["size"] for r in opponent_independent),
        "own_black_hole_count": side_black_hole_count,
        "opponent_black_hole_count": opponent_black_hole_count,
        "own_verified_black_hole_count": side_verified_black_hole_count,
        "opponent_verified_black_hole_count": opponent_verified_black_hole_count,
        "flat_inner_edge_pressure_move_count": pressure_count,
        "has_flat_inner_edge_pressure_move": int(pressure_count > 0),
    }
    out.update(dispersion_summary(board, side))
    out.update(compact_move_dispersion_summary(board, side))
    out.update(hint6_dispersion_summary(board, side, hint6_1_move, hint6_2_move, hint6_3_move))
    out.update(shared_region_parity(board, side, own_independent, opponent_independent, ply40_active))
    out.update(stable_summary(board, side, ply40_active))
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--chunksize", type=int, default=256)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    t0 = time.time()
    write_progress({"status": "loading", "data_path": str(args.data), "out_path": str(args.out)})
    cols = ["game_id", "ply", "move_index", "board", "side_to_move", "hint6_1_move", "hint6_2_move", "hint6_3_move"]
    df = pd.read_csv(args.data, usecols=cols, low_memory=False)
    if args.limit:
        df = df.head(args.limit).copy()
    df["board"] = df["board"].map(normalize_board)
    df["normalized_side_to_move"] = df["side_to_move"].map(normalize_side)
    unique = df[
        ["board", "normalized_side_to_move", "ply", "hint6_1_move", "hint6_2_move", "hint6_3_move"]
    ].drop_duplicates().reset_index(drop=True)
    items = list(unique.itertuples(index=False, name=None))
    write_progress(
        {
            "status": "computing",
            "rows": int(len(df)),
            "unique_positions": int(len(unique)),
            "workers": int(args.workers),
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )

    rows: list[dict[str, Any]] = []
    if args.workers and args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            for i, row in enumerate(executor.map(compute_one, items, chunksize=args.chunksize), start=1):
                rows.append(row)
                if i % 5000 == 0:
                    write_progress(
                        {
                            "status": "computing",
                            "rows": int(len(df)),
                            "unique_positions": int(len(unique)),
                            "computed_unique_positions": i,
                            "percent": round(i / max(len(unique), 1) * 100, 2),
                            "elapsed_seconds": round(time.time() - t0, 2),
                        }
                    )
    else:
        for i, item in enumerate(items, start=1):
            rows.append(compute_one(item))
            if i % 5000 == 0:
                write_progress(
                    {
                        "status": "computing",
                        "rows": int(len(df)),
                        "unique_positions": int(len(unique)),
                        "computed_unique_positions": i,
                        "percent": round(i / max(len(unique), 1) * 100, 2),
                        "elapsed_seconds": round(time.time() - t0, 2),
                    }
                )

    meta_unique = pd.DataFrame(rows)
    merged = df.merge(
        meta_unique,
        on=["board", "normalized_side_to_move", "ply", "hint6_1_move", "hint6_2_move", "hint6_3_move"],
        how="left",
        suffixes=("", "_meta"),
    )
    drop_cols = ["normalized_side_to_move_meta", "side_to_move_meta"]
    merged = merged.drop(columns=[c for c in drop_cols if c in merged.columns], errors="ignore")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = args.out.with_suffix(args.out.suffix + ".tmp")
    merged.to_csv(tmp_out, index=False)
    tmp_out.replace(args.out)
    write_progress(
        {
            "status": "complete",
            "rows": int(len(merged)),
            "unique_positions": int(len(unique)),
            "out_path": str(args.out),
            "elapsed_seconds": round(time.time() - t0, 2),
        }
    )
    print(f"Wrote {args.out} rows={len(merged)} unique_positions={len(unique)}")


if __name__ == "__main__":
    main()
