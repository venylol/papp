"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

function mergeInMemory(saved, result, options) {
  // Exercise the real merge and normalization without a browser or state API.
  const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8")
    .replace("module.exports = {", `module.exports = {
      testMerge(saved, result, options) {
        state = deepClone(saved);
        let persisted;
        renderScoreHelper = () => { ensureScoreHelper(); };
        renderFinalRegistration = () => {};
        scheduleSave = () => { persisted = deepClone(state); };
        const summary = mergeOqPollResult(result.round, result, options);
        return { summary, state: persisted };
      },`);
  const context = { module: { exports: {} }, console, require };
  vm.runInNewContext(source, context, { filename: "app.js" });
  return JSON.parse(JSON.stringify(context.module.exports.testMerge(saved, result, options)));
}

function pairing(id) {
  return {
    id, table: 1, source: "papp-c", status: "imported",
    black: "Alice", white: "Bob", blackAccount: "old-a", whiteAccount: "old-b",
    blackScore: null, whiteScore: null,
    metadata: { papp: { source: "papp-c", blackPlayerId: "1", whitePlayerId: "2" } },
  };
}

for (const stage of ["preliminary", "semifinal"]) {
  test(`OQ ${stage} merge persists scores and transcripts across account updates and normalization`, () => {
    const currentRound = stage === "preliminary" ? 2 : 3;
    const current = pairing("current");
    const saved = {
      scoreHelper: {
        preliminaryRoundCount: 2, roundCount: 2, activeRound: 2,
        rounds: [
          { round: 1, roundStartAt: "2026-09-12 21:00:00", pairings: [pairing("history")] },
          { round: 2, roundStartAt: "2026-09-12 21:15:00", pairings: [current] },
        ],
      },
      playoffRegistration: {
        preliminaryRoundCount: 2, semifinalPairings: [current], placementPairings: [],
      },
    };
    const ready = {
      ...current, status: "ready", blackAccount: "alice", whiteAccount: "bob",
      blackScore: 46, whiteScore: 18, lastEditedBy: "script", resultKind: "oq-auto",
    };
    const result = {
      ok: true, source: "papp-c", stage, round: currentRound, ready: [ready], pending: [],
      pairingAccountUpdates: [
        { stage, round: currentRound, pairingId: "current", blackAccount: "alice", whiteAccount: "bob" },
        { stage: "preliminary", round: 1, pairingId: "history", blackAccount: "alice", whiteAccount: "bob" },
      ],
      oqGameRecords: [
        { stage, round: currentRound, pairingId: "current", oqGameId: "g-current",
          gameRecord: { gameId: "g-current", moves: ["f5"] } },
        { stage: "preliminary", round: 1, pairingId: "history", oqGameId: "g-history",
          gameRecord: { gameId: "g-history", moves: ["d3"] } },
      ],
      historicalTranscriptCount: 1,
    };
    const merged = mergeInMemory(saved, result, { stage });
    const row = stage === "preliminary"
      ? merged.state.scoreHelper.rounds[1].pairings[0]
      : merged.state.playoffRegistration.semifinalPairings[0];
    assert.equal(row.status, "ready");
    assert.equal(row.blackScore, 46);
    assert.equal(row.whiteScore, 18);
    assert.equal(row.blackAccount, "alice");
    assert.equal(row.oqGameId, "g-current");
    assert.equal(row.metadata.gameRecord.gameId, "g-current");
    assert.equal(merged.state.scoreHelper.rounds[0].pairings[0].metadata.gameRecord.gameId, "g-history");
    assert.equal(merged.state.scoreHelper.rounds[0].pairings[0].blackAccount, "alice");
    if (stage === "preliminary") {
      assert.equal(merged.state.scoreHelper.rounds[1].oq.lastOk, true);
      assert.ok(merged.state.scoreHelper.rounds[1].oq.lastPollAt);
    }
    assert.equal(merged.summary.readyCount, 1);
    assert.equal(merged.summary.transcriptCount, 2);
  });
}
