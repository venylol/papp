"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const { shouldRetryFailedEgAnalysis } = require("./app.js");

const pairing = {
  id: "p1", table: 1, source: "papp-c", oqGameId: "g1",
  black: "Alice", white: "Bob", blackAccount: "alice", whiteAccount: "bob",
  metadata: { gameRecord: { gameId: "g1", moves: ["f5"] } },
};
const saved = { scoreHelper: { rounds: [{ pairings: [pairing] }] } };

test("OQ can retry a failed EG job from stored transcripts without receiving a new transcript", () => {
  const failed = { running: false, pending: false, error: "Egaroucid output parse failed" };
  assert.equal(shouldRetryFailedEgAnalysis(failed, saved), true);
  assert.equal(shouldRetryFailedEgAnalysis(failed, {
    playoffRegistration: { semifinalPairings: [pairing] },
  }), true);
  assert.equal(shouldRetryFailedEgAnalysis(failed, {}), false);
  assert.equal(shouldRetryFailedEgAnalysis(failed, {
    scoreHelper: { rounds: [{ pairings: [{ ...pairing, metadata: {} }] }] },
  }), false);
});

test("stored transcripts do not retry completed, stopped, or running EG jobs", () => {
  for (const status of [
    null,
    { running: false, pending: false, error: "", completedRecords: 1, recordsTotal: 1 },
    { running: false, pending: false, error: "", completedRecords: 0, recordsTotal: 1 },
    { running: true, error: "previous error" },
    { pending: true, error: "previous error" },
    { status: "pending", error: "previous error" },
  ]) assert.equal(shouldRetryFailedEgAnalysis(status, saved), false);
});

test("EG response reaches loss display only for the matching game, pairing, stage, and account", () => {
  // Expose private presentation functions in this test's isolated module only.
  const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8")
    .replace("module.exports = {", "module.exports = { applyEgAnalysisResult, renderEgLossTag,");
  const context = { module: { exports: {} }, console, require };
  vm.runInNewContext(source, context, { filename: "app.js" });
  const app = context.module.exports;
  app.applyEgAnalysisResult({ analysis: { pairingLossByRound: { "1": { "1": {
    stage: "preliminary", pairingId: "p1", gameId: "g1",
    blackAccount: "alice", whiteAccount: "bob",
    players: [{ account: "alice", ftdSide: "black", totalLoss: 7, averageLoss: 0.5 }],
  } } } } }, { render: false, persist: false });
  assert.match(app.renderEgLossTag(1, "preliminary", pairing, "black"), /子损 7/);
  assert.equal(app.renderEgLossTag(1, "semifinal", pairing, "black"), "");
  assert.equal(app.renderEgLossTag(2, "preliminary", pairing, "black"), "");
  for (const change of [{ id: "p2" }, { oqGameId: "g2" }, { blackAccount: "other" }]) {
    assert.equal(app.renderEgLossTag(1, "preliminary", { ...pairing, ...change }, "black"), "");
  }
});
