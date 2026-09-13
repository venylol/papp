from __future__ import annotations

import csv
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable


LOSS_THRESHOLDS = (4, 10)
LOSS_PROBABILITY_FIELDS = {
    4: "probability_loss_ge4",
    10: "probability_loss_ge10",
}
SUPPORTED_WLD_FROM_PLY = 39
ENGINE_WLD_TOTAL_FIELD = "engine_wld_loss_total_from_ply39"
PREDICTED_WLD_TOTAL_FIELD = "predicted_expected_wld_loss_total_from_ply39"

MOVE_RE = re.compile(r"^[a-h][1-8]$", re.IGNORECASE)
MOVE_SEPARATOR_RE = re.compile(r"[\s,;:/_-]+")


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        target.write_text("", encoding="utf-8", newline="")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def account_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def quantile(values: Iterable[float], q: float) -> float | None:
    items = sorted(float(value) for value in values)
    if not items:
        return None
    position = (len(items) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return items[lower]
    fraction = position - lower
    return items[lower] * (1.0 - fraction) + items[upper] * fraction


def rounded(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def validate_wld_from_ply(wld_from_ply: int | None) -> int | None:
    if wld_from_ply is None:
        return None
    if isinstance(wld_from_ply, bool) or int(wld_from_ply) != SUPPORTED_WLD_FROM_PLY:
        raise ValueError(
            f"wld_from_ply must be {SUPPORTED_WLD_FROM_PLY} because the public total field is fixed to ply 39"
        )
    return SUPPORTED_WLD_FROM_PLY


def wld_rank(score: float | int) -> int:
    value = float(score)
    if not math.isfinite(value):
        raise ValueError("WLD score must be finite")
    if value > 0:
        return 2
    if value < 0:
        return 0
    return 1


def actual_move_score_from_next_best(
    next_best_score: float | int,
    same_side_after_pass: bool,
) -> float:
    value = float(next_best_score)
    if not math.isfinite(value):
        raise ValueError("next_best_score must be finite")
    return value if same_side_after_pass else -value


def wld_loss_from_scores(before_score: float | int, actual_move_score: float | int) -> float:
    drop = max(0, wld_rank(before_score) - wld_rank(actual_move_score))
    return drop / 2.0


def engine_node_wld_loss(node: dict[str, Any]) -> float:
    before_score = node.get("bestEval", node.get("hint6_1_score"))
    if before_score is None:
        raise ValueError("WLD-enabled engine node is missing bestEval/current best score")
    actual_score = node.get("actualEval")
    if actual_score is None:
        next_best_score = node.get("next_best_score")
        if next_best_score is None or "same_side_after_move" not in node:
            raise ValueError(
                "WLD-enabled engine node must provide actualEval or next_best_score plus same_side_after_move"
            )
        actual_score = actual_move_score_from_next_best(
            next_best_score,
            bool(node["same_side_after_move"]),
        )
    return wld_loss_from_scores(before_score, actual_score)


def node_global_placement_ply(node: dict[str, Any]) -> int:
    raw = node.get("global_placement_ply", node.get("ply"))
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not float(raw).is_integer():
        raise ValueError("WLD-enabled node is missing an integer pass-free global placement ply")
    ply = int(raw)
    if not 1 <= ply <= 60:
        raise ValueError(f"global placement ply must be in [1, 60], got {ply}")
    return ply


def engine_wld_loss_total(games: list[dict[str, Any]], wld_from_ply: int) -> float:
    boundary = validate_wld_from_ply(wld_from_ply)
    assert boundary is not None
    total = 0.0
    for game in games:
        for node in game.get("nodes", []):
            if node_global_placement_ply(node) >= boundary:
                total += engine_node_wld_loss(node)
    return rounded(total) or 0.0


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
        for dr, dc in self.directions:
            rr, cc = row + dr, col + dc
            line: list[tuple[int, int]] = []
            while 0 <= rr < 8 and 0 <= cc < 8 and self.board[rr][cc] == other:
                line.append((rr, cc))
                rr += dr
                cc += dc
            if line and 0 <= rr < 8 and 0 <= cc < 8 and self.board[rr][cc] == color:
                captured.extend(line)
        return captured

    def legal_moves(self, color: str | None = None) -> list[tuple[int, int]]:
        side = color or self.current
        return [
            (row, col)
            for row in range(8)
            for col in range(8)
            if self.captures(row, col, side)
        ]

    def normalize_turn(self) -> None:
        if self.legal_moves(self.current):
            return
        other = self.opponent(self.current)
        if self.legal_moves(other):
            self.current = other

    def apply_move(self, move: str) -> str:
        text = str(move).strip().lower()
        if not MOVE_RE.fullmatch(text):
            raise ValueError(f"invalid move coordinate: {move!r}")
        row = int(text[1]) - 1
        col = ord(text[0]) - ord("a")
        flips = self.captures(row, col, self.current)
        if not flips:
            raise ValueError(f"illegal move {text} for side {self.current}")
        played = self.current
        self.board[row][col] = played
        for rr, cc in flips:
            self.board[rr][cc] = played
        self.current = self.opponent(played)
        self.normalize_turn()
        return played

    @staticmethod
    def _transform(board64: str, mode: int) -> str:
        result = ["-"] * 64
        for row in range(8):
            for col in range(8):
                if mode == 0:
                    dst_row, dst_col = row, col
                elif mode == 1:
                    dst_row, dst_col = 7 - row, col
                elif mode == 2:
                    dst_row, dst_col = row, 7 - col
                elif mode == 3:
                    dst_row, dst_col = 7 - row, 7 - col
                elif mode == 4:
                    dst_row, dst_col = col, row
                elif mode == 5:
                    dst_row, dst_col = 7 - col, 7 - row
                elif mode == 6:
                    dst_row, dst_col = col, 7 - row
                else:
                    dst_row, dst_col = 7 - col, row
                result[dst_row * 8 + dst_col] = board64[row * 8 + col]
        return "".join(result)

    def canonical_current_view(self) -> str:
        chars: list[str] = []
        for row in self.board:
            for value in row:
                if value == "-":
                    chars.append("-")
                elif value == self.current:
                    chars.append("X")
                else:
                    chars.append("O")
        board64 = "".join(chars)
        return min(self._transform(board64, mode) for mode in range(8))


def parse_move_sequence(value: Any) -> list[str]:
    text = MOVE_SEPARATOR_RE.sub("", str(value or "").strip().lower())
    if not text or len(text) % 2:
        raise ValueError(f"opening sequence must contain complete coordinates: {value!r}")
    moves = [text[index:index + 2] for index in range(0, len(text), 2)]
    invalid = [move for move in moves if not MOVE_RE.fullmatch(move)]
    if invalid:
        raise ValueError(f"opening sequence contains invalid coordinates: {invalid}")
    return moves


def transform_move(move: str, mode: int) -> str:
    row = int(move[1]) - 1
    col = ord(move[0]) - ord("a")
    if mode == 0:
        dst_row, dst_col = row, col
    elif mode == 1:
        dst_row, dst_col = 7 - row, col
    elif mode == 2:
        dst_row, dst_col = row, 7 - col
    elif mode == 3:
        dst_row, dst_col = 7 - row, 7 - col
    elif mode == 4:
        dst_row, dst_col = col, row
    elif mode == 5:
        dst_row, dst_col = 7 - col, 7 - row
    elif mode == 6:
        dst_row, dst_col = col, 7 - row
    elif mode == 7:
        dst_row, dst_col = 7 - col, row
    else:
        raise ValueError(f"unknown symmetry mode: {mode}")
    return f"{chr(ord('a') + dst_col)}{dst_row + 1}"


def canonical_move_sequence(moves: Iterable[str]) -> str:
    items = [str(move).strip().lower() for move in moves]
    if not items or any(not MOVE_RE.fullmatch(move) for move in items):
        raise ValueError("move sequence must contain at least one valid coordinate")
    return min("".join(transform_move(move, mode) for move in items) for mode in range(8))


def opening_catalog_entries(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entries = catalog.get("openings")
    if not isinstance(raw_entries, list):
        raise ValueError("opening catalog must contain an openings array")
    entries: list[dict[str, Any]] = []
    lookup_owner: dict[str, str] = {}
    sequence_owner: dict[str, str] = {}
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("every opening catalog entry must be an object")
        opening_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not opening_id or not name:
            raise ValueError("every opening catalog entry requires id and name")
        moves = parse_move_sequence(raw.get("sequence"))
        board = OthelloBoard()
        try:
            for move in moves:
                board.apply_move(move)
        except ValueError as exc:
            raise ValueError(f"opening {opening_id!r} has an illegal sequence: {exc}") from exc
        raw_aliases = raw.get("aliases", [])
        if not isinstance(raw_aliases, list):
            raise ValueError(f"opening {opening_id!r} aliases must be an array")
        aliases = [str(alias).strip() for alias in raw_aliases if str(alias).strip()]
        canonical = canonical_move_sequence(moves)
        if canonical in sequence_owner:
            raise ValueError(
                f"openings {sequence_owner[canonical]!r} and {opening_id!r} have symmetry-equivalent sequences"
            )
        sequence_owner[canonical] = opening_id
        for label in (opening_id, name, *aliases):
            key = label.casefold()
            owner = lookup_owner.get(key)
            if owner is not None and owner != opening_id:
                raise ValueError(f"opening lookup label {label!r} is shared by {owner!r} and {opening_id!r}")
            lookup_owner[key] = opening_id
        entries.append({
            "id": opening_id,
            "name": name,
            "sequence": "".join(moves),
            "aliases": aliases,
            "ply": len(moves),
            "canonicalSequence": canonical,
        })
    return sorted(entries, key=lambda item: (item["ply"], item["id"]))


def resolve_opening(entries: list[dict[str, Any]], reference: str) -> dict[str, Any]:
    key = str(reference or "").strip().casefold()
    for entry in entries:
        labels = [entry["id"], entry["name"], *entry["aliases"]]
        if key in {str(label).casefold() for label in labels}:
            return {**entry, "catalogued": True}
    try:
        moves = parse_move_sequence(reference)
    except ValueError as exc:
        raise ValueError(f"opening {reference!r} is not present in the catalog") from exc
    canonical = canonical_move_sequence(moves)
    for entry in entries:
        if entry["canonicalSequence"] == canonical:
            return {**entry, "catalogued": True}
    board = OthelloBoard()
    try:
        for move in moves:
            board.apply_move(move)
    except ValueError as exc:
        raise ValueError(f"opening query has an illegal sequence: {exc}") from exc
    return {
        "id": None,
        "name": None,
        "sequence": "".join(moves),
        "aliases": [],
        "ply": len(moves),
        "canonicalSequence": canonical,
        "catalogued": False,
    }


def standard_opening_query(
    bundle: dict[str, Any],
    catalog: dict[str, Any],
    account: str,
    requested_game_ids: set[str] | None = None,
    opening_reference: str | None = None,
) -> dict[str, Any]:
    entries = opening_catalog_entries(catalog)
    requested = resolve_opening(entries, opening_reference) if opening_reference else None
    details = bundle_games(bundle)
    all_ids = {str(detail.get("id") or "") for detail in details}
    target_details = [detail for detail in details if target_side(detail, account) is not None]
    target_ids = {str(detail.get("id") or "") for detail in target_details}
    selected_ids = set(requested_game_ids or [])
    if selected_ids:
        missing = sorted(selected_ids - all_ids)
        if missing:
            raise ValueError(f"game IDs not found in bundle: {missing}")
        wrong_account = sorted(selected_ids - target_ids)
        if wrong_account:
            raise ValueError(f"game IDs do not contain target account {account!r}: {wrong_account}")
        target_details = [detail for detail in target_details if str(detail.get("id") or "") in selected_ids]

    games: list[dict[str, Any]] = []
    for detail in sorted(target_details, key=lambda item: (str(item.get("created") or ""), str(item.get("id") or ""))):
        players = game_players(detail)
        side = target_side(detail, account)
        moves = [
            str(item.get("m")).strip().lower()
            for item in source_events(detail)
            if isinstance(item.get("m"), str) and MOVE_RE.fullmatch(str(item.get("m")).strip())
        ]
        matches = [
            entry for entry in entries
            if len(moves) >= entry["ply"]
            and canonical_move_sequence(moves[:entry["ply"]]) == entry["canonicalSequence"]
        ]
        deepest = matches[-1] if matches else None
        requested_match = (
            requested is None
            or (
                len(moves) >= requested["ply"]
                and canonical_move_sequence(moves[:requested["ply"]]) == requested["canonicalSequence"]
            )
        )
        if not requested_match:
            continue
        games.append({
            "gameId": str(detail.get("id") or ""),
            "created": detail.get("created"),
            "targetColor": side,
            "opponentAccount": players[1 if side == "black" else 0].get("id"),
            "moveCount": len(moves),
            "moveSequence": "".join(moves),
            "canonicalMoveSequence": canonical_move_sequence(moves) if moves else "",
            "opening": ({key: deepest[key] for key in ("id", "name", "sequence", "ply")} if deepest else None),
            "openingMatches": [
                {key: entry[key] for key in ("id", "name", "sequence", "ply")}
                for entry in matches
            ],
        })

    public_requested = None
    if requested is not None:
        public_requested = {
            key: requested[key]
            for key in ("id", "name", "sequence", "ply", "canonicalSequence", "catalogued")
        }
    catalogued_count = sum(1 for game in games if game["opening"] is not None)
    return {
        "schema": "player-standard-opening-query-v1",
        "account": account,
        "catalogSchema": catalog.get("schema"),
        "catalogEntryCount": len(entries),
        "requestedGameIds": sorted(selected_ids),
        "requestedOpening": public_requested,
        "scannedGameCount": len(target_details),
        "returnedGameCount": len(games),
        "cataloguedReturnedGameCount": catalogued_count,
        "symmetryPolicy": "coordinate sequences are compared after canonicalization over 8 rotations/reflections",
        "matchPolicy": "an opening matches when its normalized sequence is a prefix of the game; opening is the longest catalogued match",
        "games": games,
    }


def bundle_games(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    details = bundle.get("details")
    if not isinstance(details, list):
        raise ValueError("bundle must contain a details array")
    return [item for item in details if isinstance(item, dict)]


def game_players(detail: dict[str, Any]) -> list[dict[str, Any]]:
    players = detail.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError(f"game {detail.get('id')} must contain exactly two players")
    return players


def target_side(detail: dict[str, Any], account: str) -> str | None:
    key = account_key(account)
    players = game_players(detail)
    matches = [index for index, player in enumerate(players) if account_key(player.get("id")) == key]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"game {detail.get('id')} contains target account more than once")
    return "black" if matches[0] == 0 else "white"


def source_events(detail: dict[str, Any]) -> list[dict[str, Any]]:
    position = detail.get("position")
    moves = position.get("moves") if isinstance(position, dict) else None
    if not isinstance(moves, list):
        raise ValueError(f"game {detail.get('id')} has no position.moves array")
    return [item if isinstance(item, dict) else {} for item in moves]


def replay_game(detail: dict[str, Any], max_ply: int | None = None) -> list[dict[str, Any]]:
    players = game_players(detail)
    board = OthelloBoard()
    placed_ply = 0
    records: list[dict[str, Any]] = []
    for source_index, item in enumerate(source_events(detail)):
        raw_move = item.get("m")
        move = str(raw_move or "").strip().lower()
        if not MOVE_RE.fullmatch(move):
            continue
        if max_ply is not None and placed_ply >= max_ply:
            break
        expected_color = "X" if source_index % 2 == 0 else "O"
        if board.current != expected_color:
            raise ValueError(
                f"game {detail.get('id')} turn mismatch at source index {source_index}: "
                f"source={expected_color}, board={board.current}"
            )
        parent_key = board.canonical_current_view()
        played = board.apply_move(move)
        placed_ply += 1
        player = players[0] if played == "X" else players[1]
        records.append({
            "gameId": str(detail.get("id") or ""),
            "sourceMoveIndex": source_index,
            "ply": placed_ply,
            "move": move,
            "playerColor": "black" if played == "X" else "white",
            "playerAccount": str(player.get("id") or player.get("name") or ""),
            "thinkingTimeMs": item.get("t"),
            "parentKey": parent_key,
            "childKey": board.canonical_current_view(),
        })
    return records


def build_personal_book(
    details: list[dict[str, Any]],
    account: str,
    color: str,
    excluded_game_ids: set[str],
    max_ply: int,
) -> dict[str, Any]:
    counts: Counter[tuple[int, str]] = Counter()
    source_ids: list[str] = []
    for detail in details:
        game_id = str(detail.get("id") or "")
        if game_id in excluded_game_ids or target_side(detail, account) != color:
            continue
        source_ids.append(game_id)
        board = OthelloBoard()
        counts[(0, board.canonical_current_view())] += 1
        for record in replay_game(detail, max_ply=max_ply):
            counts[(int(record["ply"]), str(record["childKey"]))] += 1
    nodes = [
        [board_key, count, ply]
        for (ply, board_key), count in sorted(counts.items(), key=lambda item: (item[0][0], -item[1], item[0][1]))
    ]
    return {
        "schema": "player-human-frequency-book-v1",
        "account": account,
        "color": color,
        "maxPlyInclusive": max_ply,
        "sourceGameCount": len(source_ids),
        "sourceGameIds": source_ids,
        "boardStringFormat": "64 chars, current-player view, X=current side, O=opponent, -=empty, canonicalized over 8 symmetries",
        "nodes": nodes,
    }


def book_lookup(book: dict[str, Any]) -> dict[tuple[int, str], int]:
    lookup: dict[tuple[int, str], int] = {}
    for node in book.get("nodes", []):
        if isinstance(node, list) and len(node) >= 3:
            lookup[(int(node[2]), str(node[0]))] = int(node[1])
    return lookup


def evaluate_opening_game(
    detail: dict[str, Any],
    account: str,
    book: dict[str, Any],
    max_ply: int,
) -> dict[str, Any]:
    lookup = book_lookup(book)
    target = account_key(account)
    decisions: list[dict[str, Any]] = []
    for record in replay_game(detail, max_ply=max_ply):
        if account_key(record["playerAccount"]) != target:
            continue
        ply = int(record["ply"])
        parent_count = lookup.get((ply - 1, str(record["parentKey"])), 0)
        child_count = lookup.get((ply, str(record["childKey"])), 0)
        if parent_count <= 0:
            classification = "unseen_parent"
        elif child_count > 0:
            classification = "historical_choice"
        else:
            classification = "new_player_choice"
        decisions.append({
            "decisionNumber": len(decisions) + 1,
            "ply": ply,
            "move": record["move"],
            "parentBookCount": parent_count,
            "bookCount": child_count,
            "bookHit": 1 if child_count > 0 else 0,
            "parentChildRatio": rounded(child_count / parent_count) if parent_count > 0 else None,
            "classification": classification,
        })
    known = [item for item in decisions if item["parentBookCount"] > 0]
    hits = [item for item in known if item["bookHit"] == 1]
    continuous = 0
    for item in decisions:
        if item["classification"] != "historical_choice":
            break
        continuous += 1
    first_exit = decisions[continuous] if continuous < len(decisions) else None
    if first_exit is None:
        exit_cause = None
    elif first_exit["classification"] == "new_player_choice":
        exit_cause = "player_new_choice"
    else:
        exit_cause = "opponent_or_prior_branch_unseen"
    return {
        "gameId": str(detail.get("id") or ""),
        "color": target_side(detail, account),
        "decisionCount": len(decisions),
        "knownParentCount": len(known),
        "historicalChoiceCount": len(hits),
        "historyCoverageRate": rounded(len(known) / len(decisions)) if decisions else None,
        "historicalChoiceRate": rounded(len(hits) / len(known)) if known else None,
        "continuousHistoricalChoiceCount": continuous,
        "firstExitDecisionNumber": first_exit["decisionNumber"] if first_exit else None,
        "firstExitCause": exit_cause,
        "decisions": decisions,
    }


def summarize_opening(
    bundle: dict[str, Any],
    account: str,
    reported_game_ids: set[str],
    max_ply: int,
) -> dict[str, Any]:
    details = bundle_games(bundle)
    by_id = {str(detail.get("id") or ""): detail for detail in details}
    missing = sorted(reported_game_ids - set(by_id))
    if missing:
        raise ValueError(f"reported game IDs not found in bundle: {missing}")
    reported_results: list[dict[str, Any]] = []
    books: dict[str, dict[str, Any]] = {}
    for color in ("black", "white"):
        books[color] = build_personal_book(details, account, color, reported_game_ids, max_ply)
    for game_id in sorted(reported_game_ids):
        detail = by_id[game_id]
        color = target_side(detail, account)
        if color is None:
            raise ValueError(f"reported game {game_id} does not contain {account}")
        reported_results.append(evaluate_opening_game(detail, account, books[color], max_ply))

    controls: list[dict[str, Any]] = []
    for detail in details:
        game_id = str(detail.get("id") or "")
        color = target_side(detail, account)
        if game_id in reported_game_ids or color is None:
            continue
        leave_one_out_book = build_personal_book(
            details, account, color, reported_game_ids | {game_id}, max_ply
        )
        controls.append(evaluate_opening_game(detail, account, leave_one_out_book, max_ply))

    def distribution(field: str, color: str) -> dict[str, Any]:
        values = [float(item[field]) for item in controls if item["color"] == color and item[field] is not None]
        return {
            "count": len(values),
            "minimum": rounded(min(values)) if values else None,
            "median": rounded(statistics.median(values)) if values else None,
            "maximum": rounded(max(values)) if values else None,
        }

    return {
        "schema": "player-opening-comparison-v1",
        "account": account,
        "maxPlyInclusive": max_ply,
        "reportedGameIds": sorted(reported_game_ids),
        "bookPolicy": "reported games excluded; black and white books separate",
        "controlPolicy": "same-color leave-one-out",
        "reportedGames": reported_results,
        "controlGames": controls,
        "controlDistributions": {
            color: {
                "historyCoverageRate": distribution("historyCoverageRate", color),
                "historicalChoiceRate": distribution("historicalChoiceRate", color),
                "continuousHistoricalChoiceCount": distribution("continuousHistoricalChoiceCount", color),
            }
            for color in ("black", "white")
        },
        "books": books,
    }


def load_engine_games(directory: str | Path) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    for path in sorted(Path(directory).glob("game_*.json")):
        value = read_json(path)
        if isinstance(value, dict) and isinstance(value.get("nodes"), list):
            games.append(value)
    if not games:
        raise ValueError(f"no game_*.json analyses found in {directory}")
    return games


def target_engine_games(games: list[dict[str, Any]], account: str) -> list[dict[str, Any]]:
    target = account_key(account)
    output: list[dict[str, Any]] = []
    for game in games:
        nodes = [
            node for node in game.get("nodes", [])
            if account_key(node.get("playerAccount")) == target
        ]
        if not nodes:
            continue
        output.append({
            "gameId": str(game.get("gameId") or ""),
            "round": game.get("round"),
            "color": str(nodes[0].get("playerColor") or ""),
            "isTournamentGame": bool(game.get("isTournamentGame")),
            "nodes": nodes,
            "source": game,
        })
    return output


def engine_wld_totals_by_game_player(
    games: list[dict[str, Any]], wld_from_ply: int
) -> dict[str, Any]:
    boundary = validate_wld_from_ply(wld_from_ply)
    assert boundary is not None
    game_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    player_totals: dict[tuple[str, str], float] = defaultdict(float)
    for game in games:
        game_id = str(game.get("gameId") or "").strip()
        if not game_id:
            raise ValueError("WLD-enabled engine analysis contains an empty gameId")
        for node in game.get("nodes", []):
            player_id = str(node.get("playerAccount") or "").strip()
            side = str(node.get("playerColor") or "").strip()
            if not player_id and not side:
                raise ValueError(
                    f"WLD-enabled engine node in game {game_id!r} has neither playerAccount nor playerColor"
                )
            game_key = (game_id, player_id, side)
            player_key = (player_id, side if not player_id else "")
            game_totals[game_key] += 0.0
            player_totals[player_key] += 0.0
            if node_global_placement_ply(node) < boundary:
                continue
            value = engine_node_wld_loss(node)
            game_totals[game_key] += value
            player_totals[player_key] += value
    game_rows = [
        {
            "game_id": game_id,
            "player_id": player_id or None,
            "side": side or None,
            ENGINE_WLD_TOTAL_FIELD: rounded(total) or 0.0,
        }
        for (game_id, player_id, side), total in sorted(game_totals.items())
    ]
    player_rows = [
        {
            "player_id": player_id or None,
            "side": side or None,
            ENGINE_WLD_TOTAL_FIELD: rounded(total) or 0.0,
        }
        for (player_id, side), total in sorted(player_totals.items())
    ]
    return {
        "schema": "player-engine-wld-loss-totals-v1",
        "wldFromPly": boundary,
        "globalPlacementPlyPolicy": "pass-free actual placement ply; boundary is inclusive",
        "gamePlayerTotals": game_rows,
        "playerTotals": player_rows,
    }


def read_prediction_rows(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if source.suffix.casefold() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = [dict(row) for row in reader]
            fieldnames = set(reader.fieldnames or [])
    elif source.suffix.casefold() == ".json":
        value = read_json(source)
        raw_rows = value.get("predictions") if isinstance(value, dict) else value
        if not isinstance(raw_rows, list) or any(not isinstance(row, dict) for row in raw_rows):
            raise ValueError("prediction JSON must be an array or an object with a predictions array")
        rows = [dict(row) for row in raw_rows]
        fieldnames = {key for row in rows for key in row}
    else:
        raise ValueError("model prediction input must be UTF-8 CSV or JSON")
    required = {"expected_wld_loss", "wld_applicable", "global_placement_ply", "game_id"}
    missing = sorted(required - fieldnames)
    if missing:
        raise ValueError(f"WLD-enabled model prediction input is missing required fields: {missing}")
    if not ({"player_id", "side"} & fieldnames):
        raise ValueError(
            "WLD-enabled model prediction input requires player_id or side to identify the mover"
        )
    return rows


def parse_prediction_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"{field} must be true/false or 1/0")


def predicted_wld_totals_by_game_player(
    rows: list[dict[str, Any]], wld_from_ply: int
) -> dict[str, Any]:
    boundary = validate_wld_from_ply(wld_from_ply)
    assert boundary is not None
    required = {"expected_wld_loss", "wld_applicable", "global_placement_ply", "game_id"}
    game_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    player_totals: dict[tuple[str, str], float] = defaultdict(float)
    for index, row in enumerate(rows, start=1):
        missing = sorted(field for field in required if field not in row)
        if missing:
            raise ValueError(f"model prediction row {index} is missing required fields: {missing}")
        game_id = str(row.get("game_id") or "").strip()
        player_id = str(row.get("player_id") or "").strip()
        side = str(row.get("side") or "").strip()
        if not game_id:
            raise ValueError(f"model prediction row {index} has an empty game_id")
        if not player_id and not side:
            raise ValueError(f"model prediction row {index} has neither player_id nor side")
        try:
            ply_value = float(row["global_placement_ply"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"model prediction row {index} global_placement_ply must be an integer"
            ) from exc
        if not math.isfinite(ply_value) or not ply_value.is_integer() or not 1 <= ply_value <= 60:
            raise ValueError(
                f"model prediction row {index} global_placement_ply must be an integer in [1, 60]"
            )
        applicable = parse_prediction_bool(row["wld_applicable"], "wld_applicable")
        game_key = (game_id, player_id, side)
        player_key = (player_id, side if not player_id else "")
        game_totals[game_key] += 0.0
        player_totals[player_key] += 0.0
        if int(ply_value) < boundary or not applicable:
            continue
        try:
            expected = float(row["expected_wld_loss"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"model prediction row {index} expected_wld_loss must be numeric when wld_applicable is true"
            ) from exc
        if not math.isfinite(expected) or not 0.0 <= expected <= 1.0:
            raise ValueError(
                f"model prediction row {index} expected_wld_loss must be finite and in [0, 1]"
            )
        game_totals[game_key] += expected
        player_totals[player_key] += expected
    game_rows = [
        {
            "game_id": game_id,
            "player_id": player_id or None,
            "side": side or None,
            PREDICTED_WLD_TOTAL_FIELD: rounded(total) or 0.0,
        }
        for (game_id, player_id, side), total in sorted(game_totals.items())
    ]
    player_rows = [
        {
            "player_id": player_id or None,
            "side": side or None,
            PREDICTED_WLD_TOTAL_FIELD: rounded(total) or 0.0,
        }
        for (player_id, side), total in sorted(player_totals.items())
    ]
    return {
        "schema": "player-model-expected-wld-loss-totals-v1",
        "wldFromPly": boundary,
        "globalPlacementPlyPolicy": "pass-free actual placement ply; boundary is inclusive",
        "gamePlayerTotals": game_rows,
        "playerTotals": player_rows,
    }


def disc_loss(raw_loss: float | int) -> float:
    """Return the shared non-negative disc-loss definition."""
    return max(0.0, float(raw_loss))


def loss_threshold_flags(raw_loss: float | int) -> dict[str, bool]:
    value = disc_loss(raw_loss)
    return {
        "loss_ge4": value >= 4,
        "loss_ge10": value >= 10,
    }


def node_losses(game: dict[str, Any]) -> list[float]:
    return [
        disc_loss(node["lossClipped"])
        for node in game["nodes"]
        if node.get("lossClipped") is not None
    ]


def threshold_count(losses: list[float], threshold: int) -> int:
    return sum(value >= threshold for value in losses)


def threshold_rate(losses: list[float], threshold: int) -> float | None:
    return threshold_count(losses, threshold) / len(losses) if losses else None


def model_probability_summary(
    games: list[dict[str, Any]], threshold: int
) -> dict[str, Any]:
    field = LOSS_PROBABILITY_FIELDS[threshold]
    eligible_nodes = [
        node
        for game in games
        for node in game["nodes"]
        if node.get("lossClipped") is not None
    ]
    probabilities: list[float] = []
    for node in eligible_nodes:
        raw_probability = node.get(field)
        if raw_probability is None:
            continue
        probability = float(raw_probability)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError(f"{field} must be a finite probability in [0, 1]")
        probabilities.append(probability)

    losses = [disc_loss(node["lossClipped"]) for node in eligible_nodes]
    actual_count = threshold_count(losses, threshold)
    missing_count = len(eligible_nodes) - len(probabilities)
    available = bool(eligible_nodes) and missing_count == 0
    expected_count = sum(probabilities) if available else None
    residual_count = actual_count - expected_count if expected_count is not None else None
    return {
        "inputField": field,
        "status": "available" if available else "unavailable",
        "modelProbabilityAvailable": available,
        "eligibleNodeCount": len(eligible_nodes),
        "probabilityNodeCount": len(probabilities),
        "missingProbabilityNodeCount": missing_count,
        "expectedNodeCount": rounded(expected_count),
        "actualNodeCount": actual_count,
        "actualMinusExpectedNodeCount": rounded(residual_count),
        "actualRateMinusMeanProbability": rounded(
            residual_count / len(eligible_nodes)
            if residual_count is not None and eligible_nodes else None
        ),
        "actualMinusExpectedPerGame": rounded(
            residual_count / len(games)
            if residual_count is not None and games else None
        ),
        "unavailableReason": None if available else "模型概率不可用",
    }


def loss_game_summary(
    game: dict[str, Any], wld_from_ply: int | None = None
) -> dict[str, Any]:
    losses = node_losses(game)
    positives = [value for value in losses if value > 0]
    summary = {
        "gameId": game["gameId"],
        "round": game.get("round"),
        "color": game.get("color"),
        "moveCount": len(losses),
        "totalLoss": rounded(sum(losses)),
        "meanLoss": rounded(mean(losses)),
        "medianLoss": rounded(statistics.median(losses)) if losses else None,
        "maximumLoss": rounded(max(losses)) if losses else None,
        "zeroLossRate": rounded(sum(value == 0 for value in losses) / len(losses)) if losses else None,
        "positiveLossMean": rounded(mean(positives)),
        "lossAtLeast4Count": threshold_count(losses, 4),
        "lossAtLeast10Count": threshold_count(losses, 10),
        "lossAtLeast4Rate": rounded(threshold_rate(losses, 4)),
        "lossAtLeast10Rate": rounded(threshold_rate(losses, 10)),
        "modelProbability": {
            "lossGe4": model_probability_summary([game], 4),
            "lossGe10": model_probability_summary([game], 10),
        },
    }
    boundary = validate_wld_from_ply(wld_from_ply)
    if boundary is not None:
        summary[ENGINE_WLD_TOTAL_FIELD] = engine_wld_loss_total([game], boundary)
    return summary


def aggregate_loss_games(
    games: list[dict[str, Any]], wld_from_ply: int | None = None
) -> dict[str, Any]:
    boundary = validate_wld_from_ply(wld_from_ply)
    summaries = [loss_game_summary(game, boundary) for game in games]
    losses = [value for game in games for value in node_losses(game)]
    positives = [value for value in losses if value > 0]
    aggregate = {
        "gameCount": len(games),
        "moveCount": len(losses),
        "gameWeightedMeanLoss": rounded(mean(item["meanLoss"] for item in summaries if item["meanLoss"] is not None)),
        "moveWeightedMeanLoss": rounded(mean(losses)),
        "zeroLossRate": rounded(sum(value == 0 for value in losses) / len(losses)) if losses else None,
        "positiveLossMean": rounded(mean(positives)),
        "lossAtLeast4Count": threshold_count(losses, 4),
        "lossAtLeast10Count": threshold_count(losses, 10),
        "lossAtLeast4Rate": rounded(threshold_rate(losses, 4)),
        "lossAtLeast10Rate": rounded(threshold_rate(losses, 10)),
        "modelProbability": {
            "lossGe4": model_probability_summary(games, 4),
            "lossGe10": model_probability_summary(games, 10),
        },
        "games": summaries,
    }
    if boundary is not None:
        aggregate[ENGINE_WLD_TOTAL_FIELD] = engine_wld_loss_total(games, boundary)
    return aggregate


def cluster_bootstrap_threshold_rate_differences(
    reported: list[dict[str, Any]],
    control: list[dict[str, Any]],
    repetitions: int,
    seed: int,
) -> dict[int, list[float | None]]:
    """Bootstrap all loss thresholds from the same whole-game draws."""
    rng = random.Random(seed)
    samples = {threshold: [] for threshold in LOSS_THRESHOLDS}
    for _ in range(repetitions):
        left = [reported[rng.randrange(len(reported))] for _ in reported]
        right = [control[rng.randrange(len(control))] for _ in control]
        left_losses = [value for game in left for value in node_losses(game)]
        right_losses = [value for game in right for value in node_losses(game)]
        for threshold in LOSS_THRESHOLDS:
            left_rate = threshold_rate(left_losses, threshold)
            right_rate = threshold_rate(right_losses, threshold)
            if left_rate is not None and right_rate is not None:
                samples[threshold].append(left_rate - right_rate)
    return {
        threshold: [
            rounded(quantile(values, 0.025)),
            rounded(quantile(values, 0.975)),
        ]
        if values else [None, None]
        for threshold, values in samples.items()
    }


def cluster_bootstrap_difference(
    reported: list[dict[str, Any]],
    control: list[dict[str, Any]],
    repetitions: int,
    seed: int,
    move_weighted: bool,
) -> list[float | None]:
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(repetitions):
        left = [rng.choice(reported) for _ in reported]
        right = [rng.choice(control) for _ in control]
        if move_weighted:
            left_value = mean(value for game in left for value in node_losses(game))
            right_value = mean(value for game in right for value in node_losses(game))
        else:
            left_value = mean(loss_game_summary(game)["meanLoss"] for game in left)
            right_value = mean(loss_game_summary(game)["meanLoss"] for game in right)
        if left_value is not None and right_value is not None:
            values.append(left_value - right_value)
    return [rounded(quantile(values, 0.025)), rounded(quantile(values, 0.975))]


def cluster_bootstrap_engine_wld_per_game_difference(
    reported: list[dict[str, Any]],
    control: list[dict[str, Any]],
    repetitions: int,
    seed: int,
    wld_from_ply: int,
) -> list[float | None]:
    """Whole-game bootstrap for mean engine WLD loss per game."""
    reported_values = [engine_wld_loss_total([game], wld_from_ply) for game in reported]
    control_values = [engine_wld_loss_total([game], wld_from_ply) for game in control]
    rng = random.Random(seed)
    values = [
        statistics.fmean(rng.choice(reported_values) for _ in reported_values)
        - statistics.fmean(rng.choice(control_values) for _ in control_values)
        for _ in range(repetitions)
    ]
    return [rounded(quantile(values, 0.025)), rounded(quantile(values, 0.975))]


def cluster_bootstrap_zero_loss_rate_difference(
    reported: list[dict[str, Any]], control: list[dict[str, Any]], repetitions: int, seed: int
) -> list[float | None]:
    """Whole-game bootstrap for the move-weighted zero-disc-loss rate difference."""
    rng = random.Random(seed)
    values = []
    for _ in range(repetitions):
        left = [reported[rng.randrange(len(reported))] for _ in reported]
        right = [control[rng.randrange(len(control))] for _ in control]
        left_losses = [value for game in left for value in node_losses(game)]
        right_losses = [value for game in right for value in node_losses(game)]
        values.append(
            sum(value == 0 for value in left_losses) / len(left_losses)
            - sum(value == 0 for value in right_losses) / len(right_losses)
        )
    return [rounded(quantile(values, 0.025)), rounded(quantile(values, 0.975))]


EXACT_COMBINATION_ENUMERATION_LIMIT = 1_000_000


def _combination_indices_from_rank(population: int, count: int, rank: int) -> list[int]:
    """Unrank one lexicographically ordered combination without enumerating earlier rows."""
    indices: list[int] = []
    start = 0
    remaining = count
    while remaining:
        for index in range(start, population):
            suffix_count = math.comb(population - index - 1, remaining - 1)
            if rank < suffix_count:
                indices.append(index)
                start = index + 1
                remaining -= 1
                break
            rank -= suffix_count
        else:
            raise ValueError("combination rank is outside the valid range")
    return indices


def exact_combination_position(
    universe: list[dict[str, Any]],
    selected_ids: set[str],
    *,
    maximum_combinations: int = EXACT_COMBINATION_ENUMERATION_LIMIT,
    seed: int = 20260801,
) -> dict[str, Any]:
    count = len(selected_ids)
    total = math.comb(len(universe), count)
    game_ids = [game["gameId"] for game in universe]
    game_means = [float(loss_game_summary(game)["meanLoss"]) for game in universe]
    selected_mean = statistics.fmean(
        value for game_id, value in zip(game_ids, game_means, strict=True) if game_id in selected_ids
    )
    if total > maximum_combinations:
        rng = random.Random(seed)
        sampled_ranks = rng.sample(range(total), maximum_combinations)
        lower = 0
        upper = 0
        for rank in sampled_ranks:
            indexes = _combination_indices_from_rank(len(universe), count, rank)
            value = statistics.fmean(game_means[index] for index in indexes)
            lower += value <= selected_mean + 1e-12
            upper += value >= selected_mean - 1e-12
        return {
            "status": "monte_carlo",
            "method": "uniform-combination-rank-sample-without-replacement-v1",
            "combinationCount": total,
            "enumerationLimit": maximum_combinations,
            "sampledCombinationCount": maximum_combinations,
            "seed": seed,
            "lowerTailMonteCarloP": rounded(lower / maximum_combinations),
            "upperTailMonteCarloP": rounded(upper / maximum_combinations),
            "twoTailMonteCarloP": rounded(
                min(1.0, 2.0 * min(lower, upper) / maximum_combinations)
            ),
        }
    values = [
        statistics.fmean(combo)
        for combo in combinations(game_means, count)
    ]
    lower = sum(value <= selected_mean + 1e-12 for value in values)
    upper = sum(value >= selected_mean - 1e-12 for value in values)
    return {
        "status": "computed",
        "combinationCount": total,
        "ascendingRank": lower,
        "lowerTailExactP": rounded(lower / total),
        "upperTailExactP": rounded(upper / total),
        "twoTailRankP": rounded(min(1.0, 2.0 * min(lower, upper) / total)),
    }


def compare_loss_groups(
    reported: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    universe: list[dict[str, Any]],
    repetitions: int,
    seed: int,
    wld_from_ply: int | None = None,
) -> dict[str, Any]:
    left = aggregate_loss_games(reported, wld_from_ply)
    right = aggregate_loss_games(controls, wld_from_ply)
    threshold_intervals = cluster_bootstrap_threshold_rate_differences(
        reported, controls, repetitions, seed + 2
    )
    result = {
        "reported": left,
        "control": right,
        "gameWeightedDifference": rounded(left["gameWeightedMeanLoss"] - right["gameWeightedMeanLoss"]),
        "gameWeightedClusterBootstrap95CI": cluster_bootstrap_difference(reported, controls, repetitions, seed, False),
        "moveWeightedDifference": rounded(left["moveWeightedMeanLoss"] - right["moveWeightedMeanLoss"]),
        "moveWeightedClusterBootstrap95CI": cluster_bootstrap_difference(reported, controls, repetitions, seed + 1, True),
        "zeroLossRateDifference": rounded(left["zeroLossRate"] - right["zeroLossRate"]),
        "zeroLossRateClusterBootstrap95CI": cluster_bootstrap_zero_loss_rate_difference(
            reported, controls, repetitions, seed + 4
        ),
        "lossAtLeast4RateDifference": rounded(left["lossAtLeast4Rate"] - right["lossAtLeast4Rate"]),
        "lossAtLeast10RateDifference": rounded(left["lossAtLeast10Rate"] - right["lossAtLeast10Rate"]),
        "lossAtLeast4RateClusterBootstrap95CI": threshold_intervals[4],
        "lossAtLeast10RateClusterBootstrap95CI": threshold_intervals[10],
        "exactCombination": exact_combination_position(
            universe, {game["gameId"] for game in reported}, seed=seed + 5
        ),
    }
    if wld_from_ply is not None:
        left_mean_wld = float(left[ENGINE_WLD_TOTAL_FIELD]) / len(reported)
        right_mean_wld = float(right[ENGINE_WLD_TOTAL_FIELD]) / len(controls)
        result["engineWldLossPerGameDifference"] = rounded(left_mean_wld - right_mean_wld)
        result["engineWldLossPerGameClusterBootstrap95CI"] = (
            cluster_bootstrap_engine_wld_per_game_difference(
                reported, controls, repetitions, seed + 3, wld_from_ply
            )
        )
    return result


def two_part_model(
    games: list[dict[str, Any]],
    reported_ids: set[str],
    bootstrap_repetitions: int,
    seed: int,
) -> dict[str, Any]:
    import numpy as np
    from sklearn.linear_model import LinearRegression, LogisticRegression

    def rows_from(sampled_games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for game in sampled_games:
            for node in game["nodes"]:
                if node.get("lossClipped") is None:
                    continue
                rows.append({
                    "loss": float(node["lossClipped"]),
                    "reported": 1 if game["gameId"] in reported_ids else 0,
                    "ply": float(node.get("ply") or 0),
                    "white": 1 if node.get("playerColor") == "white" else 0,
                    "tournament": 1 if game.get("isTournamentGame") else 0,
                })
        return rows

    def features(row: dict[str, Any], reported_override: int | None = None) -> list[float]:
        z = (row["ply"] - 30.0) / 20.0
        return [
            float(row["reported"] if reported_override is None else reported_override),
            z,
            float(row["white"]),
            float(row["tournament"]),
        ]

    def fit(sampled_games: list[dict[str, Any]]) -> tuple[float, float, float, float, float]:
        rows = rows_from(sampled_games)
        x = np.asarray([features(row) for row in rows], dtype=float)
        y_zero = np.asarray([1 if row["loss"] == 0 else 0 for row in rows], dtype=int)
        logistic = LogisticRegression(C=1e12, solver="lbfgs", max_iter=5000).fit(x, y_zero)
        positive = [row for row in rows if row["loss"] > 0]
        xp = np.asarray([features(row) for row in positive], dtype=float)
        log_y = np.log(np.asarray([row["loss"] for row in positive], dtype=float))
        linear = LinearRegression().fit(xp, log_y)
        residual = log_y - linear.predict(xp)
        smearing = float(np.mean(np.exp(residual)))
        reported_rows = [row for row in rows if row["reported"] == 1]

        def predicted_mean(override: int) -> float:
            xx = np.asarray([features(row, override) for row in reported_rows], dtype=float)
            p_zero = logistic.predict_proba(xx)[:, 1]
            positive_mean = np.exp(linear.predict(xx)) * smearing
            return float(np.mean((1.0 - p_zero) * positive_mean))

        predicted = predicted_mean(1)
        counterfactual = predicted_mean(0)
        return (
            math.exp(float(logistic.coef_[0][0])),
            math.exp(float(linear.coef_[0])),
            predicted,
            counterfactual,
            predicted - counterfactual,
        )

    point = fit(games)
    reported_games = [game for game in games if game["gameId"] in reported_ids]
    control_games = [game for game in games if game["gameId"] not in reported_ids]
    rng = random.Random(seed)
    samples: list[tuple[float, float, float, float, float]] = []
    for _ in range(bootstrap_repetitions):
        sampled = [rng.choice(reported_games) for _ in reported_games] + [rng.choice(control_games) for _ in control_games]
        try:
            samples.append(fit(sampled))
        except ValueError:
            continue

    def interval(index: int) -> list[float | None]:
        return [rounded(quantile((value[index] for value in samples), 0.025)), rounded(quantile((value[index] for value in samples), 0.975))]

    return {
        "controls": ["ply", "color", "tournament"],
        "positiveLossModel": "ordinary least squares on log positive loss with smearing correction",
        "clusterUnit": "game",
        "bootstrapSuccessfulFits": len(samples),
        "zeroLossOddsRatio": rounded(point[0]),
        "zeroLossOddsRatio95CI": interval(0),
        "positiveLossMeanRatio": rounded(point[1]),
        "positiveLossMeanRatio95CI": interval(1),
        "reportedPredictedMeanLoss": rounded(point[2]),
        "counterfactualPredictedMeanLoss": rounded(point[3]),
        "predictedDifference": rounded(point[4]),
        "predictedDifference95CI": interval(4),
    }


def loss_analysis(
    engine_directory: str | Path,
    account: str,
    reported_game_ids: set[str],
    bootstrap_repetitions: int,
    model_bootstrap_repetitions: int,
    seed: int,
    wld_from_ply: int | None = None,
) -> dict[str, Any]:
    boundary = validate_wld_from_ply(wld_from_ply)
    games = target_engine_games(load_engine_games(engine_directory), account)
    by_id = {game["gameId"]: game for game in games}
    missing = sorted(reported_game_ids - set(by_id))
    if missing:
        raise ValueError(f"reported game IDs missing from engine analyses: {missing}")
    reported = [by_id[game_id] for game_id in sorted(reported_game_ids)]
    controls = [game for game in games if game["gameId"] not in reported_game_ids]
    colors = {game["color"] for game in reported}
    same_color_universe = [game for game in games if game["color"] in colors]
    same_color_controls = [game for game in same_color_universe if game["gameId"] not in reported_game_ids]
    result = {
        "schema": "player-loss-analysis-v1",
        "account": account,
        "reportedGameIds": sorted(reported_game_ids),
        "reportedGames": [loss_game_summary(game, boundary) for game in reported],
        "sameColorComparison": compare_loss_groups(reported, same_color_controls, same_color_universe, bootstrap_repetitions, seed, boundary),
        "allGamesComparison": compare_loss_groups(reported, controls, games, bootstrap_repetitions, seed + 10, boundary),
        "clusterAwareTwoPartModel": two_part_model(games, reported_game_ids, model_bootstrap_repetitions, seed + 20),
    }
    if boundary is not None:
        result["wldFromPly"] = boundary
        result[ENGINE_WLD_TOTAL_FIELD] = engine_wld_loss_total(games, boundary)
    return result


def rankdata(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[order[position]] = rank
        start = end
    return ranks


def correlation(left: list[float], right: list[float], spearman: bool = False) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    if spearman:
        left = rankdata(left)
        right = rankdata(right)
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right))
    return numerator / denominator if denominator else None


def fit_mean_time_curve(control_games: list[dict[str, Any]]) -> tuple[Any, dict[int, float], dict[int, float]]:
    import numpy as np

    rows = [
        (float(node.get("ply") or 0), float(node["thinkingTimeMs"]))
        for game in control_games
        for node in game["nodes"]
        if node.get("thinkingTimeMs") is not None
    ]
    if not rows:
        raise ValueError("control games contain no thinkingTimeMs values")
    max_ply = max(row[0] for row in rows)
    knots = (0.2, 0.4, 0.6, 0.8)

    def basis(ply: float) -> list[float]:
        x = ply / max_ply
        return [1.0, x, x * x, x * x * x] + [max(0.0, x - knot) ** 3 for knot in knots]

    x = np.asarray([basis(ply) for ply, _ in rows], dtype=float)
    y = np.asarray([time for _, time in rows], dtype=float)
    penalty = np.eye(x.shape[1]) * 0.1
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(x.T @ x + penalty, x.T @ y)

    def predict(ply: float) -> float:
        return max(0.0, float(np.asarray(basis(ply)) @ coefficients))

    by_ply: dict[int, list[float]] = defaultdict(list)
    for ply, time in rows:
        by_ply[int(ply)].append(time)
    p90 = {ply: float(quantile(values, 0.9)) for ply, values in by_ply.items()}
    means = {ply: statistics.fmean(values) for ply, values in by_ply.items()}
    return predict, means, p90


def nearest_ply_value(values: dict[int, float], ply: int) -> float:
    if ply in values:
        return values[ply]
    nearest = min(values, key=lambda candidate: abs(candidate - ply))
    return values[nearest]


def time_game_metrics(game: dict[str, Any], predict: Any, p90: dict[int, float]) -> dict[str, Any]:
    nodes = [node for node in game["nodes"] if node.get("thinkingTimeMs") is not None]
    actual = [float(node["thinkingTimeMs"]) for node in nodes]
    baseline = [predict(float(node.get("ply") or 0)) for node in nodes]
    residuals = [a - b for a, b in zip(actual, baseline)]
    excesses = [max(0.0, a - nearest_ply_value(p90, int(node.get("ply") or 0))) for a, node in zip(actual, nodes)]
    long_flags = [value > 0 for value in excesses]
    return {
        "gameId": game["gameId"],
        "round": game.get("round"),
        "color": game.get("color"),
        "moveCount": len(nodes),
        "totalTimeMs": rounded(sum(actual), 3),
        "meanTimeMs": rounded(mean(actual), 3),
        "meanResidualFromBaselineMs": rounded(mean(residuals), 3),
        "meanAbsoluteResidualMs": rounded(mean(abs(value) for value in residuals), 3),
        "pearsonCurveCorrelation": rounded(correlation(actual, baseline)),
        "spearmanCurveCorrelation": rounded(correlation(actual, baseline, True)),
        "longThinkCount": sum(long_flags),
        "longThinkRate": rounded(sum(long_flags) / len(nodes)) if nodes else None,
        "longThinkExcessTotalMs": rounded(sum(excesses), 3),
        "longThinkExcessMaximumMs": rounded(max(excesses), 3) if excesses else None,
    }


def time_analysis(engine_directory: str | Path, account: str, reported_game_ids: set[str]) -> dict[str, Any]:
    games = target_engine_games(load_engine_games(engine_directory), account)
    by_id = {game["gameId"]: game for game in games}
    reported = [by_id[game_id] for game_id in sorted(reported_game_ids)]
    colors = {game["color"] for game in reported}
    controls = [game for game in games if game["gameId"] not in reported_game_ids and game["color"] in colors]
    predict, per_ply_mean, per_ply_p90 = fit_mean_time_curve(controls)
    reported_metrics = [time_game_metrics(game, predict, per_ply_p90) for game in reported]
    control_metrics = [time_game_metrics(game, predict, per_ply_p90) for game in controls]
    pair_size = len(reported)
    control_combos = list(combinations(control_metrics, pair_size))

    def combo_mean(combo: Iterable[dict[str, Any]], field: str) -> float:
        return statistics.fmean(float(item[field]) for item in combo if item[field] is not None)

    fields_and_tail = {
        "meanResidualFromBaselineMs": "upper",
        "meanAbsoluteResidualMs": "upper",
        "pearsonCurveCorrelation": "lower",
        "spearmanCurveCorrelation": "lower",
        "longThinkRate": "upper",
        "longThinkExcessTotalMs": "upper",
        "longThinkExcessMaximumMs": "upper",
    }
    pair_comparison: dict[str, Any] = {"controlCombinationCount": len(control_combos)}
    for field, tail in fields_and_tail.items():
        observed = combo_mean(reported_metrics, field)
        references = [combo_mean(combo, field) for combo in control_combos]
        extreme = sum(value >= observed for value in references) if tail == "upper" else sum(value <= observed for value in references)
        pair_comparison[field] = rounded(observed, 6)
        pair_comparison[f"{field}{tail.title()}TailPlusOneP"] = rounded((extreme + 1) / (len(references) + 1))
    return {
        "schema": "player-time-analysis-v1",
        "account": account,
        "reportedGameIds": sorted(reported_game_ids),
        "baseline": {
            "controlGameCount": len(controls),
            "color": sorted(colors),
            "center": "arithmetic mean",
            "curve": "cubic regression spline with four fixed knots and ridge alpha 0.1",
            "longThinkThreshold": "same-ply control 90th percentile",
            "perPlyRawMeanMs": {str(key): rounded(value, 3) for key, value in sorted(per_ply_mean.items())},
            "perPlyP90Ms": {str(key): rounded(value, 3) for key, value in sorted(per_ply_p90.items())},
        },
        "reportedGames": reported_metrics,
        "reportedAgainstControlCombinations": pair_comparison,
    }


def reference_group_stats(
    games: list[dict[str, Any]], wld_from_ply: int | None = None
) -> dict[str, Any]:
    aggregate = aggregate_loss_games(games, wld_from_ply)
    phases: dict[str, dict[str, Any]] = {}
    for name, predicate in {
        "ply1to20": lambda ply: ply <= 20,
        "ply21to40": lambda ply: 21 <= ply <= 40,
        "ply41plus": lambda ply: ply >= 41,
    }.items():
        phase_games: list[dict[str, Any]] = []
        for game in games:
            nodes = [node for node in game["nodes"] if predicate(int(node.get("ply") or 0))]
            if nodes:
                phase_games.append({**game, "nodes": nodes})
        phases[name] = aggregate_loss_games(phase_games)
        phases[name].pop("games", None)
    aggregate["phases"] = phases
    return aggregate


def reference_analysis(
    config: dict[str, Any], wld_from_ply: int | None = None
) -> dict[str, Any]:
    boundary = validate_wld_from_ply(
        wld_from_ply if wld_from_ply is not None else config.get("wldFromPly")
    )
    target_cfg = config["target"]
    target_games = target_engine_games(load_engine_games(target_cfg["engineDirectory"]), target_cfg["account"])
    target_ids = set(target_cfg.get("gameIds") or [])
    if target_ids:
        target_games = [game for game in target_games if game["gameId"] in target_ids]
    groups: dict[str, Any] = {}
    comparisons: dict[str, Any] = {}
    for group_cfg in config.get("groups", []):
        engine_games = load_engine_games(group_cfg["engineDirectory"])
        selected: list[dict[str, Any]] = []
        if group_cfg.get("bundle"):
            bundle = read_json(group_cfg["bundle"])
            selection_by_id = {
                str(item.get("gameId") or ""): item
                for item in bundle.get("selection", [])
                if isinstance(item, dict)
            }
            where = group_cfg.get("where") or {}
            account_field = group_cfg.get("accountField", "leaderboardAccount")
            by_id = {str(game.get("gameId") or ""): game for game in engine_games}
            for game_id, selection in selection_by_id.items():
                if any(selection.get(key) != value for key, value in where.items()):
                    continue
                account = str(selection.get(account_field) or "")
                candidate = target_engine_games([by_id[game_id]], account) if game_id in by_id else []
                selected.extend(candidate)
        else:
            selected = target_engine_games(engine_games, group_cfg["account"])
            include = set(group_cfg.get("gameIds") or [])
            exclude = set(group_cfg.get("excludeGameIds") or [])
            if include:
                selected = [game for game in selected if game["gameId"] in include]
            selected = [game for game in selected if game["gameId"] not in exclude]
        if group_cfg.get("color"):
            selected = [game for game in selected if game["color"] == group_cfg["color"]]
        name = str(group_cfg["name"])
        groups[name] = reference_group_stats(selected, boundary)
        pair_count = len(target_games)
        combinations_list = list(combinations(selected, pair_count))
        target_mean = aggregate_loss_games(target_games, boundary)["gameWeightedMeanLoss"]
        reference_means = [aggregate_loss_games(list(combo), boundary)["gameWeightedMeanLoss"] for combo in combinations_list]
        at_or_below = sum(value <= target_mean for value in reference_means)
        comparisons[name] = {
            "targetMinusReferenceGameWeightedMean": rounded(target_mean - groups[name]["gameWeightedMeanLoss"]),
            "referenceCombinationCount": len(reference_means),
            "referenceCombinationsAtOrBelowTarget": at_or_below,
            "lowerTailPlusOneP": rounded((at_or_below + 1) / (len(reference_means) + 1)) if reference_means else None,
        }
    result = {
        "schema": "player-reference-comparison-v1",
        "target": reference_group_stats(target_games, boundary),
        "groups": groups,
        "targetComparisons": comparisons,
    }
    if boundary is not None:
        result["wldFromPly"] = boundary
    return result
