from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def account_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def verify_bundle(path: Path, opening: str, expected_pages: dict[int, int]) -> dict[str, Any]:
    bundle = read_json(path)
    selection = bundle.get("selection")
    details = bundle.get("details")
    index = bundle.get("index")
    if not all(isinstance(value, list) and len(value) == 20 for value in (selection, details, index)):
        raise ValueError(f"{path.name}: selection/index/details must each contain 20 rows")
    ids = [str(row["gameId"]) for row in selection]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name}: duplicate selected game IDs")
    if {str(row.get("id") or "") for row in details} != set(ids):
        raise ValueError(f"{path.name}: detail IDs do not match selection")
    if {str(row.get("id") or "") for row in index} != set(ids):
        raise ValueError(f"{path.name}: index IDs do not match selection")
    page_counts = {page: sum(int(row["page"]) == page for row in selection) for page in expected_pages}
    if page_counts != expected_pages:
        raise ValueError(f"{path.name}: page allocation mismatch: {page_counts}")
    selection_by_id = {str(row["gameId"]): row for row in selection}
    required_attr = "opening:DiagonalOpening" if opening == "Diagonal Opening" else "opening:Tanida"
    for detail in details:
        gid = str(detail["id"])
        row = selection_by_id[gid]
        if detail.get("gtype") != "reversi" or detail.get("tcb") != 300000:
            raise ValueError(f"{path.name}: mode/tcb mismatch in {gid}")
        if required_attr not in detail.get("attrs", []):
            raise ValueError(f"{path.name}: required opening attr missing in {gid}")
        players = detail.get("players")
        if not isinstance(players, list) or len(players) != 2:
            raise ValueError(f"{path.name}: invalid players in {gid}")
        white = players[1] if isinstance(players[1], dict) else {}
        if account_key(white.get("id") or white.get("name")) != account_key(row["leaderboardAccount"]):
            raise ValueError(f"{path.name}: leaderboard account is not white in {gid}")
        moves = detail.get("position", {}).get("moves")
        if not isinstance(moves, list) or any(not isinstance(move, dict) or "t" not in move for move in moves):
            raise ValueError(f"{path.name}: original move times missing in {gid}")
    return {"path": str(path), "sha256": sha256(path), "gameCount": 20, "pageCounts": page_counts}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a completed opening-reference manual-review output directory.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--protected-file", action="append", default=[])
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    report_path = Path(args.report).resolve()
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite: {report_path}")

    json_files = sorted(output.rglob("*.json"))
    for path in json_files:
        read_json(path)
    text_files = sorted(
        path for path in output.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".json", ".csv", ".md", ".py"}
    )
    bom_files: list[str] = []
    for path in text_files:
        raw = path.read_bytes()
        raw.decode("utf-8")
        if raw.startswith(b"\xef\xbb\xbf"):
            bom_files.append(str(path))
    if bom_files:
        raise ValueError(f"UTF-8 BOM found: {bom_files}")

    bundles = [
        verify_bundle(output / "diagonal-20-bundle.json", "Diagonal Opening", {5: 7, 10: 6, 20: 7}),
        verify_bundle(output / "tanida-20-bundle.json", "Tanida", {4: 7, 5: 6, 6: 7}),
    ]
    all_ids: list[str] = []
    for name in ("diagonal-20-sampling-manifest.json", "tanida-20-sampling-manifest.json"):
        all_ids.extend(str(value) for value in read_json(output / name)["selectedGameIds"])
    if len(all_ids) != 40 or len(all_ids) != len(set(all_ids)):
        raise ValueError("combined samples must contain exactly 40 unique game IDs")

    packet_manifest = read_json(output / "40-review-packets-manifest.json")
    marks = read_json(output / "40-agent-marks-consolidated.json")
    records = read_json(output / "40-offbook-records-consolidated.json")
    summary = read_json(output / "40-offbook-summary-table.json")
    comparison = read_json(output / "reported-vs-reference-summary.json")
    if packet_manifest.get("gameCount") != 40 or packet_manifest.get("packetCount") != 24:
        raise ValueError("review packet manifest count mismatch")
    if marks.get("markCount") != 40:
        raise ValueError("consolidated marks count mismatch")
    if records.get("recordCount") != 40 or records.get("offBookRecordCount") != 38 or records.get("noOffBookRecordCount") != 2:
        raise ValueError("consolidated validated records count mismatch")
    if summary.get("rowCount") != 40:
        raise ValueError("summary row count mismatch")
    if len(comparison.get("reportedComparisons", [])) != 2:
        raise ValueError("reported comparison count mismatch")
    packet_files = list((output / "review-packets").glob("*.json"))
    marks_files = list((output / "agent-marks").glob("*.json"))
    records_files = list((output / "validated-records").glob("*.json"))
    if (len(packet_files), len(marks_files), len(records_files)) != (24, 24, 24):
        raise ValueError("per-account packet/marks/records file count mismatch")
    for path in packet_files:
        packet = read_json(path)
        if any(game.get("targetColor") != "white" for game in packet.get("games", [])):
            raise ValueError(f"packet includes non-white target game: {path}")

    protected = []
    for raw_path in args.protected_file:
        path = Path(raw_path).resolve()
        protected.append(
            {
                "path": str(path),
                "sha256": sha256(path),
                "size": path.stat().st_size,
                "lastWriteTime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    report = {
        "schema": "oq-opening-reference-output-verification-v1",
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "outputDirectory": str(output),
        "jsonFileCount": len(json_files),
        "utf8TextFileCount": len(text_files),
        "utf8BomFileCount": 0,
        "combinedUniqueGameCount": len(all_ids),
        "packetCount": len(packet_files),
        "marksFileCount": len(marks_files),
        "validatedRecordsFileCount": len(records_files),
        "offBookRecordCount": records["offBookRecordCount"],
        "noOffBookRecordCount": records["noOffBookRecordCount"],
        "bundles": bundles,
        "protectedReadOnlyInputsSnapshot": protected,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
