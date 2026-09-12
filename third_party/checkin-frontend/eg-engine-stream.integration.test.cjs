"use strict";

const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

test("Windows EG keeps hint rows before prompts and produces cached pairing loss", {
  skip: process.platform !== "win32",
  timeout: 45000,
}, async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "papp-eg-stream-regression-"));
  const script = `
    const assert = require('node:assert/strict');
    const service = require('./local-server.js');
    const payload = { round: 1, pairings: Array.from({ length: 12 }, (_, i) => ({
      id: 'stream-' + i, table: i + 1, black: 'Black', white: 'White',
      blackAccount: 'black', whiteAccount: 'white', status: 'imported',
      metadata: { gameRecord: { gameId: 'stream-' + i, moves: ['f5'],
        pappBlackAccount: 'black', pappWhiteAccount: 'white' } }
    })) };
    const timeout = setTimeout(() => { service.stopEgAnalysis(); process.exitCode = 1; }, 30000);
    (async () => {
      assert.equal(service.startEgAnalysis(payload).ok, true);
      let result;
      do {
        await new Promise(resolve => setTimeout(resolve, 100));
        result = service.egStatus(payload);
      } while (result.running);
      assert.equal(result.error, '');
      assert.equal(result.analysis.gameCount, 12);
      assert.equal(Object.keys(result.analysis.pairingLossByRound['1']).length, 12);
      assert.equal(result.analysis.pairingLossByRound['1']['1'].players[0].totalLoss, 0);
      const cached = service.startEgAnalysis(payload);
      assert.equal(cached.running, false);
      assert.equal(cached.analysis.gameCount, 12);
    })().catch(error => { console.error(error); service.stopEgAnalysis(); process.exitCode = 1; })
      .finally(() => clearTimeout(timeout));
  `;
  const child = spawn(process.execPath, ["-e", script], {
    cwd: __dirname,
    env: { ...process.env, PAPP_DATA_DIR: dataDir },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", chunk => { output += chunk; });
  child.stderr.on("data", chunk => { output += chunk; });
  const code = await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("close", resolve);
  });
  assert.equal(code, 0, output);
});
