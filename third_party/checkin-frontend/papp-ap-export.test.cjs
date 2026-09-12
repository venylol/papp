"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const directory = fs.mkdtempSync(path.join(os.tmpdir(), "papp-ap-png-"));
process.env.PAPP_AP_DOWNLOADS_DIR = directory;
const { exportImage, getRenderers } = require("./papp-ap-export.js");

test("background PNGs reuse the real canvas renderers and stable download operations", async t => {
  const state = {competitionName: "AP 自动编排测试", ap: {sessionId: "test"}, tournamentParameters: {hasSemifinalAndFinal: true}};
  const target = {stage: "preliminary", round: 1};
  const pairings = [{id: "table-1", table: 1, black: "黑方选手", white: "白方选手", blackAccount: "black", whiteAccount: "white", blackScore: 40, whiteScore: 24, status: "completed"},
    {id: "table-2", table: 2, black: "轮空选手", white: "BYE", blackScore: 40, whiteScore: 24, status: "bye"}];
  const standings = [{rank: 1, displayName: "黑方选手", totalPoints: 2, brightwell: 12, totalDiscs: 40, preliminaryRank: 1},
    {rank: 2, displayName: "白方选手", totalPoints: 0, brightwell: 6, totalDiscs: 24, preliminaryRank: 2}];
  const ctx = getRenderers().score.buildPairingsCanvas({pairings, round: 1}).getContext("2d");
  ctx.font = "850 24px Arial, sans-serif";
  assert.ok(ctx.measureText("0101").width < 100, "variable font weight must not become an 850px size");
  for (const kind of ["pairings", "scores", "preliminary", "overall"]) {
    const result = await exportImage(kind, {state, target, pairings, standings}, kind);
    const data = fs.readFileSync(result.file);
    assert.deepEqual([...data.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    assert.ok(result.width > 500 && result.height > 100);
    const repeat = await exportImage(kind, {state, target, pairings, standings}, kind);
    assert.equal(repeat.existing, true);
    assert.equal(repeat.file, result.file);
  }
  assert.equal(fs.readdirSync(directory).length, 4);
  t.diagnostic(`PNG previews retained: ${directory}`);
});
