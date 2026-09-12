"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { sanitizeStandingsSnapshots } = require("./app.js");

function snapshot(kind, round, playerId, rank, pappWorkfileId = "workfile-a") {
  return {
    kind,
    round,
    source: "papp-c",
    operation: kind === "overall" ? "overall-standings" : "round-standings",
    pappWorkfileId,
    capturedAt: 1789200000000 + round,
    progress: { complete: true, expectedRounds: round },
    standings: [{
      playerId,
      rank,
      displayName: `Player ${playerId}`,
      totalPoints: rank / 2,
      brightwell: 12,
      totalDiscs: 64,
    }],
  };
}

test("standings snapshots keep native ranks per round and stay bound to the tournament workfile", () => {
  const cache = sanitizeStandingsSnapshots([
    snapshot("preliminary", 2, "b", 9),
    snapshot("preliminary", 1, "a", 4),
    snapshot("preliminary", 2, "c", 2),
    { ...snapshot("preliminary", 3, "d", 1), source: "javascript" },
    snapshot("preliminary", 4, "e", 1, "workfile-b"),
    snapshot("overall", 2, "z", 1),
  ], "workfile-a");

  assert.deepEqual(cache.map((entry) => `${entry.kind}:${entry.round}`), [
    "preliminary:1",
    "preliminary:2",
    "overall:2",
  ]);
  assert.deepEqual(cache[1].standings.map((row) => row.id), ["c"]);
  assert.equal(cache[1].standings[0].rank, 2);
  assert.equal(cache[1].standings[0].totalPoints, 1);
  assert.equal(cache[1].pappWorkfileId, "workfile-a");
});
