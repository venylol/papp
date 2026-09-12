"use strict";

// Explicitly run with node; never included in *.test.cjs automatic suites.
// This harness runs a historical clock in memory. C score persistence is replaced
// at the injected HTTP boundary and cannot reach the executable.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const root = path.resolve(__dirname, "../..");
const read = relative => fs.readFileSync(path.join(root, relative), "utf8").replace(/^\uFEFF/, "");
const clone = value => structuredClone(value);
const logic = require("./app.js");
const { ApCoordinator, mergeChanges } = require("./papp-ap-coordinator.js");
const checkin = require("./papp-ap-checkin.js");
const tournament = require("./papp-ap-tournament.js");
const imageExporter = require("./papp-ap-export.js");
const sharedPath = "data/checkin-state.json";
const originalPath = "data/backups/full-flow-reset-2026-09-12T13-54-59-825Z/checkin-state.json";
const comparisonPath = "data/backups/oq-retest-2026-09-12T13-46-13-521Z/comparison-non-bye.json";
const messagesPath = "data/backups/ap-clock-replay-2026-09-12/wechat-messages.json";
const beforeShared = read(sharedPath);
const current = JSON.parse(beforeShared);
const original = JSON.parse(read(originalPath));
const comparison = JSON.parse(read(comparisonPath));
const messages = JSON.parse(read(messagesPath)).messages;
const directory = fs.mkdtempSync(path.join(root, "data/backups/ap-simulated-replay-"));
process.env.PAPP_AP_DOWNLOADS_DIR = path.join(directory, "images");
process.env.PAPP_DATA_DIR = directory;
const { pollLocalOqRound } = require("./local-server.js");
const readonlyOperations = new Set(["oq-poll", "stage-status", "overall-standings"]);
const executable = path.join(root, "bin/Windows/papp_GB.exe");
const localTime = milliseconds => new Date(milliseconds + 8 * 60 * 60_000).toISOString().replace("T", " ").replace(/Z$/, " +08:00");
const relays = messages.filter(message => logic.isWechatRelayTemplateContent(message.content)).sort((a, b) => a.createTime - b.createTime);
assert.equal(relays.length, 2);
let clock = relays[0].createTime * 1000;
let state = clone(current);
const report = {
  mode: "历史时钟模拟；不执行真实 PAPP 比分写入或持久化读回",
  inputs: { currentState: sharedPath, originalState: originalPath, comparison: comparisonPath, messages: messagesPath },
  frozenCurrentRevision: clone(current.localSync),
  outputDirectory: directory,
  realComponents: ["ApCoordinator", "ApCheckin", "ApTournament", "tournament-adapter", "PAPP C oq-poll/stage-status/overall-standings", "内置 PNG renderer"],
  simulatedComponents: ["/api/state 为内存状态副本", "write-score-batch/read-score-batch 为内存批次替身，无 C 调用", "EG 仅模拟后台排队，不启动分析引擎"],
  availabilityRule: "棋谱可用时刻保守设为 OQ created + position.moves 中 t 的总和；这不是已知的真实结束时间。模拟进入阶段可能晚于档案开赛时间，档案四轮开赛时间原样保留。",
  timeline: [], cOperations: [], simulatedBatches: [], refereeActions: [], images: [], queries: { relay: 0, checkin: 0 },
};
const log = (event, details = {}) => report.timeline.push({ simulatedAt: localTime(clock), event, ...details });
const accountKey = player => String(player.account || "").toLowerCase();
const originalPlayer = player => original.players.find(candidate => accountKey(candidate) === accountKey(player));
const immutableRounds = rounds => rounds.map(round => ({ round: round.round, roundStartAt: round.roundStartAt,
  pairings: round.pairings.map(pairing => ({ id: pairing.id, table: pairing.table, black: pairing.black, white: pairing.white,
    blackAccount: pairing.blackAccount, whiteAccount: pairing.whiteAccount, papp: pairing.metadata?.papp })) }));
