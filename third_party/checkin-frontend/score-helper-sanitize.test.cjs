"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  sanitizeScoreItem,
  sanitizeScorePairing,
  sanitizeScoreRound,
  createDefaultScoreHelper,
  sanitizeScoreHelper,
  isBoardScorePairing,
  isPappReadbackConfirmedPairing,
  isScoreBatchCandidate,
  scorePairingConfirmedByReadback,
  scorePairingScoresMatch,
} = require("./app.js");

test("PAPP workfile identity is unique for new tournaments and stable when restored", () => {
  const first = createDefaultScoreHelper(4);
  const second = createDefaultScoreHelper(4);
  const resumed = sanitizeScoreHelper(first);

  assert.match(first.pappWorkfileId, /^[A-Za-z0-9_-]{1,128}$/);
  assert.notEqual(first.pappWorkfileId, second.pappWorkfileId);
  assert.equal(resumed.pappWorkfileId, first.pappWorkfileId);
  assert.match(sanitizeScoreHelper({ ...first, pappWorkfileId: "../old" }).pappWorkfileId,
    /^[A-Za-z0-9_-]{1,128}$/);
});

test("score pending sanitization preserves OQ candidates, mismatch, followup, and resolution", () => {
  const candidate = {
    candidateKey: "id:game-7",
    gameId: "game-7",
    blackAccount: "oq-a",
    whiteAccount: "oq-b",
    blackScore: 63,
    whiteScore: 1,
    gameDetail: { position: { moves: [{ m: "f5" }] } },
  };
  const item = sanitizeScoreItem({
    id: "pending-7",
    pairingId: "pairing-7",
    table: 7,
    pendingKind: "oq-auto-score-mismatch",
    reason: "OQ 回放比分不同",
    oqPendingDetail: { table: 7, candidateCount: 1, candidates: [candidate] },
    oqCandidates: [candidate],
    oqScoreMismatch: [{ gameId: "game-7", currentBlackScore: 32, oqBlackScore: 63 }],
    oqFollowup: { history: [{ reason: "发现后续局" }] },
    resolvedByReferee: true,
    resolutionStatus: "resolved",
    selectedSourceKey: "oq-auto:id:game-7",
  });

  assert.equal(item.pairingId, "pairing-7");
  assert.equal(item.oqPendingDetail.candidates[0].gameDetail.position.moves[0].m, "f5");
  assert.equal(item.oqCandidates[0].candidateKey, "id:game-7");
  assert.equal(item.oqScoreMismatch[0].oqBlackScore, 63);
  assert.equal(item.oqFollowup.history.length, 1);
  assert.equal(item.resolvedByReferee, true);
  assert.equal(item.resolutionStatus, "resolved");
  assert.equal(item.selectedSourceKey, "oq-auto:id:game-7");
});

test("score pairing and round sanitization preserve PAPP audits and poll metadata", () => {
  const audit = { candidateKey: "id:game-7", game: { gameId: "game-7" } };
  const round = sanitizeScoreRound({
    round: 4,
    roundStartAt: "2026-09-12 10:00:00",
    pairings: [{
      id: "pairing-7",
      table: 7,
      status: "dirty",
      dirty: true,
      lastEditedBy: "user",
      userEditedFields: { blackScore: true },
      oqAutoAudit: { game: { gameId: "old-game" } },
      oqGameAvailable: true,
      oqGameAvailableAt: 42,
      oqGameAvailableAudit: audit,
      blackScore: 32,
      whiteScore: 32,
    }],
    pending: [],
    oq: {
      queryErrors: { "oq-a": "timeout" },
      window: { startLocal: "start", endLocal: "end", minutes: 40 },
    },
  }, 1);
  const pairing = round.pairings[0];

  assert.equal(round.round, 4);
  assert.equal(pairing.status, "dirty");
  assert.equal(pairing.dirty, true);
  assert.equal(pairing.userEditedFields.blackScore, true);
  assert.equal(pairing.oqAutoAudit.game.gameId, "old-game");
  assert.deepEqual(pairing.oqGameAvailableAudit, audit);
  assert.deepEqual(round.oq.queryErrors, { "oq-a": "timeout" });
  assert.deepEqual(round.oq.window, { startLocal: "start", endLocal: "end", minutes: 40 });
});

