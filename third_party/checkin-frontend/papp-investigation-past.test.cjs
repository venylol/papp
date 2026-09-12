"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const service = require("./local-server.js");

const portal = path.join(__dirname, "papp-portal", "player-investigation");

test("past tournament investigation UI wires archive selection and both workflows", () => {
  const html = fs.readFileSync(path.join(portal, "index.html"), "utf8");
  const script = fs.readFileSync(path.join(portal, "player-investigation.js"), "utf8");
  for (const id of [
    "past-tournament-list",
    "past-player-list",
    "btn-past-sentinel",
    "btn-past-reported",
  ]) assert.match(html, new RegExp(`id=["']${id}["']`));
  assert.match(script, /\/api\/player-investigation\/tournaments\/detail\?file=/);
  assert.match(script, /selected\.length !== 1/);
  assert.match(script, /\/api\/player-investigation\/batch-sentinel/);
  assert.match(script, /batch-analysis\.html\?batchId=/);
  assert.match(script, /pendingAcquisitionFlow = "manual"/);
});

test("batch report page exposes ordered summary and individual report links", () => {
  const html = fs.readFileSync(path.join(portal, "batch-analysis.html"), "utf8");
  const script = fs.readFileSync(path.join(portal, "batch-analysis.js"), "utf8");
  assert.match(html, /id="batch-result-list"/);
  assert.match(script, /\/api\/player-investigation\/batch-status\?batchId=/);
  assert.match(script, /analysis\.html\?runId=/);
  assert.match(script, /result\.error/);
});

test("batch endpoint rejects archive paths outside the manual archive directory", () => {
  assert.throws(
    () => service.startBatchSentinelInvestigation({
      tournamentFile: "../data/checkin-state.csv",
      players: [{ rank: 1, account: "player" }],
    }),
    /文件名无效|路径越界/,
  );
});

test("batch endpoint accepts only OQ accounts in the selected official standings", () => {
  const [tournament] = service.listArchivedTournaments().filter((item) => item.available);
  if (!tournament) return;
  const detail = service.readArchivedTournament(tournament.file);
  const player = detail.players.find((item) => item.selectable);
  assert.ok(player);
  assert.throws(
    () => service.startBatchSentinelInvestigation({
      tournamentFile: tournament.file,
      players: [{ rank: player.rank, account: "__mismatched_account_for_test__" }],
    }),
    /账号与比赛存档不一致/,
  );
});
