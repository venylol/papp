from __future__ import annotations

import argparse
import csv
import hashlib
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
    ENGINE_WLD_TOTAL_FIELD,
    engine_wld_loss_total,
    target_engine_games,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_new_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_new_text(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.write_text(text, encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def distribution(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": rounded(float(statistics.median(values))),
        "mean": rounded(float(statistics.fmean(values))),
        "max": max(values),
    }


def ecdf(values: list[int], observed: int) -> dict[str, Any]:
    return {
        "referenceCount": len(values),
        "lessThan": sum(value < observed for value in values),
        "equal": sum(value == observed for value in values),
        "lessThanOrEqual": sum(value <= observed for value in values),
        "inclusiveEmpiricalPercentile": rounded(100 * sum(value <= observed for value in values) / len(values)),
    }


def markdown_escape(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidate validated manual off-book records and summarize reported-game comparisons.")
    parser.add_argument("--diagonal-manifest", required=True)
    parser.add_argument("--tanida-manifest", required=True)
    parser.add_argument("--records-dir", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--reported-records", required=True)
    parser.add_argument("--reported-config", required=True)
    parser.add_argument("--reported-engine-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--wld-from-ply", type=int, choices=(39,))
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    records_dir = Path(args.records_dir).resolve()
    decisions_path = Path(args.decisions).resolve()
    decisions_doc = read_json(decisions_path)
    decisions = {str(row["gameId"]): row for row in decisions_doc["decisions"]}
    if len(decisions) != 40:
        raise ValueError(f"expected 40 unique manual decisions, found {len(decisions)}")

    selections: dict[str, dict[str, Any]] = {}
    for manifest_path in (Path(args.diagonal_manifest).resolve(), Path(args.tanida_manifest).resolve()):
        manifest = read_json(manifest_path)
        opening = str(manifest["opening"])
        for row in manifest["selected"]:
            gid = str(row["gameId"])
            if gid in selections:
                raise ValueError(f"duplicate selected game ID: {gid}")
            selections[gid] = {**row, "opening": opening, "manifest": str(manifest_path)}
    if set(selections) != set(decisions):
        raise ValueError("sampling manifests and manual decisions contain different game IDs")

    validated: dict[str, dict[str, Any]] = {}
    source_records: list[dict[str, Any]] = []
    for path in sorted(records_dir.glob("*--offbook-records.json")):
        doc = read_json(path)
        if doc.get("schema") != "player-offbook-manual-records-v1" or doc.get("mode") != "reference":
            raise ValueError(f"unsupported validated records file: {path}")
        source_records.append(
            {
                "account": doc["account"],
                "path": str(path),
                "sha256": sha256(path),
                "recordCount": doc["recordCount"],
            }
        )
        for record in doc["records"]:
            gid = str(record["gameId"])
            if gid in validated:
                raise ValueError(f"duplicate validated game ID: {gid}")
            validated[gid] = {**record, "account": doc["account"], "sourceRecords": str(path)}
    if set(validated) != set(decisions):
        raise ValueError("validated records and manual decisions contain different game IDs")

    rows: list[dict[str, Any]] = []
    consolidated_records: list[dict[str, Any]] = []
    consolidated_marks: list[dict[str, Any]] = []
    for gid, selection in selections.items():
        decision = decisions[gid]
        record = validated[gid]
        for field in ("judgment", "offBookPly", "agentNote"):
            if record.get(field) != decision.get(field):
                raise ValueError(f"validated/manual mismatch for {gid} field {field}")
        if str(selection["leaderboardAccount"]).casefold() != str(record["account"]).casefold():
            raise ValueError(f"manifest/record account mismatch for {gid}")
        row = {
            "opening": selection["opening"],
            "sourcePage": int(selection["page"]),
            "leaderboardRank": selection.get("rank"),
            "leaderboardAccount": selection["leaderboardAccount"],
            "openingRating": selection.get("openingRating"),
            "gameId": gid,
            "judgment": record["judgment"],
            "offBookPly": record["offBookPly"],
            "move": record["move"],
            "thinkingTimeMs": record["thinkingTimeMs"],
            "agentNote": record["agentNote"],
        }
        rows.append(row)
        consolidated_records.append({**row, "validatedRecord": record})
        consolidated_marks.append(
            {
                "opening": row["opening"],
                "sourcePage": row["sourcePage"],
                "leaderboardAccount": row["leaderboardAccount"],
                "gameId": gid,
                "judgment": row["judgment"],
                "offBookPly": row["offBookPly"],
                "agentNote": row["agentNote"],
            }
        )
    rows.sort(key=lambda row: (row["opening"], row["sourcePage"], str(row["leaderboardAccount"]).casefold(), row["gameId"]))

    records_output = {
        "schema": "oq-opening-reference-consolidated-validated-records-v1",
        "recordCount": len(consolidated_records),
        "offBookRecordCount": sum(row["judgment"] == "offbook" for row in rows),
        "noOffBookRecordCount": sum(row["judgment"] == "no_offbook" for row in rows),
        "validationTool": "legacy-reference-records",
        "sourceRecords": source_records,
        "records": consolidated_records,
    }
    marks_output = {
        "schema": "oq-opening-reference-consolidated-agent-marks-v1",
        "reviewedBy": "agent",
        "markCount": len(consolidated_marks),
        "sourceManualDecisions": str(decisions_path),
        "sourceManualDecisionsSha256": sha256(decisions_path),
        "marks": consolidated_marks,
    }

    write_new_json(output_dir / "40-agent-marks-consolidated.json", marks_output)
    write_new_json(output_dir / "40-offbook-records-consolidated.json", records_output)
    write_new_json(output_dir / "40-offbook-summary-table.json", {"schema": "oq-opening-reference-summary-table-v1", "rowCount": 40, "rows": rows})

    csv_path = output_dir / "40-offbook-summary-table.csv"
    if csv_path.exists():
        raise FileExistsError(f"refusing to overwrite: {csv_path}")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    table_lines = [
        "# 40局排行榜参照人工脱谱汇总",
        "",
        "| Opening | 来源页 | 排名 | 排行榜账号 | gameId | 判断 | offBookPly | 落子 | thinkingTimeMs | agentNote |",
        "|---|---:|---:|---|---|---|---:|---|---:|---|",
    ]
    for row in rows:
        table_lines.append(
            "| " + " | ".join(
                markdown_escape(row[key])
                for key in (
                    "opening", "sourcePage", "leaderboardRank", "leaderboardAccount", "gameId",
                    "judgment", "offBookPly", "move", "thinkingTimeMs", "agentNote",
                )
            ) + " |"
        )
    write_new_text(output_dir / "40-offbook-summary-table.md", "\n".join(table_lines) + "\n")

    config = read_json(Path(args.reported_config).resolve())
    reported_doc = read_json(Path(args.reported_records).resolve())
    reported_by_id = {str(row["gameId"]): row for row in reported_doc["records"]}
    engine_by_id: dict[str, dict[str, Any]] = {}
    for path in Path(args.reported_engine_dir).resolve().glob("game_*.json"):
        doc = read_json(path)
        engine_by_id[str(doc.get("gameId") or "")] = doc

    opening_summaries: dict[str, dict[str, Any]] = {}
    for opening in ("Diagonal Opening", "Tanida"):
        subset = [row for row in rows if row["opening"] == opening]
        anchors = [row for row in subset if row["judgment"] == "offbook"]
        opening_summaries[opening] = {
            "sampleGameCount": len(subset),
            "offBookCount": len(anchors),
            "noOffBookCount": len(subset) - len(anchors),
            "offBookPly": distribution([int(row["offBookPly"]) for row in anchors]),
            "anchorThinkingTimeMs": distribution([int(row["thinkingTimeMs"]) for row in anchors]),
            "sourcePageCounts": {
                str(page): sum(row["sourcePage"] == page for row in subset)
                for page in sorted({row["sourcePage"] for row in subset})
            },
        }

    comparisons: list[dict[str, Any]] = []
    for meta in config["reportedGames"]:
        gid = str(meta["gameId"])
        record = reported_by_id[gid]
        opening = str(meta["opening"])
        reference_rows = [row for row in rows if row["opening"] == opening and row["judgment"] == "offbook"]
        plies = [int(row["offBookPly"]) for row in reference_rows]
        times = [int(row["thinkingTimeMs"]) for row in reference_rows]
        engine = engine_by_id.get(gid)
        if engine is None:
            raise ValueError(f"reported game missing from engine directory: {gid}")
        node = next((node for node in engine["nodes"] if int(node["ply"]) == int(record["offBookPly"])), None)
        if node is None:
            raise ValueError(f"reported anchor missing from engine nodes: {gid}")
        comparison = {
                "gameId": gid,
                "opening": opening,
                "opponentName": meta.get("opponentName"),
                "opponentAccount": meta.get("opponentAccount"),
                "targetAccount": reported_doc["account"],
                "targetColor": record["targetColor"],
                "judgment": record["judgment"],
                "offBookPly": record["offBookPly"],
                "move": record["move"],
                "thinkingTimeMs": record["thinkingTimeMs"],
                "engineLossClipped": node.get("lossClipped"),
                "agentNote": record["agentNote"],
                "matchingReference": opening_summaries[opening],
                "offBookPlyEmpiricalPosition": ecdf(plies, int(record["offBookPly"])),
                "anchorTimeEmpiricalPosition": ecdf(times, int(record["thinkingTimeMs"])),
                "differenceFromReferenceMedian": {
                    "offBookPly": rounded(float(record["offBookPly"]) - float(statistics.median(plies))),
                    "thinkingTimeMs": rounded(float(record["thinkingTimeMs"]) - float(statistics.median(times))),
                },
            }
        if args.wld_from_ply is not None:
            target_games = target_engine_games([engine], str(reported_doc["account"]))
            if len(target_games) != 1:
                raise ValueError(f"could not isolate target-player engine nodes for {gid}")
            comparison[ENGINE_WLD_TOTAL_FIELD] = engine_wld_loss_total(
                target_games, args.wld_from_ply
            )
        comparisons.append(comparison)

    comparison_output = {
        "schema": "oq-reported-vs-opening-reference-summary-v1",
        "interpretationBoundary": "Descriptive comparison only. Empirical positions are not probabilities of cheating and EG loss is supporting context, not an off-book anchor selector.",
        "referenceOpenings": opening_summaries,
        "reportedComparisons": comparisons,
    }
    if args.wld_from_ply is not None:
        comparison_output["wldFromPly"] = args.wld_from_ply
        comparison_output[ENGINE_WLD_TOTAL_FIELD] = rounded(
            sum(float(item[ENGINE_WLD_TOTAL_FIELD]) for item in comparisons), 6
        )
    write_new_json(output_dir / "reported-vs-reference-summary.json", comparison_output)

    report_lines = [
        "# 两盘举报局与排行榜参照样本对照摘要",
        "",
        "本摘要仅作描述性比较。经验位置不是作弊概率；EG子损只作背景，不参与参照局脱谱锚点选择。",
        "",
        "## 参照样本",
        "",
        "| Opening | 样本局数 | offbook | no_offbook | offBookPly（min / median / mean / max） | 锚点用时ms（min / median / mean / max） |",
        "|---|---:|---:|---:|---|---|",
    ]
    for opening, summary in opening_summaries.items():
        ply = summary["offBookPly"]
        timing = summary["anchorThinkingTimeMs"]
        report_lines.append(
            f"| {opening} | {summary['sampleGameCount']} | {summary['offBookCount']} | {summary['noOffBookCount']} | "
            f"{ply['min']} / {ply['median']} / {ply['mean']} / {ply['max']} | "
            f"{timing['min']} / {timing['median']} / {timing['mean']} / {timing['max']} |"
        )
    report_lines.extend(["", "## 举报局逐盘对照", ""])
    for item in comparisons:
        ply_pos = item["offBookPlyEmpiricalPosition"]
        time_pos = item["anchorTimeEmpiricalPosition"]
        report_lines.extend(
            [
                f"### {item['gameId']} — {item['opening']}",
                "",
                f"- 对手：{item['opponentName']}（{item['opponentAccount']}）；目标账号 {item['targetAccount']} 执白。",
                f"- 人工锚点：ply {item['offBookPly']}，{item['move']}，{item['thinkingTimeMs']}ms；EG子损 {item['engineLossClipped']}。",
                *(
                    [f"- 从实际落子 ply 39（含）起的 WLD 损失加权总和：{item[ENGINE_WLD_TOTAL_FIELD]}。"]
                    if args.wld_from_ply is not None
                    else []
                ),
                f"- 在该开局19个可定锚参照局中，offBookPly 小于/等于该局的是 {ply_pos['lessThan']}/{ply_pos['equal']} 局，含等号经验位置 {ply_pos['inclusiveEmpiricalPercentile']}%。",
                f"- 锚点用时小于/等于该局的是 {time_pos['lessThan']}/{time_pos['equal']} 局，含等号经验位置 {time_pos['inclusiveEmpiricalPercentile']}%。",
                f"- 相对该参照中位数：锚点晚 {item['differenceFromReferenceMedian']['offBookPly']} ply，锚点用时差 {item['differenceFromReferenceMedian']['thinkingTimeMs']}ms。",
                f"- 原人工说明：{item['agentNote']}",
                "",
            ]
        )
    write_new_text(output_dir / "reported-vs-reference-summary.md", "\n".join(report_lines))
    terminal = {"ok": True, "rows": len(rows), "comparisons": len(comparisons)}
    if args.wld_from_ply is not None:
        terminal[ENGINE_WLD_TOTAL_FIELD] = comparison_output[ENGINE_WLD_TOTAL_FIELD]
    print(json.dumps(terminal, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