const originalInvariants = immutableRounds(original.scoreHelper.rounds);
const originalByes = original.scoreHelper.rounds.flatMap(round => round.pairings.filter(pairing => pairing.status === "bye"));
const gameFixtures = original.scoreHelper.rounds.flatMap(round => round.pairings.filter(pairing => pairing.status !== "bye").map(pairing => {
  const game = pairing.oqAutoAudit.game;
  assert.ok(game.detail.position.moves.length, `missing historical detail ${pairing.id}`);
  const elapsedMs = game.detail.position.moves.reduce((sum, move) => sum + Math.max(0, Number(move.t) || 0), 0);
  return { round: round.round, pairingId: pairing.id, availableAt: Date.parse(game.createdAt) + elapsedMs,
    game: { id: pairing.oqGameId, created: game.createdAt, black_name: game.blackName, white_name: game.whiteName,
      status: game.status, detail: clone(game.detail) } };
}));

state.step = "checkin";
state.ap = { enabled: false, sessionId: "historical-clock-replay" };
state.players = logic.parseImportTextsDetailed("", relays[0].content).players.map(player => {
  const archived = originalPlayer(player);
  assert.ok(archived, `cannot resolve historical relay identity ${player.displayName}`);
  return { ...clone(archived), checkedIn: false, checkedInAt: null };
});
state.nextPlayerId = Math.max(...state.players.map(player => Number(player.id))) + 1;
state.relayText = relays[0].content;
state.wechatRelaySync = { enabled: false, ready: true, syncModeVersion: 3, lastProcessedMessageId: "", lastProcessedCreateTime: 0 };
state.wechatAutoCheckin = logic.createDefaultWechatAutoCheckin();
state.scoreHelper = clone(original.scoreHelper);
state.scoreHelper.activeRound = 1;
state.scoreHelper.pappWorkfileId = "historical-replay-no-real-workfile";
state.standingsSnapshots = [];
state.playoffRegistration = clone(original.playoffRegistration);
state.plannedWithdrawals = [];
state.egAnalysis = {};
for (const round of state.scoreHelper.rounds) {
  round.pending = []; round.manualPending = []; round.completed = [];
  for (const pairing of round.pairings) {
    if (pairing.status === "bye") continue;
    pairing.blackScore = null; pairing.whiteScore = null; pairing.status = "imported";
    pairing.pappReadbackAt = ""; pairing.completedAt = null; pairing.lastEditedBy = "";
    pairing.oqAutoAudit = null; pairing.resultText = ""; pairing.reason = "";
    pairing.metadata = { papp: clone(pairing.metadata.papp) };
  }
}
assert.deepEqual(state.mapping, current.mapping, "initial current mapping is copied exactly");
assert.deepEqual(immutableRounds(state.scoreHelper.rounds), originalInvariants);
log("导入18:05:53原始接龙", { initialPlayers: state.players.map(player => ({ id: player.id, name: player.displayName })),
  currentRosterMissingHistoricalPlayers: original.players.filter(player => !current.players.some(candidate => accountKey(candidate) === accountKey(player))).map(player => player.displayName),
  actualScoresCleared: 12, byesPreserved: 4, params: clone(state.eventSchedule) });
const batches = new Map();
fs.writeFileSync(path.join(directory, "frozen-current-input.json"), beforeShared, "utf8");

async function invokeReadonlyC(payload) {
  assert.ok(readonlyOperations.has(payload.operation), `FORBIDDEN C OPERATION ${payload.operation}`);
  report.cOperations.push({ simulatedAt: localTime(clock), operation: payload.operation, round: payload.round });
  const env = { ...process.env };
  delete env.PAPP_TOURNAMENT_WORKFILE;
  delete env.PAPP_TOURNAMENT_WORKFILES_DIR;
  const child = spawnSync(executable, ["--tournament-json"], { cwd: directory, env,
    input: JSON.stringify(payload), encoding: "utf8", windowsHide: true });
  assert.ifError(child.error);
  const value = JSON.parse(child.stdout);
  assert.equal(value.ok, true, value.message || JSON.stringify(value));
  return value;
}

