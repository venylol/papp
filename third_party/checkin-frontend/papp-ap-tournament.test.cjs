"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {tick, enterNext, mergeOq, adapterFor} = require("./papp-ap-tournament.js");
const ok = fields => ({ok: true, source: "papp-c", ...fields});
function fixture() {
  return {step: "score-helper", players: [{id: "a", displayName: "甲", checkedIn: true}, {id: "b", displayName: "乙", checkedIn: true}],
    mapping: {rows: []}, tournamentParameters: {hasSemifinalAndFinal: false},
    scoreHelper: {activeRound: 1, preliminaryRoundCount: 2, roundCount: 2, pappWorkfileId: "test",
      rounds: [{round: 1, roundStartAt: "2026-09-12T21:06:34+08:00", pending: [], manualPending: [],
        pairings: [{id: "r1t1", table: 1, black: "甲", white: "乙", blackId: "a", whiteId: "b", status: "imported", source: "papp-c", blackScore: null, whiteScore: null},
          {id: "bye", status: "bye", blackScore: 40, whiteScore: 24}]},
      {round: 2, pairings: [], roundStartAt: ""}]}, plannedWithdrawals: []};
}
function options(extra = {}) {
  const calls = [];
  return {nowMs: 50000, calls, exportImage: async kind => calls.push(kind),
    adapter: {
      pollOqRound: async () => ok({ready: []}),
      startEgAnalysis: async () => ({ok: true, analysis: {gameCount: 1}}),
      getStageStatus: async () => ok({canAdvance: true}),
      writeScoreBatch: async ctx => {calls.push("write"); return ok({});},
      readScoreBatch: async ctx => {calls.push("read"); return ok({pairings: ctx.pairings.map(p => ({...p, status: "completed"}))});},
    }, ...extra};
}
test("complete round writes once, confirms before PNG and next; retains original start and BYE", async () => {
  const state = fixture();
  const opt = options();
  opt.adapter.pollOqRound = async () => ok({ready: [{...state.scoreHelper.rounds[0].pairings[0], blackScore: 42, whiteScore: 22, status: "ready"}]});
  const result = await tick(state, opt);
  assert.equal(result.error, undefined);
  assert.deepEqual(result.next, {stage: "preliminary", round: 2});
  assert.deepEqual(opt.calls, ["pairings", "write", "read", "scores"]);
  assert.equal(result.state.scoreHelper.rounds[0].roundStartAt, state.scoreHelper.rounds[0].roundStartAt);
  assert.deepEqual(result.state.scoreHelper.rounds[0].pairings[1], state.scoreHelper.rounds[0].pairings[1]);
  const again = await tick(result.state, {...opt, nowMs: 51000});
  assert.equal(again.error, undefined);
  assert.deepEqual(opt.calls, ["pairings", "write", "read", "scores"]);
  assert.equal(state.scoreHelper.rounds[0].pairings[0].status, "imported");
});
test("readback failure preserves fetched scores and blocks PNG/advance", async () => {
  const state = fixture(); const opt = options();
  opt.adapter.pollOqRound = async () => ok({ready: [{...state.scoreHelper.rounds[0].pairings[0], blackScore: 42, whiteScore: 22, status: "ready"}]});
  opt.adapter.readScoreBatch = async () => ok({pairings: []});
  const result = await tick(state, opt);
  assert.match(result.error, /读回/);
  assert.equal(result.next, undefined);
  assert.equal(result.state.scoreHelper.rounds[0].pairings[0].blackScore, 42);
  assert.deepEqual(opt.calls, ["pairings", "write"]);
});
test("pending prevents batch; error pause keeps OQ and EG but prohibits commit", async () => {
  const state = fixture(); const opt = options();
  state.scoreHelper.rounds[0].pairings[0].status = "ready";
  state.scoreHelper.rounds[0].manualPending = [{id: "manual"}];
  assert.equal((await tick(state, opt)).next, undefined);
  assert.deepEqual(opt.calls, ["pairings"]);
  state.scoreHelper.rounds[0].manualPending = [];
  opt.calls.length = 0;
  const result = await tick(state, {...opt, allowCommit: false});
  assert.equal(result.next, undefined);
  assert.equal(result.state.egAnalysis.gameCount, 1);
  assert.deepEqual(opt.calls, []);
});
test("accounts, replay and scores merge onto the same live pairing", () => {
  const state = fixture();
  const p = state.scoreHelper.rounds[0].pairings[0];
  mergeOq(state, {stage: "preliminary", round: 1}, ok({
    pairingAccountUpdates: [{round: 1, pairingId: p.id, blackAccount: "alice", whiteAccount: "bob"}],
    ready: [{id: p.id, status: "ready", blackScore: 42, whiteScore: 22}],
    oqGameRecords: [{round: 1, pairingId: p.id, oqGameId: "game", gameRecord: {moves: "f5"}}],
  }));
  assert.equal(p, state.scoreHelper.rounds[0].pairings[0]);
  assert.equal(p.blackAccount, "alice"); assert.equal(p.blackScore, 42); assert.equal(p.oqGameId, "game");
});
test("existing pairings/start stay unchanged and late withdrawal moves forward", async () => {
  const state = fixture(); const opt = options();
  state.plannedWithdrawals = [{playerId: "a", round: 1}];
  const result = await enterNext(state, {stage: "preliminary", round: 1}, opt);
  assert.equal(result.error, undefined);
  assert.deepEqual(result.state.scoreHelper.rounds, state.scoreHelper.rounds);
  assert.equal(result.state.plannedWithdrawals[0].round, 2);
  assert.equal(result.state.players[0].checkedIn, true);
});
test("due withdrawal reaches C roster before fresh pairing generation", async () => {
  const state = fixture(); const opt = options();
  state.plannedWithdrawals = [{playerId: "a", round: 2}];
  opt.adapter.importPairings = async ctx => {
    assert.deepEqual(ctx.checkedInPlayers.map(p => p.id), ["b"]);
    return ok({pairings: [{id: "new", status: "bye", blackScore: 40, whiteScore: 24}]});
  };
  const result = await enterNext(state, {stage: "preliminary", round: 2}, opt);
  assert.equal(result.error, undefined); assert.deepEqual(result.state.plannedWithdrawals, []);
  assert.equal(result.state.scoreHelper.rounds[0].roundStartAt, state.scoreHelper.rounds[0].roundStartAt);
});
test("real adapter Node bridge sends C operation and mapped roster", async () => {
  const state = fixture(); let received;
  const adapter = adapterFor(async (url, body) => { received = {url, body}; return ok({roundCount: 2}); });
  const result = await adapter.getRoundCount({state, checkedInPlayers: state.players});
  assert.equal(result.roundCount, 2);
  assert.equal(received.url, "/api/papp/tournament");
  assert.equal(received.body.operation, "round-count"); assert.equal(received.body.playerCount, 2);
});
test("default OQ cadence is 15 seconds", async () => {
  const state = fixture(); const opt = options(); let polls = 0;
  opt.adapter.pollOqRound = async () => {polls++; return ok({ready: []});};
  let result = await tick(state, opt);
  result = await tick(result.state, {...opt, nowMs: 64999}); assert.equal(polls, 1);
  await tick(result.state, {...opt, nowMs: 65000}); assert.equal(polls, 2);
});
test("concurrent human edit guard pauses before C write while retaining OQ result", async () => {
  const state = fixture(); const opt = options();
  opt.adapter.pollOqRound = async () => ok({ready: [{id: "r1t1", status: "ready", blackScore: 42, whiteScore: 22}]});
  opt.assertCurrent = async () => {throw new Error("人工状态已更新，请恢复 AP 后重试");};
  const result = await tick(state, opt);
  assert.match(result.error, /人工状态/);
  assert.equal(result.state.scoreHelper.rounds[0].pairings[0].blackScore, 42);
  assert.ok(!opt.calls.includes("write")); assert.equal(result.next, undefined);
});
test("existing readback markers require C stage confirmation before score PNG and advance", async () => {
  const state = fixture(); const opt = options();
  Object.assign(state.scoreHelper.rounds[0].pairings[0], {status: "completed", pappReadbackAt: "old", blackScore: 42, whiteScore: 22});
  opt.adapter.getStageStatus = async () => ok({canAdvance: false});
  const result = await tick(state, opt);
  assert.match(result.error, /PAPP C/); assert.equal(result.next, undefined);
  assert.ok(!opt.calls.includes("scores"));
});
test("manual pending protects its pairing from OQ and resolved audit rows do not block", async () => {
  const state = fixture(); const opt = options();
  state.scoreHelper.rounds[0].manualPending = [{pairingId: "r1t1", pendingKind: "oq-auto", manualPendingAt: 1}];
  const ready = {id: "r1t1", status: "ready", blackScore: 42, whiteScore: 22};
  opt.adapter.pollOqRound = async () => ok({ready: [ready]});
  const result = await tick(state, opt);
  assert.equal(result.state.scoreHelper.rounds[0].pairings[0].status, "imported");
  assert.equal(result.next, undefined);
  state.scoreHelper.rounds[0].manualPending = [];
  state.scoreHelper.rounds[0].pending = [{pendingKind: "user-pending", pairingId: "r1t1", resolvedByReferee: true}];
  const resolved = await tick(state, opt);
  assert.ok(resolved.next); assert.equal(resolved.error, undefined);
});

