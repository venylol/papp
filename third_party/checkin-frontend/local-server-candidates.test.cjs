"use strict";

const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const serverModule = path.join(__dirname, "local-server.js");

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function startIsolatedServer(t) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "papp-candidate-sync-"));
  const serverScript = [
    `const service = require(${JSON.stringify(serverModule)});`,
    "service.start();",
    "service.server.on('listening', () => {",
    "  process.stdout.write('PAPP_TEST_PORT=' + service.server.address().port + '\\n');",
    "});",
  ].join("\n");
  const child = spawn(process.execPath, ["-e", serverScript], {
    env: {
      ...process.env,
      PAPP_DATA_DIR: tempDir,
      PAPP_STATE_FILE: path.join(tempDir, "checkin-state.json"),
      PAPP_HOST: "127.0.0.1",
      PAPP_PORT: "0",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stdout = "";
  let stderr = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });

  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill();
    if (child.exitCode === null && child.signalCode === null) {
      await Promise.race([
        new Promise((resolve) => child.once("exit", resolve)),
        delay(1000),
      ]);
    }
    t.diagnostic(`Retained isolated test files: ${tempDir}`);
  });

  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const match = stdout.match(/PAPP_TEST_PORT=(\d+)/);
    if (match) return { baseUrl: `http://127.0.0.1:${match[1]}`, child, stderr };
    if (child.exitCode !== null) throw new Error(`PAPP local server exited: ${stderr}`);
    await delay(20);
  }
  throw new Error(`PAPP local server did not start: ${stderr}`);
}

async function requestJson(url, method, payload) {
  const response = await fetch(url, {
    method,
    headers: payload ? { "Content-Type": "application/json; charset=utf-8" } : undefined,
    body: payload ? JSON.stringify(payload) : undefined,
  });
  return { status: response.status, body: await response.json() };
}

async function waitForState(baseUrl, predicate, description) {
  const deadline = Date.now() + 7000;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await requestJson(`${baseUrl}/api/state`, "GET");
    if (predicate(latest.body.state)) return latest.body;
    await delay(50);
  }
  throw new Error(`timed out waiting for ${description}`);
}

function originalPlayerForMappingTest(id, checkedIn) {
  return {
    id,
    displayName: `候选${id}`,
    platform: "OQ",
    account: `account-${id}`,
    checkedIn,
    checkedInAt: checkedIn ? "2026-09-12T09:00:00.000Z" : "",
    club: "测试俱乐部",
  };
}

test("candidate sync uses POST /api/state, respects the script delay, and patches latest state", async (t) => {
  const { baseUrl } = await startIsolatedServer(t);
  const originalPlayer = {
    id: "old",
    displayName: "旧选手",
    platform: "OQ",
    account: "old-account",
    checkedIn: true,
  };
  const initialState = {
    version: 2,
    step: "checkin",
    competitionName: "before",
    players: [originalPlayer],
    scoreHelper: { rounds: [] },
  };
  const initialized = await requestJson(`${baseUrl}/api/state`, "POST", {
    state: initialState,
    source: "human",
  });
  assert.equal(initialized.status, 200);
  assert.equal(initialized.body.state.localSync.source, "human");

  const candidates = [
    { ...originalPlayer, id: "duplicate-1", displayName: "同名", account: "account-1" },
    { ...originalPlayer, id: "duplicate-2", displayName: "同名", account: "account-2", checkedIn: false },
  ];
  const queued = await requestJson(`${baseUrl}/api/state`, "POST", {
    operation: "sync-candidates",
    candidatePlayers: candidates,
    source: "script",
  });
  assert.equal(queued.status, 202);
  assert.equal(queued.body.ok, true);
  assert.equal(queued.body.queued, true);
  assert.ok(queued.body.retryAfterMs > 0);

  const beforeFlush = await requestJson(`${baseUrl}/api/state`, "GET");
  assert.equal(beforeFlush.body.scriptWritePending, true);
  assert.ok(beforeFlush.body.retryAfterMs > 0);
  assert.deepEqual(beforeFlush.body.state.players, [originalPlayer]);

  const humanUpdate = await requestJson(`${baseUrl}/api/state`, "POST", {
    state: {
      ...beforeFlush.body.state,
      competitionName: "latest human state",
    },
    source: "human",
  });
  assert.equal(humanUpdate.status, 200);

  let persisted = null;
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    const current = await requestJson(`${baseUrl}/api/state`, "GET");
    if (JSON.stringify(current.body.state.players) === JSON.stringify(candidates)) {
      persisted = current.body;
      break;
    }
    await delay(50);
  }
  assert.ok(persisted, "queued candidate roster was persisted");
  assert.deepEqual(persisted.state.players, candidates);
  assert.equal(persisted.state.competitionName, "latest human state");
  assert.deepEqual(persisted.state.scoreHelper, { rounds: [] });
  assert.equal(persisted.state.localSync.source, "script");
  assert.equal(persisted.scriptWritePending, false);
  assert.equal(persisted.retryAfterMs, 0);

  const idempotent = await requestJson(`${baseUrl}/api/state`, "POST", {
    operation: "sync-candidates",
    candidatePlayers: candidates,
    source: "script",
  });
  assert.equal(idempotent.status, 200);
  assert.equal(idempotent.body.changed, false);
  assert.equal(idempotent.body.revision, persisted.revision);
});