async function request(url, body) {
  if (url === "/api/state") {
    if (body) {
      assert.equal(body.operation, "ap-patch");
      state = mergeChanges(body.baseState, body.state, state);
    }
    return { ok: true, state: clone(state) };
  }
  if (url.startsWith("/api/wechat-chat-messages?")) {
    const query = new URL(url, "http://replay.invalid").searchParams;
    const relay = query.has("relayOnly");
    report.queries[relay ? "relay" : "checkin"]++;
    const rows = messages.filter(message => message.createTime * 1000 <= clock &&
      message.createTime >= Number(query.get("startTime")) && message.createTime <= Number(query.get("endTime")) &&
      (!relay || logic.isWechatRelayTemplateContent(message.content))).sort((a, b) => b.createTime - a.createTime);
    return { ok: true, messages: rows.slice(Number(query.get("offset")), Number(query.get("offset")) + Number(query.get("limit"))) };
  }
  if (url === "/api/papp/oq/poll") {
    const gamesByAccount = {};
    for (const fixture of gameFixtures.filter(item => item.availableAt <= clock)) {
      (gamesByAccount[fixture.game.black_name] ||= []).push(fixture.game);
    }
    return pollLocalOqRound({ ...body, oqPollResult: { gamesByAccount } }, { dataDir: directory,
      invokePappC: invokeReadonlyC, fetchImpl: async () => { throw new Error("Historical replay forbids network calls"); } });
  }
  if (url === "/api/papp/eg/start") return { ok: true, analysis: { running: true, simulated: true } };
  assert.equal(url, "/api/papp/tournament", `Unexpected endpoint ${url}`);
  if (body.operation === "write-score-batch") {
    for (const pairing of body.pairings) {
      const expected = comparison.find(item => item.pairingId === pairing.id);
      assert.ok(expected);
      assert.deepEqual([pairing.blackScore, pairing.whiteScore], [expected.blackScore, expected.whiteScore]);
    }
    const previous = batches.get(body.batchId);
    if (previous) assert.deepEqual(previous, body.pairings);
    else batches.set(body.batchId, clone(body.pairings));
    report.simulatedBatches.push({ simulatedAt: localTime(clock), operation: body.operation, round: body.round, batchId: body.batchId, pairingIds: body.pairings.map(pairing => pairing.id), realCWrite: false });
    log("模拟整轮比分写入边界（未执行C写入）", { round: body.round, games: body.pairings.length });
    return { ok: true, source: "papp-c", accepted: true, batchId: body.batchId, idempotent: Boolean(previous), replayTestDouble: true };
  }
  if (body.operation === "read-score-batch") {
    const stored = batches.get(body.batchId);
    assert.ok(stored, "mock read before mock write");
    report.simulatedBatches.push({ simulatedAt: localTime(clock), operation: body.operation, round: body.round, batchId: body.batchId, realCReadback: false });
    return { ok: true, source: "papp-c", verified: true, batchId: body.batchId,
      pairings: stored.map(pairing => ({ ...clone(pairing), status: "completed" })), replayTestDouble: true };
  }
  return invokeReadonlyC(body);
}

const coordinator = new ApCoordinator({ request, checkin, tournament, now: () => clock,
  exportImage: async (kind, payload, key) => {
    const result = await imageExporter.exportImage(kind, payload, key);
    report.images.push({ simulatedAt: localTime(clock), kind, round: payload.target.round, file: path.relative(directory, result.file) });
    log("生成PNG", { kind, round: payload.target.round, file: path.relative(directory, result.file) });
  } });