// All tests below use injected adapters only. They neither launch PAPP C nor write a workfile.
const simulatedStart = new Date(2020, 2, 4, 12, 34, 56).getTime();
const simulatedStartText = "2020-03-04 12:34:56";
const stagesWithStart = [
  {stage: "preliminary", round: 1}, {stage: "preliminary", round: 2},
  {stage: "semifinal", round: 3}, {stage: "placement", round: 4},
];
function stageStart(state, target) {
  return target.stage === "preliminary" ? state.scoreHelper.rounds[target.round - 1].roundStartAt
    : state.playoffRegistration[target.stage + "RoundStartAt"];
}
function stageFixture(target, existingStart = "") {
  const state = fixture();
  const pairing = {...state.scoreHelper.rounds[0].pairings[0], id: `target-${target.stage}-${target.round}`};
  if (target.stage === "preliminary") {
    state.scoreHelper.rounds[target.round - 1].roundStartAt = existingStart;
    state.scoreHelper.rounds[target.round - 1].pairings = [];
  } else {
    state.playoffRegistration = {activeStage: target.stage, semifinalPairings: [], placementPairings: [],
      semifinalRoundStartAt: "", placementRoundStartAt: "", [target.stage + "RoundStartAt"]: existingStart};
  }
  const opt = options({nowMs: simulatedStart});
  opt.adapter.getRoundCount = async () => ok({roundCount: 2});
  opt.adapter.getPreliminaryStandings = async () => ok({standings: [1, 2, 3, 4].map(id => ({id, rank: id}))});
  opt.adapter.importPairings = async () => ok({pairings: [pairing]});
  return {state, opt, pairing};
}
for (const target of stagesWithStart) {
  test(`historical injected clock sets new ${target.stage} round ${target.round} start`, async () => {
    const {state, opt} = stageFixture(target);
    const result = await enterNext(state, target, opt);
    assert.equal(result.error, undefined);
    assert.equal(stageStart(result.state, target), simulatedStartText);
    assert.equal(stageStart(state, target), "", "input snapshot must remain untouched");
  });
  test(`existing ${target.stage} round ${target.round} start is retained under a different clock`, async () => {
    const originalStart = "2026-09-12 21:06:34";
    const {state, opt, pairing} = stageFixture(target, originalStart);
    if (target.stage === "preliminary") state.scoreHelper.rounds[target.round - 1].pairings = [pairing];
    else state.playoffRegistration[target.stage + "Pairings"] = [pairing];
    const original = structuredClone(state);
    opt.adapter.importPairings = async () => {throw new Error("existing pairing must not be regenerated");};
    const result = await enterNext(state, target, opt);
    assert.equal(result.error, undefined);
    assert.equal(stageStart(result.state, target), originalStart);
    assert.deepEqual(result.state.scoreHelper.rounds, original.scoreHelper.rounds);
    if (target.stage !== "preliminary") assert.deepEqual(
      result.state.playoffRegistration[target.stage + "Pairings"], original.playoffRegistration[target.stage + "Pairings"]);
  });
}
test("skip-semifinal entry exports the C preliminary ranking before returning the direct final pairing", async () => {
  const target = {stage: "placement", round: 3};
  const {state, opt, pairing} = stageFixture(target);
  state.tournamentParameters = {hasSemifinalAndFinal: true, skipSemifinal: true};
  let rankingRead = false;
  let pairingImportedAfterRanking = false;
  opt.adapter.getPreliminaryStandings = async () => {
    rankingRead = true;
    return ok({operation: "preliminary-standings", standings: [1, 2, 3, 4].map(id => ({id, rank: id}))});
  };
  opt.adapter.importPairings = async ctx => {
    pairingImportedAfterRanking = rankingRead;
    assert.equal(ctx.stage, "placement");
    assert.equal(ctx.round, 3);
    return ok({pairings: [pairing]});
  };
  const result = await enterNext(state, target, opt);
  assert.equal(result.error, undefined);
  assert.equal(pairingImportedAfterRanking, true);
  assert.equal(result.state.playoffRegistration.activeStage, "placement");
  assert.deepEqual(result.state.playoffRegistration.placementPairings.map(row => row.id), [pairing.id]);
  assert.ok(result.state.standingsSnapshots.some(snapshot => snapshot.kind === "preliminary" && snapshot.source === "papp-c"));
  assert.deepEqual(opt.calls.filter(call => typeof call === "string"), ["preliminary", "pairings"]);
});
for (const target of [stagesWithStart[0], stagesWithStart[2], stagesWithStart[3]]) {
  test(`manual early ${target.stage} entry fills missing start before OQ using simulated clock`, async () => {
    const {state, opt, pairing} = stageFixture(target);
    if (target.stage === "preliminary") {
      state.step = "score-helper"; state.scoreHelper.activeRound = target.round;
      state.scoreHelper.rounds[target.round - 1].pairings = [pairing];
    } else {
      state.step = "final-registration";
      state.playoffRegistration[target.stage + "Pairings"] = [pairing];
    }
    let queriedStart;
    opt.adapter.pollOqRound = async ctx => {queriedStart = ctx.roundStartAt; return ok({ready: []});};
    const result = await tick(state, opt);
    assert.equal(result.error, undefined);
    assert.equal(queriedStart, simulatedStartText);
    assert.equal(stageStart(result.state, target), simulatedStartText);
    assert.ok(!opt.calls.includes("write"));
  });
}
for (const checkedInCount of [7, 8]) {
  test(`first round auto playoff choice uses ${checkedInCount} checked-in players without browser`, async () => {
    const {state, opt} = stageFixture(stagesWithStart[0]);
    state.players = Array.from({length: 10}, (_, i) => ({id: i + 1, displayName: `P${i + 1}`, checkedIn: i < checkedInCount}));
    state.tournamentParameters = {semifinalAndFinalMode: "auto", hasSemifinalAndFinal: checkedInCount !== 8};
    let rosterCount;
    let playoffChoice;
    opt.adapter.getRoundCount = async ctx => {
      rosterCount = ctx.checkedInPlayers.length;
      playoffChoice = ctx.state.tournamentParameters.hasSemifinalAndFinal;
      return ok({roundCount: 4});
    };
    const result = await enterNext(state, stagesWithStart[0], opt);
    assert.equal(result.error, undefined);
    assert.equal(rosterCount, checkedInCount);
    assert.equal(playoffChoice, checkedInCount === 8);
    assert.equal(result.state.tournamentParameters.hasSemifinalAndFinal, checkedInCount === 8);
    assert.equal(result.state.scoreHelper.rounds.length, 4);
  });
}
test("first-round auto parameter refresh keeps all four original rounds and times intact", async () => {
  const state = fixture(); const opt = options({nowMs: simulatedStart});
  state.players = Array.from({length: 8}, (_, i) => ({id: i + 1, checkedIn: true}));
  state.tournamentParameters = {semifinalAndFinalMode: "auto", hasSemifinalAndFinal: false};
  const times = ["21:06:34", "21:12:43", "21:22:11", "21:32:35"];
  state.scoreHelper.preliminaryRoundCount = state.scoreHelper.roundCount = 4;
  state.scoreHelper.rounds = times.map((time, i) => ({round: i + 1, roundStartAt: `2026-09-12 ${time}`,
    pairings: [{id: `original-${i + 1}`, status: "imported", blackScore: null, whiteScore: null}]}));
  const originalRounds = structuredClone(state.scoreHelper.rounds);
  opt.adapter.getRoundCount = async () => {throw new Error("original rounds must not be recalculated");};
  opt.adapter.importPairings = async () => {throw new Error("original pairings must not be regenerated");};
  const result = await enterNext(state, {stage: "preliminary", round: 1}, opt);
  assert.equal(result.error, undefined);
  assert.equal(result.state.tournamentParameters.hasSemifinalAndFinal, true);
  assert.deepEqual(result.state.scoreHelper.rounds, originalRounds);
});
