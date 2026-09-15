"use strict";

// The browser adapter remains the single translation boundary to PAPP C.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { sanitizeTournamentParameters } = require("./app.js");
const adapterScript = new vm.Script(fs.readFileSync(path.join(__dirname, "tournament-adapter.js"), "utf8"));
const clone = value => JSON.parse(JSON.stringify(value));
const idOf = value => String(value.id || value.pairingId || "");
const bye = p => p.status === "bye";
const confirmed = p => bye(p) || (p.status === "completed" && Boolean(p.pappReadbackAt));
const countOf = s => Number(s.scoreHelper.preliminaryRoundCount || s.scoreHelper.roundCount);
const unresolvedPending = p => p.resolvedByReferee !== true && p.resolutionStatus !== "resolved";
const assertCurrent = async options => { if (options.assertCurrent) await options.assertCurrent(); };
function localTimestamp(nowMs) {
  const d = new Date(nowMs);
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function adapterFor(request) {
  const window = { setTimeout, fetch: async (url, options = {}) => {
    const value = await request(url, options.body ? JSON.parse(options.body) : undefined);
    return { ok: value?.ok !== false, status: value?.ok === false ? 400 : 200,
      text: async () => JSON.stringify(value), json: async () => value };
  } };
  adapterScript.runInContext(vm.createContext({ window, setTimeout }));
  return window.PAPP_TOURNAMENT_ADAPTER;
}

function metadata(state) {
  state.ap ||= {};
  state.ap.tournament ||= {};
  state.ap.tournament.exports ||= {};
  return state.ap.tournament;
}

function activeTarget(state) {
  if (state.step === "final-registration") {
    const stage = state.playoffRegistration?.activeStage === "placement" ? "placement" : "semifinal";
    const skipSemifinal = state.tournamentParameters?.skipSemifinal === true;
    return { stage, round: countOf(state) + (stage === "placement" && !skipSemifinal ? 2 : 1) };
  }
  if (state.step !== "score-helper") return null;
  return { stage: "preliminary", round: Number(state.scoreHelper.activeRound) || 1 };
}

function group(state, target) {
  if (target.stage === "preliminary") return state.scoreHelper.rounds[target.round - 1];
  const p = state.playoffRegistration || {};
  return { stage: target.stage, round: target.round, pairings: p[target.stage + "Pairings"] || [],
    roundStartAt: p[target.stage + "RoundStartAt"] || "" };
}

function context(state, target) {
  const roundData = group(state, target);
  return { state, ...target, roundData, ...(roundData?.pairings?.length ? {pairings: roundData.pairings} : {}),
    roundStartAt: roundData?.roundStartAt || "", candidatePlayers: state.players || [],
    checkedInPlayers: (state.players || []).filter(p => p.checkedIn === true),
    mapping: state.mapping, preliminaryRoundCount: countOf(state), rosterSource: "checkin" };
}

function success(result, message) {
  if (!result || result.ok !== true || result.source !== "papp-c") {
    throw new Error(result?.message || result?.error || message);
  }
  return result;
}

function mergeOq(state, target, result) {
  if (result.source !== "papp-c") throw new Error("OQ 结果不是 PAPP C 核算结果");
  const data = group(state, target);
  const lookup = (t, id) => group(state, t)?.pairings.find(p => idOf(p) === String(id));
  const humanPendingIds = new Set([
    ...(data.pending || []).filter(p => /^(user-|manual-)/.test(p.pendingKind || "")),
    ...(data.manualPending || []),
  ].filter(unresolvedPending).map(p => String(p.pairingId || "")).filter(Boolean));
  // Keep references stable while merging accounts, scores and transcripts.
  for (const row of result.pairingAccountUpdates || []) {
    const p = lookup({stage: row.stage || "preliminary", round: row.round}, row.pairingId);
    if (p) for (const side of ["black", "white"]) if (row[side + "Account"] !== undefined) {
      p[side + "Account"] = row[side + "Account"];
      p[side + "OqAccount"] = row[side + "Account"];
    }
  }
  for (const row of result.ready || []) {
    const p = lookup(target, idOf(row));
    if (!p) throw new Error("OQ ready 引用了未知配对");
    if (confirmed(p) || humanPendingIds.has(idOf(p)) || ["human", "user"].includes(p.lastEditedBy)) continue;
    if (row.status !== "ready") throw new Error("OQ ready 状态无效");
    Object.assign(p, clone(row), { id: idOf(p), pappReadbackAt: "" });
  }
  const pending = [];
  for (const row of result.pending || []) {
    const p = lookup(target, row.pairingId || row.id);
    if (!p) throw new Error("OQ pending 引用了未知配对");
    if (confirmed(p) || humanPendingIds.has(idOf(p)) || ["human", "user"].includes(p.lastEditedBy)) continue;
    Object.assign(p, {status: "pending", reason: row.reason || row.message || "", pendingKind: "oq-auto"});
    pending.push({...clone(row), pendingKind: "oq-auto"});
  }
  if (target.stage === "preliminary") {
    data.pending = [...(data.pending || []).filter(p => p.pendingKind !== "oq-auto"), ...pending];
  }
  for (const row of result.gameAvailable || []) {
    const p = lookup(target, idOf(row));
    if (p) for (const key of ["oqGameAvailable", "oqGameAvailableAt", "oqGameAvailableAudit"]) p[key] = row[key];
  }
  for (const row of result.oqGameRecords || []) {
    const p = lookup({stage: row.stage || "preliminary", round: row.round}, row.pairingId);
    if (p && row.gameRecord) {
      p.oqGameId = row.oqGameId || row.gameRecord.gameId;
      p.metadata = {...p.metadata, gameRecord: clone(row.gameRecord)};
    }
  }
}

async function exportOnce(state, target, kind, result, options) {
  const meta = metadata(state);
  const key = `${state.scoreHelper.pappWorkfileId || "competition"}:${target.stage}:${target.round}:${kind}`;
  if (meta.exports[key]) return;
  await options.exportImage(kind, {state, target, ...result}, key);
  meta.exports[key] = new Date(options.nowMs).toISOString();
}

function nextTarget(state, target) {
  const count = countOf(state);
  const skipSemifinal = state.tournamentParameters?.skipSemifinal === true;
  if (target.stage === "preliminary" && target.round < count) return {stage: "preliminary", round: target.round + 1};
  if (target.stage === "preliminary" && state.tournamentParameters.hasSemifinalAndFinal) return {stage: skipSemifinal ? "placement" : "semifinal", round: count + 1};
  if (target.stage === "semifinal") return {stage: "placement", round: count + 2};
  return {stage: "overall", round: count + (state.tournamentParameters.hasSemifinalAndFinal ? (skipSemifinal ? 1 : 2) : 0)};
}

async function tick(input, options) {
  const state = clone(input);
  const target = activeTarget(state);
  if (!target) return {state, changed: false};
  const data = group(state, target);
  if (!data?.pairings?.length) return {state, changed: false};
  const meta = metadata(state);
  const adapter = options.adapter || adapterFor(options.request);
  try {
    if (!data.roundStartAt) {
      data.roundStartAt = localTimestamp(options.nowMs);
      if (target.stage !== "preliminary") state.playoffRegistration[target.stage + "RoundStartAt"] = data.roundStartAt;
    }
    const interval = Math.max(1, Number(state.ui?.oqPollSeconds) || 15) * 1000;
    const pollKey = `${target.stage}:${target.round}`;
    if (!meta.lastPollAt || meta.pollKey !== pollKey || options.nowMs - meta.lastPollAt >= interval) {
      meta.lastPollAt = options.nowMs;
      meta.pollKey = pollKey;
      let result;
      try { result = await adapter.pollOqRound(context(state, target)); }
      catch (error) { result = {ok: false, message: error.message}; }
      if (result?.source === "papp-c") mergeOq(state, target, result);
      meta.lastOqError = result?.ok === false ? result.message || result.error || "OQ 查询失败" : "";
      // Starting EG only queues the existing server worker; its completion is never a gate.
      try {
        const eg = await adapter.startEgAnalysis(context(state, target));
        const analysis = eg?.analysis || eg?.egAnalysis || eg?.report;
        if (analysis) state.egAnalysis = clone(analysis);
        meta.lastEgError = eg?.ok === false && eg.code !== "eg-record-missing" ? eg.message || eg.error || "EG 启动失败" : "";
      } catch (error) { meta.lastEgError = error.message; }
    }
    if (options.allowCommit === false) {
      return {state, changed: JSON.stringify(state) !== JSON.stringify(input)};
    }
    await exportOnce(state, target, "pairings", {pairings: data.pairings}, options);
    const unresolved = [...(data.pending || []), ...(data.manualPending || [])].some(unresolvedPending);
    if (unresolved || data.pairings.some(p => !bye(p) && !confirmed(p) && p.status !== "ready")) {
      return {state, changed: JSON.stringify(state) !== JSON.stringify(input)};
    }
    const rows = data.pairings.filter(p => !confirmed(p));
    if (rows.length) {
      if (rows.some(p => !Number.isFinite(p.blackScore) || !Number.isFinite(p.whiteScore))) {
        throw new Error("待写入比分缺失或无效");
      }
      const batchSignature = JSON.stringify(rows.map(p => [idOf(p), p.table, p.black, p.white,
        p.blackAccount, p.whiteAccount, p.blackScore, p.whiteScore]));
      if (meta.batchSignature !== batchSignature) {
        meta.batchSignature = batchSignature;
        meta.batchId = `ap-${target.stage}-${target.round}-${options.nowMs}`;
      }
      const batchId = meta.batchId;
      const ctx = {...context(state, target), batchId, pairings: clone(rows), pairingIds: rows.map(idOf)};
      await assertCurrent(options);
      success(await adapter.writeScoreBatch(ctx), "PAPP 批量写入失败");
      // readScoreBatch validates the C source and each persisted identity internally;
      // its public result deliberately contains only ok, batchId and pairings.
      const read = await adapter.readScoreBatch(ctx);
      if (read?.ok !== true) throw new Error(read?.message || read?.error || "PAPP 比分读回失败");
      for (const row of rows) {
        const matches = (read.pairings || []).filter(r => idOf(r) === idOf(row) && r.status === "completed" &&
          r.blackScore === row.blackScore && r.whiteScore === row.whiteScore);
        if (matches.length !== 1) throw new Error("PAPP 比分读回不完整或不一致");
      }
      for (const row of rows) Object.assign(row, {status: "completed", pappReadbackAt: new Date(options.nowMs).toISOString()});
    }
    const status = success(await adapter.getStageStatus(context(state, target)), "PAPP 阶段状态读取失败");
    if (status.canAdvance !== true) throw new Error(status.message || "PAPP C 尚未确认本阶段可以推进");
    await assertCurrent(options);
    await exportOnce(state, target, "scores", {pairings: data.pairings}, options);
    return {state, changed: true, next: nextTarget(state, target)};
  } catch (error) {
    return {state, changed: true, error: error.message};
  }
}

async function enterNext(input, target, options) {
  const state = clone(input);
  const adapter = options.adapter || adapterFor(options.request);
  try {
    await assertCurrent(options);
    metadata(state);
    if (target.stage === "preliminary" && target.round === 1) {
      state.tournamentParameters = sanitizeTournamentParameters(state.tournamentParameters, state.players);
    }
    if (target.stage === "preliminary" && target.round === 1 &&
        !state.scoreHelper.rounds.some(r => r.pairings?.length)) {
      const result = success(await adapter.getRoundCount(context(state, target)), "PAPP 预赛轮数读取失败");
      const rounds = Number(result.roundCount);
      if (!Number.isInteger(rounds) || rounds < 1) throw new Error("PAPP 预赛轮数无效");
      // Only initialize a new competition. Existing original rounds are immutable here.
      const previous = state.scoreHelper.rounds;
      state.scoreHelper.preliminaryRoundCount = rounds;
      state.scoreHelper.roundCount = rounds;
      state.scoreHelper.rounds = Array.from({length: rounds}, (_, i) => previous[i] || {
        round: i + 1, stage: "preliminary", pairings: [], roundStartAt: "", pending: [], manualPending: [],
      });
    }
    if (target.stage === "overall") {
      await assertCurrent(options);
      const result = success(await adapter.getOverallStandings(context(state, target)), "PAPP 最终排名读取失败");
      storeStandings(state, "overall", target.round, result, options.nowMs);
      await exportOnce(state, target, "overall", result, options);
      return {state, changed: true, complete: true};
    }
    const skipSemifinal = state.tournamentParameters?.skipSemifinal === true;
    if (target.stage === "semifinal" || (target.stage === "placement" && skipSemifinal)) {
      const result = success(await adapter.getPreliminaryStandings(context(state, target)), "PAPP 预赛排名读取失败");
      if (!Array.isArray(result.standings) || result.standings.length < 4) throw new Error("不足 4 名选手，请裁判处理淘汰赛安排");
      storeStandings(state, "preliminary", countOf(state), result, options.nowMs);
      await exportOnce(state, target, "preliminary", result, options);
    }
    let data = group(state, target);
    if (target.stage === "preliminary") {
      if (!data) throw new Error("目标预赛轮不存在");
      const paired = Boolean(data.pairings?.length);
      const due = (state.plannedWithdrawals || []).filter(p => p.round <= target.round);
      if (paired) {
        for (const plan of due) plan.round = target.round + 1;
      } else {
        const ids = new Set(due.map(p => String(p.playerId)));
        for (const player of state.players || []) if (ids.has(String(player.id))) {
          player.checkedIn = false;
          player.checkedInAt = null;
        }
        state.plannedWithdrawals = (state.plannedWithdrawals || []).filter(p => p.round > target.round);
      }
    }
    if (!data?.pairings?.length) {
      await assertCurrent(options);
      const result = success(await adapter.importPairings({...context(state, target), mode: target.round === 1 ? "start-score-registration" : "ap-enter-round"}), "PAPP 配对生成失败");
      if (!result.pairings?.length) throw new Error("PAPP 配对表为空");
      if (target.stage === "preliminary") data.pairings = clone(result.pairings);
      else {
        state.playoffRegistration ||= {preliminaryRoundCount: countOf(state)};
        state.playoffRegistration[target.stage + "Pairings"] = clone(result.pairings);
      }
      data = group(state, target);
    }
    await assertCurrent(options);
    const start = data.roundStartAt || localTimestamp(options.nowMs);
    if (target.stage === "preliminary") {
      data.roundStartAt = start;
      state.scoreHelper.activeRound = target.round;
      state.step = "score-helper";
    } else {
      state.playoffRegistration[target.stage + "RoundStartAt"] = start;
      state.playoffRegistration.activeStage = target.stage;
      state.step = "final-registration";
    }
    await exportOnce(state, target, "pairings", {pairings: data.pairings}, options);
    metadata(state).lastPollAt = 0;
    return {state, changed: true};
  } catch (error) { return {state, changed: true, error: error.message}; }
}

function storeStandings(state, kind, round, result, nowMs) {
  state.standingsSnapshots = (state.standingsSnapshots || []).filter(s => s.kind !== kind || s.round !== round);
  state.standingsSnapshots.push({kind, round, source: "papp-c", operation: result.operation,
    pappWorkfileId: state.scoreHelper.pappWorkfileId, capturedAt: nowMs,
    progress: result.progress || {}, standings: clone(result.standings || [])});
}

module.exports = {tick, enterNext, activeTarget, mergeOq, adapterFor};