async function advance(time, label) {
  assert.ok(time >= clock, "simulated clock cannot go backwards");
  clock = time;
  const previousPlayers = clone(state.players);
  const previousMapping = clone(state.mapping.rows);
  const previousStep = state.step;
  const previousRound = state.scoreHelper.activeRound;
  const previousStatus = state.ap.status;
  await coordinator.tick();
  if (state.ap.status === "error") throw new Error(state.ap.message);
  assert.deepEqual(immutableRounds(state.scoreHelper.rounds), originalInvariants, "historical pairings or time changed");
  assert.deepEqual(state.scoreHelper.rounds.flatMap(round => round.pairings.filter(pairing => pairing.status === "bye")), originalByes);
  const added = state.players.filter(player => !previousPlayers.some(candidate => candidate.id === player.id));
  const checked = state.players.filter(player => player.checkedIn && !previousPlayers.find(candidate => candidate.id === player.id)?.checkedIn);
  if (label || added.length || checked.length || previousStatus !== state.ap.status || previousStep !== state.step || previousRound !== state.scoreHelper.activeRound) {
    log(label || "AP状态更新", { status: state.ap.status, step: state.step, activeRound: state.scoreHelper.activeRound,
      message: state.ap.message, addedPlayers: added.map(player => ({ id: player.id, name: player.displayName })), checkedIn: checked.map(player => player.displayName),
      pendingCount: state.wechatAutoCheckin.items.filter(item => item.status === "pending").length, countdown: state.ap.countdown });
  }
  if (JSON.stringify(previousMapping) !== JSON.stringify(state.mapping.rows)) log("现有接龙helper自动更新映射关联", {
    changedRows: state.mapping.rows.filter(row => JSON.stringify(row) !== JSON.stringify(previousMapping.find(old => old.id === row.id))).map(row => ({ id: row.id, name: row.registrationNick, checkinPlayerId: row.checkinPlayerId })) });
}

