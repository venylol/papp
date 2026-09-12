"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { adapterFor } = require("./papp-ap-tournament.js");
const { storedTranscriptRecord } = require("./papp-eg-analysis.js");

const COLUMNS = ["记录类型", "比赛名称", "比赛编号", "存档时间", "阶段", "轮次", "桌号", "配对编号",
  "黑方", "白方", "黑方账号", "白方账号", "黑方比分", "白方比分", "比分状态", "棋谱", "初始棋盘", "棋谱详情JSON",
  "黑方总子损", "白方总子损", "黑方平均子损", "白方平均子损", "子损详情JSON",
  "名次", "选手编号", "选手", "积分", "Brightwell", "总棋子数", "预赛名次", "排名来源", "数据状态", "详细数据JSON"];

function csvCell(value) {
  let text = value == null ? "" : String(value);
  // Keep user-entered names and transcripts as text when opened in Excel.
  if (/^[\s]*[=+@-]/u.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g, '""') + '"';
}

function pairingLoss(state, stage, round, pairing) {
  const loss = state.egAnalysis?.pairingLossByRound?.[round]?.[pairing.table];
  const key = value => String(value || "").normalize("NFKC").trim().toLowerCase();
  if (!loss || key(loss.stage) !== key(stage) ||
      (loss.pairingId && String(loss.pairingId) !== String(pairing.id || pairing.pairingId)) ||
      (loss.gameId && String(loss.gameId) !== String(pairing.oqGameId || pairing.metadata?.gameRecord?.gameId || ""))) return {};
  const result = {};
  for (const side of ["black", "white"]) {
    const account = key(pairing[side + "Account"] || pairing[side + "OqAccount"]);
    if (!account || account !== key(loss[side + "Account"])) continue;
    result[side] = (loss.players || []).find(player =>
      key(player.ftdSide || player.color) === side &&
      (key(player.account) === account || key(player.name) === key(pairing[side] || pairing[side + "Name"])));
  }
  return result;
}

