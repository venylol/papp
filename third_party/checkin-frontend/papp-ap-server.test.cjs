"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { mergeChanges } = require("./papp-ap-coordinator.js");
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

test("concurrent human score edit never inherits stale AP confirmation", () => {
  const base = {rounds: [{round: 1, pairings: [{id: "a", blackScore: 40, whiteScore: 24, status: "ready"}]}]};
  const next = structuredClone(base);
  Object.assign(next.rounds[0].pairings[0], {status: "completed", pappReadbackAt: "receipt"});
  const human = structuredClone(base);
  Object.assign(human.rounds[0].pairings[0], {blackScore: 42, whiteScore: 22});
  assert.deepEqual(mergeChanges(base, next, human), human);
});

test("AP service controls and queued patches preserve concurrent human edits", async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "papp-ap-service-"));
  const script = `const s=require(${JSON.stringify(path.join(__dirname, "local-server.js"))});s.start();s.server.on('listening',()=>console.log('TEST_PORT='+s.server.address().port));`;
  const child = spawn(process.execPath, ["-e", script], { windowsHide: true,
    env: {...process.env, PAPP_PORT: "0", PAPP_DATA_DIR: directory, PAPP_STATE_FILE: path.join(directory, "checkin-state.json"), PAPP_AP_DOWNLOADS_DIR: path.join(directory, "Downloads")},
    stdio: ["ignore", "pipe", "pipe"] });
  let output = "", errors = "";
  child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
  child.stdout.on("data", chunk => output += chunk); child.stderr.on("data", chunk => errors += chunk);
  t.after(() => { child.kill(); t.diagnostic(`Retained test files: ${directory}`); });
  const deadline = Date.now() + 10000;
  while (!/TEST_PORT=(\d+)/.test(output) && Date.now() < deadline) await delay(20);
  assert.match(output, /TEST_PORT=(\d+)/, errors);
  const url = `http://127.0.0.1:${output.match(/TEST_PORT=(\d+)/)[1]}`;
  const request = async (route, body) => {
    const response = await fetch(url + route, {method: body ? "POST" : "GET", headers: {"Content-Type": "application/json"}, ...(body ? {body: JSON.stringify(body)} : {})});
    const result = await response.json();
    assert.equal(response.ok, true, JSON.stringify(result));
    return result;
  };
  const date = new Date(Date.now() + 86400000).toISOString();
  const original = {version: 2, step: "schedule", competitionName: "AP 测试", players: [{id: 1, displayName: "选手", checkedIn: false}],
    eventSchedule: {registrationDeadline: date, checkinStart: date, checkinDeadline: date, competitionStart: date, wechatGroup: {username: "test@chatroom"}},
    mapping: {rows: [{registrationNick: "选手"}]}, tournamentParameters: {semifinalAndFinalMode: "off", hasSemifinalAndFinal: false, brightwellConstant: 6},
    scoreHelper: {preliminaryRoundCount: 1, rounds: [{round: 1, pairings: [], roundStartAt: "original-time"}]}, ui: {oqPollSeconds: 15}};
  await request("/api/state", {state: original, source: "human"});
  assert.equal((await request("/api/ap/status")).oqPollSeconds, 15);
  let enabled = await request("/api/ap/control", {action: "enable"});
  assert.equal(enabled.ap.enabled, true);
  await request("/api/ap/control", {action: "pause"});
  const base = (await request("/api/state")).state;
  const proposed = structuredClone(base);
  proposed.ap.checkin = {relayFinal: true};
  const queued = await request("/api/state", {operation: "ap-patch", baseState: base, state: proposed, source: "script"});
  assert.equal(queued.queued, true);
  const human = structuredClone(base);
  human.players[0].checkedIn = true;
  human.ap = {enabled: false, status: "stale-browser-copy"};
  await request("/api/state", {state: human, source: "human"});
  assert.equal((await request("/api/ap/status")).ap.enabled, true);
  await delay(3300);
  const final = (await request("/api/state")).state;
  assert.equal(final.players[0].checkedIn, true);
  assert.equal(final.ap.checkin.relayFinal, true);
  assert.deepEqual(final.scoreHelper, original.scoreHelper);
  await request("/api/ap/control", {action: "stop"});
  assert.equal((await request("/api/ap/status")).ap.enabled, false);
  const health = await request("/api/health");
  const launcher = fs.readFileSync(path.resolve(__dirname, "../../打开PAPP前端.cmd"), "utf8");
  assert.ok(launcher.includes(`SERVER_VERSION=${health.version}`));
});