test("mapping-only sync updates one unchecked PAPP player and preserves unrelated data", async (t) => {
  const { baseUrl } = await startIsolatedServer(t);
  const unchecked = { ...originalPlayerForMappingTest("mapped", false), displayName: "候选显示名" };
  const other = originalPlayerForMappingTest("other", true);
  const oldPappRecord = {
    ...unchecked,
    name: "旧报名姓名",
    country: "old-account",
    rating: 1742,
    profile: { category: "club", keep: true },
  };
  const untouchedPappRecord = {
    ...other,
    name: "原有姓名",
    country: "other-account",
    score: 15,
  };
  const initialState = {
    version: 2,
    step: "checkin",
    competitionName: "映射测试",
    players: [unchecked, other],
    pappPlayers: [oldPappRecord, untouchedPappRecord],
    scoreHelper: { rounds: [{ round: 1, marker: "preserve-history" }] },
  };
  const initialized = await requestJson(`${baseUrl}/api/state`, "POST", {
    state: initialState,
    source: "human",
  });
  assert.equal(initialized.status, 200);

  const mappingPlayers = [{
    mappingRowId: "map-mapped",
    candidatePlayerId: "mapped",
    name: "新报名姓名",
    country: "new-account",
  }];
  const write = await requestJson(`${baseUrl}/api/state`, "POST", {
    operation: "sync-candidates",
    candidatePlayers: [unchecked, other],
    mappingPlayers,
    source: "script",
  });
  assert.equal(write.status, 202);

  const persisted = await waitForState(baseUrl, (state) => {
    const record = (state.pappPlayers || []).find((player) => player.id === "mapped");
    return record && record.name === "新报名姓名" && record.country === "new-account";
  }, "mapping-only PAPP field update");
  const mappedRecord = persisted.state.pappPlayers.find((player) => player.id === "mapped");

  assert.deepEqual(persisted.state.players, [unchecked, other]);
  assert.equal(persisted.state.players[0].checkedIn, false);
  assert.deepEqual(persisted.state.scoreHelper, initialState.scoreHelper);
  assert.deepEqual(mappedRecord, {
    ...oldPappRecord,
    name: "新报名姓名",
    country: "new-account",
  });
  assert.deepEqual(
    persisted.state.pappPlayers.find((player) => player.id === "other"),
    untouchedPappRecord,
  );
  assert.ok(persisted.revision > initialized.body.revision, "mapping change writes despite an unchanged candidate roster");

  const repeated = await requestJson(`${baseUrl}/api/state`, "POST", {
    operation: "sync-candidates",
    candidatePlayers: [unchecked, other],
    mappingPlayers,
    source: "script",
  });
  assert.equal(repeated.status, 200);
  assert.equal(repeated.body.changed, false);
  assert.equal(repeated.body.revision, persisted.revision);
  assert.equal(repeated.body.state.pappPlayers.length, 2);
});