async function createArchive(state, { request, directory, now = new Date() }) {
  if (!state || typeof state !== "object" || !state.scoreHelper || !Array.isArray(state.scoreHelper.rounds)) {
    throw new Error("缺少比赛状态，无法存档");
  }
  const count = Number(state.scoreHelper.preliminaryRoundCount || state.scoreHelper.roundCount);
  if (!Number.isInteger(count) || count < 1 || count > 128) throw new Error("比赛轮数无效");
  const rows = [];
  let missingCount = 0;
  const add = row => rows.push({ "比赛名称": state.competitionName || "未命名比赛",
    "比赛编号": state.scoreHelper.pappWorkfileId || "未设置", "存档时间": now.toISOString(), ...row });
  const missing = (row, message) => { missingCount++; add({ ...row, "数据状态": message }); };
  const stages = Array.from({ length: count }, (_, index) => ({ stage: "preliminary", label: "预赛", round: index + 1,
    pairings: state.scoreHelper.rounds[index]?.pairings || [] }));
  if (state.tournamentParameters?.hasSemifinalAndFinal !== false) {
    stages.push({ stage: "semifinal", label: "半决赛", round: count + 1, pairings: state.playoffRegistration?.semifinalPairings || [] },
      { stage: "placement", label: "决赛及三四名赛", round: count + 2, pairings: state.playoffRegistration?.placementPairings || [] });
  }
  for (const group of stages) {
    const base = { "记录类型": "对局", "阶段": group.label, "轮次": group.round };
    if (!group.pairings.length) missing(base, "缺失：尚无配对");
    for (const p of group.pairings) {
      const bye = p.status === "bye";
      const record = storedTranscriptRecord(p);
      const loss = pairingLoss(state, group.stage, group.round, p);
      const absent = [];
      const score = side => {
        const value = p[side + "Score"];
        if (typeof value === "number" && Number.isFinite(value)) return value;
        if (!bye) absent.push(side === "black" ? "黑方比分" : "白方比分");
        return bye ? "不适用（轮空）" : "缺失";
      };
      const metric = (side, field) => {
        if (bye) return "不适用（轮空）";
        const value = loss[side]?.[field];
        if (typeof value === "number" && Number.isFinite(value)) return value;
        absent.push((side === "black" ? "黑方" : "白方") + (field === "totalLoss" ? "总子损" : "平均子损"));
        return "缺失";
      };
      if (!bye && !record?.moves?.length) absent.push("棋谱");
      const row = { ...base, "桌号": p.table, "配对编号": p.id || p.pairingId,
        "黑方": p.black || p.blackName, "白方": p.white || p.whiteName,
        "黑方账号": p.blackAccount || p.blackOqAccount, "白方账号": p.whiteAccount || p.whiteOqAccount,
        "黑方比分": score("black"), "白方比分": score("white"),
        "比分状态": bye ? "轮空" : p.status === "completed" && p.pappReadbackAt ? "PAPP C 已确认" : "尚未由 PAPP C 确认",
        "棋谱": bye ? "不适用（轮空）" : record?.moves?.length ? record.moves.join("") : "缺失",
        "初始棋盘": record?.board || "", "棋谱详情JSON": JSON.stringify(record || null),
        "黑方总子损": metric("black", "totalLoss"), "白方总子损": metric("white", "totalLoss"),
        "黑方平均子损": metric("black", "averageLoss"), "白方平均子损": metric("white", "averageLoss"),
        "子损详情JSON": JSON.stringify(loss), "详细数据JSON": JSON.stringify(p) };
      if (absent.length) missing(row, "缺失：" + absent.join("、"));
      else add({ ...row, "数据状态": "资料齐全" });
    }
  }
  const adapter = adapterFor(request);
  const ctx = { state, candidatePlayers: state.players || [], checkedInPlayers: (state.players || []).filter(p => p.checkedIn === true),
    mapping: state.mapping, preliminaryRoundCount: count, rosterSource: "checkin" };
  async function rankings(round, overall) {
    const base = { "记录类型": overall ? "总排名" : "轮末排名", "阶段": overall ? "赛事总排名" : "预赛", "轮次": overall ? "" : round };
    try {
      const result = await adapter[overall ? "getOverallStandings" : "getRoundStandings"]({ ...ctx, round, stage: overall ? "overall" : "preliminary" });
      if (result?.ok !== true || result.source !== "papp-c" || !Array.isArray(result.standings)) throw new Error(result?.message || "PAPP C 未返回有效排名");
      if (!overall && (result.operation !== "round-standings" || Number(result.round) !== round)) throw new Error("PAPP C 排名轮次不匹配");
      if (!result.standings.length) { missing(base, "缺失：PAPP C 尚未产生排名（比赛阶段未完成）"); return; }
      const complete = overall ? result.stageProgress?.complete === true : result.progress?.complete === true;
      for (const p of result.standings) add({ ...base, "名次": p.rank, "选手编号": p.playerId || p.id,
        "选手": p.displayName || p.name, "积分": p.totalPoints, "Brightwell": p.brightwell, "总棋子数": p.totalDiscs,
        "预赛名次": p.preliminaryRank, "排名来源": "PAPP C", "数据状态": complete ? "轮末已完成" : "暂算排名（本轮未完成）",
        "详细数据JSON": JSON.stringify(p) });
    } catch (error) {
      missing(base, "缺失：排名读取失败；" + error.message);
    }
  }
  for (let round = 1; round <= count; round++) await rankings(round, false);
  await rankings(count + (stages.length > count ? 2 : 0), true);
  const csv = "\uFEFF" + [COLUMNS.map(csvCell).join(","), ...rows.map(row => COLUMNS.map(col => csvCell(row[col])).join(","))].join("\r\n") + "\r\n";
  let name = String(state.competitionName || "比赛").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").replace(/[. ]+$/, "").slice(0, 70) || "比赛";
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) name = "_" + name;
  fs.mkdirSync(directory, { recursive: true });
  for (let suffix = 0; ; suffix++) {
    const file = path.join(directory, `${name}${suffix || ""}.csv`);
    try {
      fs.writeFileSync(file, csv, { encoding: "utf8", flag: "wx" });
      return { ok: true, file, missingCount, rowCount: rows.length };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }
  }
}

module.exports = { createArchive, csvCell, pairingLoss, COLUMNS };
