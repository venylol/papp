"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const ap = require("./papp-ap-tournament.js");
const { sanitizeScoreHelper, sanitizePlayoffRegistration, sanitizeStandingsSnapshots } = require("./app.js");
const executable = process.env.PAPP_C_EXE || path.resolve(__dirname, "../../bin/Windows/papp_GB.exe");

function fixture(playoffs) {
  return { step: "checkin", ap: {}, ui: { oqPollSeconds: 15 },
    players: ["Alpha", "Bravo", "Charlie", "Delta"].map((displayName, i) => ({
      id: i + 1, displayName, account: `account${i + 1}`, checkedIn: true, platform: "oq",
    })), mapping: { rows: [] }, plannedWithdrawals: [],
    tournamentParameters: { hasSemifinalAndFinal: playoffs, brightwellConstant: 0 },
    scoreHelper: { pappWorkfileId: "ap-isolated-integration", preliminaryRoundCount: 2,
      roundCount: 2, roundCountSource: "manual", activeRound: 1,
      rounds: [1, 2].map(round => ({ round, stage: "preliminary", pairings: [], pending: [], manualPending: [], roundStartAt: "" })) },
    playoffRegistration: { preliminaryRoundCount: 2, activeStage: "semifinal", semifinalPairings: [], placementPairings: [] },
  };
}

function harness() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "papp-ap-c-integration-"));
  const calls = [];
  const exports = [];
  const options = { nowMs: new Date("2026-09-12T21:00:00+08:00").getTime(),
    request: async (url, payload) => {
      if (url === "/api/papp/oq/poll") return { ok: true, source: "papp-c", ready: [], pending: [] };
      if (url === "/api/papp/eg/start") return { ok: true, analysis: { running: true } };
      assert.equal(url, "/api/papp/tournament");
      calls.push(structuredClone(payload));
      const child = spawnSync(executable, ["--tournament-json"], {
        input: JSON.stringify(payload), encoding: "utf8", windowsHide: true,
        env: { ...process.env, PAPP_TOURNAMENT_WORKFILE: path.join(directory, "score-workfile.txt") },
      });
      assert.ifError(child.error);
      const result = JSON.parse(child.stdout);
      return result;
    },
    exportImage: async (kind, payload, key) => { exports.push({ kind, key, payload: structuredClone(payload) }); },
  };
  return { options, calls, exports, directory };
}

function roundData(state, target) {
  return target.stage === "preliminary" ? state.scoreHelper.rounds[target.round - 1]
    : { pairings: state.playoffRegistration[`${target.stage}Pairings`] };
}

async function completeRound(state, target, harnessValue) {
  for (const pairing of roundData(state, target).pairings) {
    if (pairing.status === "bye") continue;
    Object.assign(pairing, { blackScore: 40, whiteScore: 24, status: "ready", lastEditedBy: "human" });
  }
  harnessValue.options.nowMs += 60_000;
  const result = await ap.tick(state, harnessValue.options);
  assert.equal(result.error, undefined, result.error);
  assert.ok(result.next, `no next target for ${target.stage} ${target.round}`);
  assert.ok(roundData(result.state, target).pairings.every(p => p.status === "completed" && p.pappReadbackAt));
  const writes = harnessValue.calls.filter(call => call.operation === "write-score-batch" && call.stage === target.stage && call.round === target.round);
  assert.equal(writes.length, 1, "one atomic score batch per round");
  assert.equal(writes[0].pairings.length, 2);
  return result;
}

for (const playoffs of [false, true]) {
  test(`AP adapter executes actual Windows C through ${playoffs ? "semifinal, placement and overall" : "preliminary and overall"}`, async () => {
    const h = harness();
    let state = fixture(playoffs);
    let target = { stage: "preliminary", round: 1 };
    while (target.stage !== "overall") {
      const entered = await ap.enterNext(state, target, h.options);
      assert.equal(entered.error, undefined, entered.error);
      state = entered.state;
      assert.equal(roundData(state, target).pairings.length, 2);
      // The frontend state sanitizer must preserve the server-generated start time.
      if (target.stage === "preliminary") {
        const safe = sanitizeScoreHelper(state.scoreHelper);
        assert.ok(safe.rounds[target.round - 1].roundStartAt, "preliminary start survives shared state sanitization");
      } else {
        const safe = sanitizePlayoffRegistration(state.playoffRegistration);
        assert.ok(safe[`${target.stage}RoundStartAt`], "playoff start survives shared state sanitization");
      }
      const completed = await completeRound(state, target, h);
      state = completed.state;
      target = completed.next;
    }
    const finished = await ap.enterNext(state, target, h.options);
    assert.equal(finished.error, undefined, finished.error);
    assert.equal(finished.complete, true);
    assert.ok(["score-helper", "final-registration"].includes(finished.state.step));
    const snapshots = sanitizeStandingsSnapshots(finished.state.standingsSnapshots);
    const overall = snapshots.find(item => item.kind === "overall");
    assert.ok(overall, "overall C snapshot survives frontend sanitizer");
    assert.equal(overall.standings.length, 4);
    assert.equal(overall.source, "papp-c");
    assert.equal(h.exports.filter(item => item.kind === "scores").length, playoffs ? 4 : 2);
    assert.equal(h.exports.filter(item => item.kind === "preliminary").length, playoffs ? 1 : 0);
    assert.equal(h.exports.filter(item => item.kind === "overall").length, 1);
    assert.equal(h.calls.filter(call => call.operation === "round-count").length, 1);
  });
}

