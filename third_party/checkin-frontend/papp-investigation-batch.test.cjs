"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { InvestigationBatchManager, normalizePlayers } = require("./papp-investigation-batch.js");

test("batch sentinel runs players by rank and continues after a failure", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "papp-batch-sentinel-"));
  const visited = [];
  const manager = new InvestigationBatchManager({
    root,
    runPlayer: async (player) => {
      visited.push(player.account);
      if (player.account === "broken") throw new Error("测试失败");
      return { runId: `run-${player.account}`, summary: { classification: "normal" } };
    },
  });
  const started = manager.start({
    tournamentFile: "秋季赛.csv",
    competitionName: "秋季赛",
    players: [
      { rank: 3, name: "丙", account: "gamma" },
      { rank: 1, name: "甲", account: "alpha" },
      { rank: 2, name: "乙", account: "broken" },
    ],
  });
  assert.equal(started.status, "running");
  let status;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    status = manager.status(started.batchId);
    if (status.status === "completed") break;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(status.status, "completed");
  assert.deepEqual(visited, ["alpha", "broken", "gamma"]);
  assert.equal(status.completedCount, 2);
  assert.equal(status.failedCount, 1);
  assert.equal(status.results[1].error, "测试失败");
  assert.ok(fs.existsSync(status.reportPath));
  const report = JSON.parse(fs.readFileSync(status.reportPath, "utf8"));
  assert.equal(report.schema, "papp-batch-sentinel-report-v1");
  assert.equal(report.results.length, 3);
});

test("batch sentinel rejects missing and duplicate accounts", () => {
  assert.throws(() => normalizePlayers([{ rank: 1, name: "甲", account: "" }]), /缺少有效/);
  assert.throws(() => normalizePlayers([
    { rank: 1, account: "Same" },
    { rank: 2, account: "same" },
  ]), /重复选择/);
});

test("batch status reads do not rewrite the progress snapshot", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "papp-batch-status-"));
  let finishPlayer;
  const manager = new InvestigationBatchManager({
    root,
    runPlayer: () => new Promise((resolve) => { finishPlayer = resolve; }),
  });
  const started = manager.start({ tournamentFile: "赛.csv", players: [{ rank: 1, account: "alpha" }] });
  const progress = path.join(root, started.batchId, "progress.json");
  const contents = fs.readFileSync(progress, "utf8");
  manager.status(started.batchId);
  assert.equal(fs.readFileSync(progress, "utf8"), contents);
  finishPlayer({ runId: "run-alpha", summary: { status: "completed" } });
});

test("completed batch status reads the final report from disk", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "papp-batch-final-report-"));
  const manager = new InvestigationBatchManager({
    root,
    runPlayer: async () => ({ runId: "run-alpha", summary: { status: "completed" } }),
  });
  const started = manager.start({ tournamentFile: "赛.csv", players: [{ rank: 1, account: "alpha" }] });
  let status;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    status = manager.status(started.batchId);
    if (status.status === "completed") break;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  const reportPath = path.join(root, started.batchId, "report.json");
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  report.repairedAt = "2026-09-13T00:00:00.000Z";
  fs.writeFileSync(reportPath, JSON.stringify(report), "utf8");
  assert.equal(manager.status(started.batchId).repairedAt, "2026-09-13T00:00:00.000Z");
});
