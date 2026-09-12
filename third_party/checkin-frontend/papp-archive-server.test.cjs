"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const archive = require("./papp-tournament-archive.js");
const { server } = require("./local-server.js");

test("manual archive HTTP endpoint and launcher health version", async () => {
  const original = archive.createArchive;
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "papp-archive-http-"));
  archive.createArchive = (state, options) => {
    assert.equal(options.directory, path.resolve(__dirname, "../../manual-tournament-archives"));
    return original(state, { ...options, directory: temp });
  };
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const health = await (await fetch(base + "/api/health")).json();
    const launcher = fs.readFileSync(path.resolve(__dirname, "../../打开PAPP前端.cmd"), "utf8");
    assert.equal(health.ok, true);
    assert.equal(health.service, "papp-local-frontend");
    assert.equal(health.version, launcher.match(/SERVER_VERSION=([^"\r\n]+)/)[1]);
    const response = await fetch(base + "/api/papp/archive", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: { competitionName: "未完赛", players: [], tournamentParameters: { hasSemifinalAndFinal: false },
        scoreHelper: { preliminaryRoundCount: 1, rounds: [{ pairings: [] }] } } }) });
    const result = await response.json();
    assert.equal(response.status, 200);
    assert.equal(result.ok, true);
    assert.equal(result.file, path.join(temp, "未完赛.csv"));
    assert.ok(result.missingCount > 0);
    assert.match(fs.readFileSync(result.file, "utf8"), /缺失/);
    const invalid = await fetch(base + "/api/papp/archive", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    assert.equal(invalid.status, 400);
    assert.equal((await invalid.json()).ok, false);
  } finally {
    archive.createArchive = original;
    await new Promise(resolve => server.close(resolve));
  }
});