test("actual C persisted batch is retried idempotently after incomplete readback, without advancing or exporting scores", async () => {
  const h = harness();
  const target = { stage: "preliminary", round: 1 };
  const entered = await ap.enterNext(fixture(false), target, h.options);
  assert.equal(entered.error, undefined);
  const state = entered.state;
  for (const pairing of roundData(state, target).pairings) {
    Object.assign(pairing, { status: "ready", blackScore: 40, whiteScore: 24, lastEditedBy: "human" });
  }
  const request = h.options.request;
  h.options.request = async (url, payload) => {
    const result = await request(url, payload);
    return payload?.operation === "read-score-batch" ? { ...result, pairings: [] } : result;
  };
  const failed = await ap.tick(state, h.options);
  assert.ok(failed.error);
  assert.equal(failed.next, undefined);
  assert.ok(roundData(failed.state, target).pairings.every(pairing => pairing.status === "ready"));
  assert.equal(h.exports.filter(item => item.kind === "scores").length, 0);
  h.options.request = request;
  h.options.nowMs += 1000;
  const retried = await ap.tick(failed.state, h.options);
  assert.equal(retried.error, undefined, retried.error);
  assert.deepEqual(retried.next, { stage: "preliminary", round: 2 });
  const writes = h.calls.filter(call => call.operation === "write-score-batch");
  assert.equal(writes.length, 2);
  assert.equal(writes[0].batchId, writes[1].batchId);
  assert.equal(h.exports.filter(item => item.kind === "scores").length, 1);
});

test("real OQ C replay fills existing game IDs, survives account/transcript merging, and preserves the C bye", async () => {
  const h = harness();
  const initial = fixture(false);
  initial.players.push({ id: 5, displayName: "Echo", account: "account5", checkedIn: true, platform: "oq" });
  const entered = await ap.enterNext(initial, { stage: "preliminary", round: 1 }, h.options);
  assert.equal(entered.error, undefined, entered.error);
  const state = entered.state;
  state.mapping.rows = state.players.map(player => ({ id: `map-${player.id}`, checkinPlayerId: String(player.id),
    registrationNick: player.displayName, oqAccount: `mapped${player.id}` }));
  const pairings = state.scoreHelper.rounds[0].pairings;
  const originalBye = structuredClone(pairings.find(pairing => pairing.status === "bye"));
  assert.ok(originalBye);
  const gamesByAccount = {};
  for (const pairing of pairings.filter(pairing => pairing.status !== "bye")) {
    pairing.oqGameId = `already-fetched-${pairing.id}`;
    pairing.blackScore = null;
    pairing.whiteScore = null;
    const blackAccount = `mapped${pairing.blackId}`;
    const whiteAccount = `mapped${pairing.whiteId}`;
    gamesByAccount[blackAccount] = [{ id: pairing.oqGameId, created: "2026-09-12T21:01:00+08:00",
      black_name: blackAccount, white_name: whiteAccount, black_score: 1, white_score: 63,
      status: "SCORE", detail: { position: { moves: [{ m: "f5" }] } } }];
  }
  // Importing the module does not start its HTTP service. Both all transcript IO
  // and C workfiles are explicitly scoped to this test's retained temp directory.
  const previousDataDir = process.env.PAPP_DATA_DIR;
  process.env.PAPP_DATA_DIR = h.directory;
  const { pollLocalOqRound } = require("./local-server.js");
  if (previousDataDir === undefined) delete process.env.PAPP_DATA_DIR;
  else process.env.PAPP_DATA_DIR = previousDataDir;
  const request = h.options.request;
  let oqResult;
  h.options.request = async (url, payload) => {
    if (url !== "/api/papp/oq/poll") return request(url, payload);
    oqResult = await pollLocalOqRound({ ...payload, oqPollResult: { gamesByAccount } }, {
      dataDir: h.directory, invokePappC: body => request("/api/papp/tournament", body),
      fetchImpl: async () => { throw new Error("Integration fixture must not access the network"); },
    });
    return oqResult;
  };
  h.options.nowMs += 120_000;
  const result = await ap.tick(state, h.options);
  assert.equal(result.error, undefined, result.error);
  assert.deepEqual(result.next, { stage: "preliminary", round: 2 });
  assert.equal(oqResult.source, "papp-c");
  assert.equal(oqResult.ready.length, 2);
  assert.equal(oqResult.oqGameRecords.length, 2);
  const actual = result.state.scoreHelper.rounds[0].pairings;
  assert.deepEqual(actual.find(pairing => pairing.status === "bye"), originalBye);
  for (const pairing of actual.filter(pairing => pairing.status !== "bye")) {
    const replay = oqResult.ready.find(row => row.pairingId === pairing.id || row.id === pairing.id);
    assert.ok(replay);
    assert.deepEqual([pairing.blackScore, pairing.whiteScore], [replay.blackScore, replay.whiteScore]);
    assert.equal(pairing.status, "completed");
    assert.ok(pairing.pappReadbackAt);
    assert.equal(pairing.blackAccount, `mapped${pairing.blackId}`);
    assert.equal(pairing.whiteAccount, `mapped${pairing.whiteId}`);
    assert.equal(pairing.metadata.gameRecord.gameId, pairing.oqGameId);
  }
  const writes = h.calls.filter(call => call.operation === "write-score-batch");
  assert.equal(writes.length, 1);
  assert.equal(writes[0].pairings.length, 2);
  assert.equal(h.exports.filter(item => item.kind === "scores").length, 1);
});