test("sanitizeScorePairing rejects unknown statuses but retains source keys for auto scores", () => {
  const pairing = sanitizeScorePairing({
    id: "p-1",
    table: 1,
    status: "ready",
    blackScore: 63,
    whiteScore: 1,
    reporter: "OQ自动查询",
    resultKind: "oq-auto",
    sourceMessageKey: "oq-auto:id:g1",
    resultSortKey: 100,
    lastEditedBy: "script",
  }, 1);

  assert.equal(pairing.status, "ready");
  assert.equal(pairing.reporter, "OQ自动查询");
  assert.equal(pairing.resultKind, "oq-auto");
  assert.equal(pairing.sourceMessageKey, "oq-auto:id:g1");
  assert.equal(pairing.resultSortKey, 100);
});

test("a completed score needs a valid board score and PAPP readback stamp", () => {
  const oldCompleted = sanitizeScorePairing({
    id: "pairing-old",
    table: 1,
    black: "甲",
    white: "乙",
    status: "completed",
    blackScore: 40,
    whiteScore: 24,
  }, 1);
  const confirmed = sanitizeScorePairing({
    id: "pairing-confirmed",
    table: 2,
    black: "丙",
    white: "丁",
    status: "completed",
    blackScore: 0,
    whiteScore: 64,
    pappReadbackAt: "2026-09-12T10:00:00.000Z",
  }, 2);
  const invalid = sanitizeScorePairing({
    id: "pairing-invalid",
    table: 3,
    black: "戊",
    white: "己",
    status: "completed",
    blackScore: 32,
    whiteScore: 31,
    pappReadbackAt: "2026-09-12T10:00:00.000Z",
  }, 3);
  const empty = sanitizeScorePairing({
    id: "pairing-empty",
    table: 4,
    black: "庚",
    white: "辛",
    status: "ready",
    blackScore: "",
    whiteScore: "64",
  }, 4);

  assert.equal(oldCompleted.status, "ready");
  assert.equal(oldCompleted.pappReadbackAt, "");
  assert.equal(confirmed.status, "completed");
  assert.equal(isPappReadbackConfirmedPairing(confirmed), true);
  assert.equal(invalid.status, "imported");
  assert.equal(invalid.blackScore, 32);
  assert.equal(empty.blackScore, null);
});

test("score sanitization only exposes valid yellow rows for PAPP C validation", () => {
  assert.equal(isScoreBatchCandidate({
    black: "甲", white: "乙", status: "ready", blackScore: 40, whiteScore: 24,
  }), true);
  assert.equal(isScoreBatchCandidate({
    black: "甲", white: "乙", status: "ready", blackScore: 40, whiteScore: 23,
  }), false);
  assert.equal(isScoreBatchCandidate({
    black: "甲", white: "乙", status: "completed", blackScore: 40, whiteScore: 24,
  }), false);
  assert.equal(isScoreBatchCandidate({
    black: "甲", white: "", status: "ready", blackScore: 40, whiteScore: 24,
  }), false);
  assert.equal(isBoardScorePairing({
    black: "甲", white: "乙", status: "bye", blackScore: 64, whiteScore: 0,
  }), false);
});

test("PAPP readback must match the pairing, side orientation, and exact score", () => {
  const expected = {
    id: "pairing-1",
    black: "甲",
    white: "乙",
    blackAccount: "oq-a",
    whiteAccount: "oq-b",
    status: "ready",
    blackScore: 40,
    whiteScore: 24,
  };
  const readback = { ...expected, status: "completed" };
  const reversed = {
    ...readback,
    black: "乙",
    white: "甲",
    blackAccount: "oq-b",
    whiteAccount: "oq-a",
    blackScore: 24,
    whiteScore: 40,
  };

  assert.equal(scorePairingScoresMatch(expected, readback), true);
  assert.equal(scorePairingConfirmedByReadback(expected, readback), true);
  assert.equal(scorePairingConfirmedByReadback(expected, { ...readback, id: "other" }), false);
  assert.equal(scorePairingConfirmedByReadback(expected, reversed), false);
  assert.equal(scorePairingConfirmedByReadback(expected, {
    ...readback, blackScore: 39, whiteScore: 25,
  }), false);
  assert.equal(scorePairingConfirmedByReadback(expected, {
    ...readback, status: "ready",
  }), false);
});