async function main() {
  await coordinator.control("enable");
  await advance(clock, "开启AP并读取早接龙");
  await advance(relays[1].createTime * 1000, "收到18:30:50更新接龙");
  assert.equal(state.players.length, 9);
  await advance(Date.parse(state.eventSchedule.registrationDeadline), "报名截止，最后刷新并按设置开启签到");
  const graceEnd = Date.parse(state.eventSchedule.checkinDeadline) + 60_000;
  const checkinTimes = messages.filter(message => message.createTime * 1000 > clock && message.createTime * 1000 < graceEnd &&
    logic.classifyWechatAutoCheckinMessage(message)).map(message => message.createTime * 1000 + 10_000).sort((a, b) => a - b);
  for (const time of checkinTimes) await advance(time);
  await advance(graceEnd, "签到截止后1分钟最终扫描");
  const pending = state.wechatAutoCheckin.items.filter(item => item.status === "pending");
  assert.ok(pending.length, "recorded withdrawal messages must block AP");
  assert.equal(state.step, "checkin");
  assert.equal(state.ap.countdown, null);
  report.pendingBlockedStart = clone(pending);
  log("验证pending阻止开赛", { pending: pending.map(item => ({ id: item.id, sender: item.senderGroupNick, content: item.content })) });
  // Explicit referee simulation restores the archived identity allocation and
  // actual participation; this is recorded and is not automatic AP behavior.
  for (const player of state.players) {
    const archived = originalPlayer(player);
    assert.ok(archived);
    if (player.id !== archived.id || player.checkedIn !== archived.checkedIn) {
      report.refereeActions.push({ name: player.displayName, oldId: player.id, archivedId: archived.id,
        previousCheckedIn: player.checkedIn, archivedCheckedIn: archived.checkedIn,
        reason: "按原比赛备份模拟裁判确认历史身份与实际参赛名单" });
    }
    for (const row of state.mapping.rows.filter(row => String(row.checkinPlayerId) === String(player.id))) row.checkinPlayerId = String(archived.id);
    player.id = archived.id; player.checkedIn = archived.checkedIn; player.checkedInAt = archived.checkedInAt;
  }
  for (const item of pending) {
    item.status = "solved"; item.resolvedBy = "human"; item.resolvedAt = graceEnd + 1000;
    report.refereeActions.push({ pendingId: item.id, content: item.content, action: item.pendingKind === "unmapped-checkin"
      ? "模拟裁判确认历史身份与签到，解决未关联映射pending" : "模拟裁判已处理退赛相关pending" });
  }
  const originalMappingRowIds = new Set(current.mapping.rows.map(row => row.id));
  const generatedDuplicates = state.mapping.rows.filter(row => !originalMappingRowIds.has(row.id) &&
    current.mapping.rows.some(existing => existing.registrationNick === row.registrationNick));
  for (const row of generatedDuplicates) report.refereeActions.push({ mappingRowId: row.id,
    action: "模拟裁判合并回放新增的重复映射行，保留当前用户原映射行与原历史选手ID", row: clone(row) });
  state.mapping.rows = state.mapping.rows.filter(row => !generatedDuplicates.includes(row));
  log("显式模拟裁判介入", { actions: report.refereeActions });
  await advance(graceEnd + 1000, "pending解决后启动首轮10秒倒计时");
  assert.equal(state.ap.status, "countdown");
  await advance(clock + 10_000, "首轮倒计时结束");
  for (let round = 1; round <= 4; round++) {
    assert.equal(state.scoreHelper.activeRound, round);
    await advance(clock + 1000, `第${round}轮先查询尚未结束的棋谱`);
    const availableAt = Math.max(clock + 15_000, ...gameFixtures.filter(item => item.round === round).map(item => item.availableAt));
    await advance(availableAt, `第${round}轮历史棋谱全部可用，执行真实C回放`);
    assert.equal(state.ap.status, "countdown", state.ap.message);
    await advance(clock + 10_000, round === 4 ? "最终排名倒计时结束" : `第${round + 1}轮倒计时结束`);
  }
  assert.equal(state.ap.status, "complete");
  for (const expected of comparison) {
    const pairing = state.scoreHelper.rounds[expected.round - 1].pairings.find(item => item.id === expected.pairingId);
    assert.deepEqual([pairing.blackScore, pairing.whiteScore], [expected.blackScore, expected.whiteScore]);
    assert.equal(pairing.status, "completed");
  }
  assert.equal(report.simulatedBatches.filter(batch => batch.operation === "write-score-batch").length, 4);
  assert.equal(report.images.length, 9);
  const observedShared = read(sharedPath);
  report.sharedStateObservation = { unchangedDuringReplay: observedShared === beforeShared,
    afterRevision: JSON.parse(observedShared).localSync,
    note: "回放只对冻结副本操作，未调用真实/api/state；用户可以同时修改真实状态，回放不会覆盖或回滚这些修改。" };
  report.invariants = { originalPairingsPreserved: 16, originalStartTimesPreserved: 4, byesPreserved: "4 × 40–24",
    actualCReplayScoresMatchingBaseline: 12, actualCMutationOperations: 0, actualCScorePersistenceReads: 0,
    simulatedScoreWriteBatches: 4, actualSharedStateWrites: 0, currentSharedStateTextUnchanged: observedShared === beforeShared, status: state.ap.status };
  report.finalStandings = state.standingsSnapshots.find(snapshot => snapshot.kind === "overall");
  report.simulatedFinish = localTime(clock);
  fs.writeFileSync(path.join(directory, "final-simulated-state.json"), JSON.stringify(state, null, 2), "utf8");
  fs.writeFileSync(path.join(directory, "report.json"), JSON.stringify(report, null, 2), "utf8");
  const lines = ["# AP 历史比赛模拟回放", "", report.mode, "", "真实执行：历史聊天解析、AP流程、OQ棋谱C回放、C阶段验证、C最终排名及PNG生成。",
    "比分写入/读回与共享状态保存均使用内存替身；本报告不声称验证了真实PAPP持久化写入。EG仅模拟排队。", "", report.availabilityRule, "",
    "## 核验", "", ...Object.entries(report.invariants).map(([key, value]) => `- ${key}: ${value}`), "", "## 裁判模拟操作", "",
    ...report.refereeActions.map(action => `- ${JSON.stringify(action)}`), "", "## 时间线", "", ...report.timeline.map(event => `- ${event.simulatedAt} — ${event.event}：${JSON.stringify(event)}`),
    "", "## 导出图片", "", ...report.images.map(image => `- [${image.kind} 第${image.round}轮](${image.file.replace(/\\/g, "/")})`)];
  fs.writeFileSync(path.join(directory, "report.md"), lines.join("\n") + "\n", "utf8");
  console.log(JSON.stringify({ directory, ...report.invariants, images: report.images.length, refereeActions: report.refereeActions.length, simulatedFinish: report.simulatedFinish }, null, 2));
}

main().catch(error => {
  report.error = String(error.stack || error);
  fs.writeFileSync(path.join(directory, "failure-report.json"), JSON.stringify(report, null, 2), "utf8");
  console.error(JSON.stringify({ directory, error: report.error }, null, 2));
  process.exitCode = 1;
});