test("removed candidates and omitted mapping rows retain PAPP players and history", async (t) => {
  const { baseUrl } = await startIsolatedServer(t);
  const mapped = originalPlayerForMappingTest("mapped", true);
  const remaining = originalPlayerForMappingTest("remaining", false);
  const history = [{
    round: 1,
    pairings: [{
      table: 1,
      black: "旧 PAPP 选手",
      white: "对手",
      metadata: { papp: { blackPlayerId: "mapped", whitePlayerId: "remaining" } },
    }],
  }];
  const initialState = {
    version: 2,
    step: "checkin",
    players: [mapped, remaining],
    scoreHelper: { rounds: history },
  };
  const initialized = await requestJson(`${baseUrl}/api/state`, "POST", {
    state: initialState,
    source: "human",
  });
  assert.equal(initialized.status, 200);

  const mappingPlayers = [{
    mappingRowId: "map-mapped",
    candidatePlayerId: "mapped",
    name: "已同步姓名",
    country: "saved-account",
  }];
  await requestJson(`${baseUrl}/api/state`, "POST", {
    operation: "sync-candidates",
    candidatePlayers: [mapped, remaining],
    mappingPlayers,
    source: "script",
  });
  const mappedState = await waitForState(baseUrl, (state) =>
    (state.pappPlayers || []).some((player) =>
      player.id === "mapped" && player.name === "已同步姓名" && player.country === "saved-account",
    ), "initial mapping write");
  const savedRecord = mappedState.state.pappPlayers.find((player) => player.id === "mapped");

  const withoutPappExtension = { ...mappedState.state };
  delete withoutPappExtension.pappPlayers;
  withoutPappExtension.competitionName = "人类状态更新";
  const humanUpdate = await requestJson(`${baseUrl}/api/state`, "POST", {
    state: withoutPappExtension,
    source: "human",
  });
  assert.equal(humanUpdate.status, 200);
  assert.deepEqual(humanUpdate.body.state.pappPlayers.find((player) => player.id === "mapped"), savedRecord);

  const omittedMapping = await requestJson(`${baseUrl}/api/state`, "POST", {
    operation: "sync-candidates",
    candidatePlayers: [mapped, remaining],
    mappingPlayers: [],
    source: "script",
  });
  assert.equal(omittedMapping.status, 200);
  assert.equal(omittedMapping.body.changed, false);
  assert.deepEqual(omittedMapping.body.state.pappPlayers.find((player) => player.id === "mapped"), savedRecord);

  await requestJson(`${baseUrl}/api/state`, "POST", {
    operation: "sync-candidates",
    candidatePlayers: [remaining],
    mappingPlayers: [],
    source: "script",
  });
  const afterRemoval = await waitForState(baseUrl, (state) =>
    state.players.length === 1 && state.players[0].id === "remaining",
  "candidate removal sync");

  assert.deepEqual(afterRemoval.state.pappPlayers.find((player) => player.id === "mapped"), savedRecord);
  assert.deepEqual(afterRemoval.state.scoreHelper.rounds, history);
  assert.deepEqual(afterRemoval.state.players, [remaining]);
});

test("candidate sync endpoint reports invalid player data clearly", async (t) => {
  const { baseUrl } = await startIsolatedServer(t);
  await requestJson(`${baseUrl}/api/state`, "POST", {
    state: { version: 2, step: "checkin", players: [] },
    source: "human",
  });
  const result = await requestJson(`${baseUrl}/api/state`, "POST", {
    operation: "sync-candidates",
    candidatePlayers: [{ displayName: "缺少签到状态" }],
    source: "script",
  });
  assert.equal(result.status, 400);
  assert.equal(result.body.ok, false);
  assert.match(result.body.detail, /checkedIn must be a boolean/);
});

test("candidate sync rejects ambiguous mappings without writing", async (t) => {
  const { baseUrl } = await startIsolatedServer(t);
  const candidates = [
    originalPlayerForMappingTest("duplicate", true),
    { ...originalPlayerForMappingTest("duplicate", true), account: "second-account" },
  ];
  const initialized = await requestJson(`${baseUrl}/api/state`, "POST", {
    state: { version: 2, step: "checkin", players: candidates },
    source: "human",
  });
  assert.equal(initialized.status, 200);

  const rejected = await requestJson(`${baseUrl}/api/state`, "POST", {
    operation: "sync-candidates",
    candidatePlayers: candidates,
    mappingPlayers: [{
      mappingRowId: "ambiguous-row",
      candidatePlayerId: "duplicate",
      name: "不应猜测",
      country: "account",
    }],
    source: "script",
  });
  assert.equal(rejected.status, 400);
  assert.match(rejected.body.detail, /exactly one candidatePlayer/);
  assert.equal(rejected.body.ok, false);
  const persisted = await requestJson(`${baseUrl}/api/state`, "GET");
  assert.deepEqual(persisted.body.state.players, candidates);
  assert.equal(persisted.body.state.pappPlayers, undefined);
});
