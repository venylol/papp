from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_new(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize manually authored reference decisions into one marks file per review packet."
    )
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--packet-dir", required=True)
    parser.add_argument("--marks-dir", required=True)
    parser.add_argument("--packet-manifest", required=True)
    parser.add_argument("--consolidated-packet", required=True)
    args = parser.parse_args()

    decisions_path = Path(args.decisions).resolve()
    packet_dir = Path(args.packet_dir).resolve()
    marks_dir = Path(args.marks_dir).resolve()
    source = read_json(decisions_path)
    if source.get("schema") != "oq-opening-reference-manual-decisions-v1":
        raise ValueError("unsupported decisions schema")
    rows = source.get("decisions")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("decisions must be an array of objects")
    by_game: dict[str, dict[str, Any]] = {}
    for row in rows:
        gid = str(row.get("gameId") or "")
        if not gid or gid in by_game:
            raise ValueError(f"missing or duplicate decision gameId: {gid!r}")
        by_game[gid] = row

    packet_entries: list[dict[str, Any]] = []
    consolidated_games: list[dict[str, Any]] = []
    seen: set[str] = set()
    for packet_path in sorted(packet_dir.glob("*--review-packet.json")):
        packet = read_json(packet_path)
        account = str(packet.get("account") or "")
        packet_games = packet.get("games")
        if not isinstance(packet_games, list) or not packet_games:
            raise ValueError(f"packet has no games: {packet_path}")
        marks: list[dict[str, Any]] = []
        opening_values: set[str] = set()
        for game in packet_games:
            gid = str(game.get("gameId") or "")
            decision = by_game.get(gid)
            if decision is None:
                raise ValueError(f"packet game has no manual decision: {gid}")
            if str(decision.get("account") or "").casefold() != account.casefold():
                raise ValueError(f"account mismatch for {gid}")
            if gid in seen:
                raise ValueError(f"game appears in more than one packet: {gid}")
            seen.add(gid)
            opening_values.add(str(decision.get("opening") or ""))
            marks.append(
                {
                    "gameId": gid,
                    "judgment": decision.get("judgment"),
                    "offBookPly": decision.get("offBookPly"),
                    "agentNote": decision.get("agentNote"),
                }
            )
            consolidated_games.append(
                {
                    "opening": decision.get("opening"),
                    "page": decision.get("page"),
                    "leaderboardAccount": account,
                    "sourcePacket": str(packet_path),
                    "game": game,
                }
            )
        if len(opening_values) != 1:
            raise ValueError(f"packet spans multiple openings: {packet_path}")
        packet_hash = sha256(packet_path)
        marks_name = packet_path.name.replace("--review-packet.json", "--agent-marks.json")
        marks_path = marks_dir / marks_name
        write_new(
            marks_path,
            {
                "schema": "player-offbook-agent-marks-input-v1",
                "account": account,
                "mode": "reference",
                "reviewedBy": "agent",
                "sourcePacketSha256": packet_hash,
                "marks": marks,
            },
        )
        packet_entries.append(
            {
                "opening": next(iter(opening_values)),
                "account": account,
                "gameCount": len(packet_games),
                "gameIds": [str(game["gameId"]) for game in packet_games],
                "packet": str(packet_path),
                "packetSha256": packet_hash,
                "marks": str(marks_path),
                "marksSha256": sha256(marks_path),
            }
        )

    missing = sorted(set(by_game) - seen)
    if missing:
        raise ValueError(f"manual decisions not represented in packets: {missing}")
    if len(seen) != 40:
        raise ValueError(f"expected exactly 40 reviewed games, found {len(seen)}")
    write_new(
        Path(args.packet_manifest).resolve(),
        {
            "schema": "oq-opening-reference-review-packet-manifest-v1",
            "gameCount": len(seen),
            "packetCount": len(packet_entries),
            "packets": packet_entries,
        },
    )
    write_new(
        Path(args.consolidated_packet).resolve(),
        {
            "schema": "oq-opening-reference-consolidated-review-packet-v1",
            "gameCount": len(consolidated_games),
            "manualReviewPolicy": "No automatic anchor selection; read each source packet's raw per-ply thinking times.",
            "games": consolidated_games,
        },
    )
    print(json.dumps({"ok": True, "gameCount": len(seen), "packetCount": len(packet_entries)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
