"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");
const vm = require("node:vm");
const { pollLocalOqRound } = require("./local-server.js");

const adapterSource = fs.readFileSync(
  path.join(__dirname, "tournament-adapter.js"),
  "utf8",
);
const PAPP_C = process.env.PAPP_C_EXE
  ? path.resolve(process.env.PAPP_C_EXE)
  : path.resolve(__dirname, "..", "..", "bin", "Windows", "papp_GB.exe");
const SCORE_WORKFILE_DIRECTORY = fs.mkdtempSync(path.join(os.tmpdir(), "papp-adapter-score-workfiles-"));
const scoreWorkfiles = new Map();

function scoreWorkfileFor(batchId) {
  if (!scoreWorkfiles.has(batchId)) {
    scoreWorkfiles.set(batchId, path.join(SCORE_WORKFILE_DIRECTORY,
      `workfile-${scoreWorkfiles.size + 1}.txt`));
  }
  return scoreWorkfiles.get(batchId);
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
    json: async () => plain(payload),
  };
}

function pappCResponse(payload) {
  const scoreBatch = payload.operation === "write-score-batch" ||
    payload.operation === "read-score-batch";
  const child = spawnSync(PAPP_C, ["--tournament-json"], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    windowsHide: true,
    env: scoreBatch ? {
      ...process.env,
      PAPP_TOURNAMENT_WORKFILE: scoreWorkfileFor(payload.batchId),
    } : process.env,
  });
  assert.ifError(child.error);
  const result = JSON.parse(child.stdout);
  return jsonResponse(child.status === 0 ? 200 : 400, result);
}

function pappPlayersAfterSync(state, candidatePlayers, mappingPlayers) {
  const pappPlayers = Array.isArray(state.pappPlayers) ? plain(state.pappPlayers) : [];
  const recordsById = new Map(pappPlayers.map((player) => [String(player.id), player]));
  const addCandidate = (player) => {
    const id = String(player && player.id != null ? player.id : "").trim();
    if (!id || recordsById.has(id)) return;
    const record = plain(player);
    recordsById.set(id, record);
    pappPlayers.push(record);
  };
  for (const player of state.players || []) addCandidate(player);
  for (const player of candidatePlayers) addCandidate(player);
  for (const mapping of mappingPlayers || []) {
    const record = recordsById.get(String(mapping.candidatePlayerId));
    if (record) {
      record.name = mapping.name;
      record.country = mapping.country;
    }
  }
  return pappPlayers;
}

function memoryStateFetch(initialState = { version: 2, step: "checkin", players: [] }) {
  let state = plain(initialState);
  let writeCount = 0;
  const fetcher = async (_url, options = {}) => {
    if (options.method === "GET") {
      return jsonResponse(200, { ok: true, state: plain(state) });
    }
    const payload = JSON.parse(options.body || "{}");
    if (payload.operation !== "sync-candidates") {
      return jsonResponse(400, { ok: false, error: "unsupported test operation" });
    }
    const nextPlayers = plain(payload.candidatePlayers);
    const nextPappPlayers = pappPlayersAfterSync(
      state,
      nextPlayers,
      payload.mappingPlayers || [],
    );
    const nextState = { ...state, players: nextPlayers };
    if (nextPappPlayers.length || Array.isArray(state.pappPlayers)) {
      nextState.pappPlayers = nextPappPlayers;
    }
    const changed = JSON.stringify(state.players) !== JSON.stringify(nextPlayers) ||
      JSON.stringify(state.pappPlayers) !== JSON.stringify(nextState.pappPlayers);
    if (changed) {
      state = nextState;
      writeCount += 1;
    }
    return jsonResponse(200, { ok: true, changed, state: plain(state) });
  };
  fetcher.getState = () => plain(state);
  fetcher.getWriteCount = () => writeCount;
  return fetcher;
}

function loadAdapter(fetcher = memoryStateFetch(), previous = {}, tournamentFetch = pappCResponse) {
  const window = { PAPP_TOURNAMENT_ADAPTER: previous };
  window.fetch = async (url, options = {}) => {
    if (url === "/api/papp/tournament") {
      return tournamentFetch(JSON.parse(options.body || "{}"));
    }
    return fetcher(url, options);
  };
  window.setTimeout = setTimeout;
  vm.runInNewContext(adapterSource, { window });
  return window.PAPP_TOURNAMENT_ADAPTER;
}

test("score validation and batch operations use the PAPP C interface", async () => {
  const players = [
    { ...makePlayer("player-a", true), displayName: "选手a", account: "account-a" },
    { ...makePlayer("player-b", true), displayName: "选手b", account: "account-b" },
    { ...makePlayer("player-c", true), displayName: "选手c", account: "account-c" },
    { ...makePlayer("player-d", true), displayName: "选手d", account: "account-d" },
  ];
  const adapter = loadAdapter(memoryStateFetch({
    version: 2,
    scoreHelper: {
      preliminaryRoundCount: 1,
      rounds: [],
    },
    tournamentParameters: { hasSemifinalAndFinal: false },
  }));
  const context = {
    stage: "preliminary",
    round: 1,
    batchId: "score-preliminary-r1:pairing-1=40-24",
    players,
    checkedInPlayers: players,
    tournamentParameters: { hasSemifinalAndFinal: false },
    state: {
      scoreHelper: {
        pappWorkfileId: "score-batch-tournament",
        preliminaryRoundCount: 1,
        roundCount: 1,
        rounds: [],
      },
      tournamentParameters: { hasSemifinalAndFinal: false },
    },
    pairings: [{
      id: "pairing-1",
      table: 1,
      black: "选手a",
      white: "选手b",
      blackId: "player-a",
      whiteId: "player-b",
      blackAccount: "account-a",
      whiteAccount: "account-b",
      status: "ready",
      blackScore: 40,
      whiteScore: 24,
    }],
  };
  context.pairingIds = context.pairings.map((pairing) => pairing.id);

  const score = plain(await adapter.registerScore({ pairing: context.pairings[0] }));
  const write = plain(await adapter.writeScoreBatch(context));
  const read = plain(await adapter.readScoreBatch(context));
  assert.equal(score.source, "papp-c");
  assert.deepEqual(score.scorePair, { blackScore: 40, whiteScore: 24 });
  assert.equal(write.ok, true);
  assert.equal(write.accepted, true);
  assert.equal(write.pairings, undefined);
  assert.equal(write.source, "papp-c");
  assert.equal(read.ok, true, JSON.stringify(read));
  assert.deepEqual(read.pairings, [{
    id: "pairing-1",
    table: 1,
    black: "选手a",
    white: "选手b",
    blackAccount: "account-a",
    whiteAccount: "account-b",
    status: "completed",
    blackScore: 40,
    whiteScore: 24,
  }]);

  const invalidScore = plain(await adapter.writeScoreBatch({
    ...context,
    batchId: "invalid-score-batch",
    pairings: [{ ...context.pairings[0], blackScore: "", whiteScore: 64 }],
  }));
  assert.equal(invalidScore.ok, false);
});

test("readScoreBatch rejects a PAPP response with a mismatched persisted score", async () => {
  const players = [
    { ...makePlayer("player-a", true), displayName: "选手a", account: "account-a" },
    { ...makePlayer("player-b", true), displayName: "选手b", account: "account-b" },
  ];
  const context = {
    stage: "preliminary",
    round: 1,
    batchId: "score-readback-mismatch",
    pairingIds: ["pairing-1"],
    players,
    checkedInPlayers: players,
    state: {
      scoreHelper: {
        pappWorkfileId: "readback-tournament",
        preliminaryRoundCount: 1,
        roundCount: 1,
        rounds: [],
      },
      tournamentParameters: { hasSemifinalAndFinal: false },
    },
    pairings: [{
      id: "pairing-1", table: 1,
      blackId: "player-a", whiteId: "player-b",
      black: "选手a", white: "选手b",
      blackAccount: "account-a", whiteAccount: "account-b",
      blackScore: 40, whiteScore: 24, status: "ready",
    }],
  };
  const adapter = loadAdapter(memoryStateFetch(), {}, (payload) => jsonResponse(200, {
    ok: true,
    source: "papp-c",
    verified: true,
    batchId: payload.batchId,
    pairings: [{
      ...payload.pairings[0],
      status: "completed",
      blackScore: 39,
      whiteScore: 25,
    }],
  }));

  const read = plain(await adapter.readScoreBatch(context));
  assert.equal(read.ok, false);
  assert.match(read.message, /身份或比分不一致/);
});

test("score batch requests carry the active tournament workfile identity", async () => {
  const players = [
    { ...makePlayer("player-a", true), displayName: "选手a", account: "account-a" },
    { ...makePlayer("player-b", true), displayName: "选手b", account: "account-b" },
  ];
  let submitted;
  const adapter = loadAdapter(memoryStateFetch(), {}, (payload) => {
    submitted = payload;
    return jsonResponse(200, {
      ok: true,
      source: "papp-c",
      accepted: true,
      batchId: payload.batchId,
    });
  });
  const context = {
    stage: "preliminary",
    round: 1,
    batchId: "score-workfile-routing",
    players,
    checkedInPlayers: players,
    state: {
      scoreHelper: {
        pappWorkfileId: "tournament-a",
        preliminaryRoundCount: 1,
        roundCount: 1,
        rounds: [],
      },
      tournamentParameters: { hasSemifinalAndFinal: false },
    },
    pairings: [{
      id: "pairing-1",
      table: 1,
      blackId: "player-a",
      whiteId: "player-b",
      black: "选手a",
      white: "选手b",
      blackAccount: "account-a",
      whiteAccount: "account-b",
      blackScore: 40,
      whiteScore: 24,
      status: "ready",
    }],
  };

  const result = plain(await adapter.writeScoreBatch(context));
  assert.equal(result.ok, true);
  assert.equal(submitted.pappWorkfileId, "tournament-a");
});

test("score registration resolves the latest OQ mapping without a group nickname", async () => {
  const players = [
    { ...makePlayer("mapped-a", true), displayName: "报名甲", account: "old-a" },
    { ...makePlayer("mapped-b", true), displayName: "报名乙", account: "old-b" },
  ];
  let submitted;
  const adapter = loadAdapter(memoryStateFetch(), {}, (payload) => {
    submitted = payload;
    return jsonResponse(200, {
      ok: true,
      source: "papp-c",
      accepted: true,
      batchId: payload.batchId,
    });
  });
  const result = plain(await adapter.writeScoreBatch({
    stage: "preliminary",
    round: 1,
    batchId: "score-latest-mapping",
    players,
    checkedInPlayers: players,
    state: {
      scoreHelper: {
        pappWorkfileId: "mapping-score-tournament",
        preliminaryRoundCount: 1,
        roundCount: 1,
      },
      mapping: {
        rows: [
          { checkinPlayerId: "mapped-a", registrationNick: "报名甲", oqAccount: "new-a", wechatNick: "" },
          { checkinPlayerId: "mapped-b", registrationNick: "报名乙", oqAccount: "new-b", wechatNick: "" },
        ],
      },
    },
    pairings: [{
      id: "mapped-score-pairing",
      table: 1,
      blackId: "mapped-a",
      whiteId: "mapped-b",
      black: "报名甲",
      white: "报名乙",
      blackAccount: "old-a",
      whiteAccount: "old-b",
      blackScore: 40,
      whiteScore: 24,
      status: "ready",
    }],
  }));

  assert.equal(result.ok, true);
  assert.deepEqual(
    [submitted.pairings[0].blackAccount, submitted.pairings[0].whiteAccount],
    ["new-a", "new-b"],
  );
});

test("legacy adapter overrides cannot replace the PAPP C score methods", async () => {
  const writeScoreBatch = async () => ({ ok: true });
  const readScoreBatch = async () => ({ ok: true, pairings: [] });
  const adapter = loadAdapter(memoryStateFetch(), { writeScoreBatch, readScoreBatch });

  assert.notEqual(adapter.writeScoreBatch, writeScoreBatch);
  assert.notEqual(adapter.readScoreBatch, readScoreBatch);
  const result = plain(await adapter.registerScore({ pairing: { blackScore: 35, whiteScore: 29 } }));
  assert.equal(result.source, "papp-c");
});

test("automatic and preserved manual preliminary round counts come from PAPP C", async () => {
  const adapter = loadAdapter();
  const auto = plain(await adapter.getRoundCount({ playerCount: 5 }));
  const manual = plain(await adapter.getRoundCount({
    playerCount: 5,
    state: { scoreHelper: { roundCountSource: "manual", preliminaryRoundCount: 4 } },
  }));

  assert.equal(auto.source, "papp-c");
  assert.equal(auto.roundCount, 4);
  assert.equal(manual.source, "papp-c");
  assert.equal(manual.roundCount, 4);
});

function makePlayer(id, checkedIn) {
  return {
    id: String(id),
    displayName: `选手${id}`,
    account: `account-${id}`,
    club: `club-${id}`,
    platform: "OQ",
    group: "A",
    checkedIn,
    checkedInAt: checkedIn ? `2026-09-12T09:0${id}:00.000Z` : "",
    isNew: false,
  };
}

test("getRoundStandings forwards the selected round and full history to PAPP C", async () => {
  const players = ["a", "b", "c", "d"].map((id) => makePlayer(id, true));
  const rounds = [
    {
      round: 1,
      pairings: [
        { id: "r1-1", blackId: "a", whiteId: "b", blackScore: 64, whiteScore: 0, status: "completed" },
        { id: "r1-2", blackId: "c", whiteId: "d", blackScore: 64, whiteScore: 0, status: "completed" },
      ],
    },
    {
      round: 2,
      pairings: [
        { id: "r2-1", blackId: "a", whiteId: "c", blackScore: 64, whiteScore: 0, status: "completed" },
        { id: "r2-2", blackId: "b", whiteId: "d", blackScore: 64, whiteScore: 0, status: "completed" },
      ],
    },
  ];
  let request;
  const adapter = loadAdapter(memoryStateFetch(), {}, (payload) => {
    request = plain(payload);
    return pappCResponse(payload);
  });
  const context = {
    round: 2,
    roundCount: 3,
    preliminaryRoundCount: 3,
    candidatePlayers: players,
    checkedInPlayers: players,
    tournamentParameters: { hasSemifinalAndFinal: false, brightwellConstant: 6 },
    state: {
      players,
      tournamentParameters: { hasSemifinalAndFinal: false, brightwellConstant: 6 },
      scoreHelper: {
        pappWorkfileId: "round-standings-contract",
        preliminaryRoundCount: 3,
        roundCount: 3,
        rounds,
      },
    },
  };

  const result = plain(await adapter.getRoundStandings(context));
  const directResponse = await pappCResponse(request);
  const directCResult = plain(await directResponse.json());
  assert.deepEqual(result, directCResult,
    "the adapter must return the actual PAPP C response unchanged without recomputing ranks or points");
  assert.equal(request.operation, "round-standings");
  assert.equal(request.round, 2);
  assert.equal(request.preliminaryRoundCount, 3);
  assert.equal(request.pappWorkfileId, "round-standings-contract");
  assert.equal(request.rounds.length, 3);
  assert.equal(request.rounds[0].pairings[0].blackScore, 64);
  assert.equal(request.rounds[1].pairings[0].blackId, "a");
  assert.deepEqual(request.tournamentParameters, {
    hasSemifinalAndFinal: false,
    brightwellConstant: 6,
  });
  assert.equal(result.source, "papp-c");
  assert.equal(result.round, 2);
  assert.equal(result.complete, true);
  assert.equal(result.progress.complete, false);
  assert.equal(result.standings.length, 4);
  assert.equal(result.standings.find((row) => row.playerId === "a").pointsHalfUnits, 4);

  const invalidRange = plain(await adapter.getRoundStandings({ ...context, round: 4 }));
  assert.equal(request.round, 4,
    "the adapter must leave range validation to PAPP C instead of clamping the request");
  assert.equal(invalidRange.ok, false);
  assert.equal(invalidRange.code, "invalid-round-index");
});

test("getCandidates reads the persisted roster and retains identity fields", async () => {
  const persisted = [makePlayer("saved-1", true), makePlayer("saved-2", false)];
  const adapter = loadAdapter(memoryStateFetch({
    version: 2,
    step: "checkin",
    players: persisted,
  }));
  const result = plain(await adapter.getCandidates({
    candidatePlayers: [makePlayer("request-only", true)],
  }));

  assert.equal(result.ok, true, JSON.stringify(result));
  assert.deepEqual(result.candidatePlayers, persisted);
  assert.equal(result.candidatePlayers.some((player) => player.id === "request-only"), false);
  assert.deepEqual(result.candidatePlayers.map((player) => ({
    displayName: player.displayName,
    platform: player.platform,
    account: player.account,
    checkedIn: player.checkedIn,
  })), persisted.map((player) => ({
    displayName: player.displayName,
    platform: player.platform,
    account: player.account,
    checkedIn: player.checkedIn,
  })));
});

test("syncCandidates is idempotent and preserves repeated names as separate entries", async () => {
  const fetcher = memoryStateFetch();
  const adapter = loadAdapter(fetcher);
  const candidates = [
    { ...makePlayer("same-1", true), displayName: "同名", account: "account-a" },
    { ...makePlayer("same-2", false), displayName: "同名", account: "account-b" },
    { ...makePlayer("same-3", true), displayName: "同名", account: "" },
  ];
  const context = {
    candidatePlayers: candidates,
    checkedInPlayers: candidates.filter((player) => player.checkedIn),
  };

  const first = plain(await adapter.syncCandidates(context));
  const second = plain(await adapter.syncCandidates(context));
  const readback = plain(await adapter.getCandidates({}));

  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  assert.equal(fetcher.getWriteCount(), 1);
  assert.equal(readback.candidatePlayers.length, 3);
  assert.deepEqual(readback.candidatePlayers.map((player) => player.displayName), ["同名", "同名", "同名"]);
  assert.deepEqual(readback.candidatePlayers.map((player) => player.checkedIn), [true, false, true]);
  assert.deepEqual(readback.candidatePlayers.map((player) => player.account), ["account-a", "account-b", ""]);
});

test("mapping-only sync updates an unchecked PAPP player by stable candidate id and is idempotent", async () => {
  const candidate = makePlayer("mapped-1", false);
  const oldRecord = {
    ...candidate,
    name: "旧报名名",
    country: "old-account",
    rating: 1742,
    customData: { keep: true },
  };
  const fetcher = memoryStateFetch({
    version: 2,
    step: "checkin",
    players: [candidate],
    pappPlayers: [oldRecord],
    scoreHelper: { rounds: [{ round: 1, marker: "keep-history" }] },
  });
  const adapter = loadAdapter(fetcher);
  const mappingPlayers = [{
    mappingRowId: "mapping-1",
    candidatePlayerId: "mapped-1",
    name: "新报名名",
    country: "new-account",
  }];
  const context = {
    candidatePlayers: [candidate],
    checkedInPlayers: [],
    mappingPlayers,
  };

  const first = plain(await adapter.syncCandidates(context));
  const firstState = fetcher.getState();
  const second = plain(await adapter.syncCandidates(context));
  const secondState = fetcher.getState();

  assert.deepEqual(first, { ok: true, candidateCount: 1, checkedInCount: 0 });
  assert.deepEqual(second, first);
  assert.equal(fetcher.getWriteCount(), 1, "mapping-only change is written once");
  assert.deepEqual(firstState.players, [candidate], "candidate sign-in state is unchanged");
  assert.deepEqual(firstState.scoreHelper, { rounds: [{ round: 1, marker: "keep-history" }] });
  assert.deepEqual(firstState.pappPlayers, [{
    ...oldRecord,
    name: "新报名名",
    country: "new-account",
  }]);
  assert.deepEqual(secondState.pappPlayers, firstState.pappPlayers);
});

test("omitting a mapping row stops updates without deleting the PAPP player record", async () => {
  const candidate = makePlayer("mapped-2", true);
  const fetcher = memoryStateFetch({
    version: 2,
    step: "checkin",
    players: [candidate],
    pappPlayers: [{ ...candidate, name: "保留姓名", country: "保留账号", score: 9 }],
  });
  const adapter = loadAdapter(fetcher);

  const result = plain(await adapter.syncCandidates({
    candidatePlayers: [candidate],
    checkedInPlayers: [candidate],
    mappingPlayers: [],
  }));

  assert.equal(result.ok, true);
  assert.equal(fetcher.getWriteCount(), 0);
  assert.deepEqual(fetcher.getState().pappPlayers, [{
    ...candidate,
    name: "保留姓名",
    country: "保留账号",
    score: 9,
  }]);
});

test("mapped PAPP names are used for new pairings while check-in accounts remain intact", async () => {
  const candidates = [makePlayer("mapped-a", true), makePlayer("mapped-b", true)];
  const mappingPlayers = [
    { mappingRowId: "row-a", candidatePlayerId: "mapped-a", name: "报名姓名甲", country: "oq-a" },
    { mappingRowId: "row-b", candidatePlayerId: "mapped-b", name: "报名姓名乙", country: "oq-b" },
  ];
  const adapter = loadAdapter(memoryStateFetch());

  const synced = plain(await adapter.syncCandidates({
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    mappingPlayers,
  }));
  const result = plain(await adapter.importPairings({
    round: 1,
    mode: "start-score-registration",
    rosterSource: "checkin",
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    mappingPlayers,
  }));

  assert.equal(synced.ok, true);
  assert.equal(result.ok, true);
  assert.deepEqual(
    result.pairings.flatMap((pairing) => [pairing.black, pairing.white]).sort(),
    ["报名姓名乙", "报名姓名甲"].sort(),
  );
  assert.deepEqual(
    result.pairings.flatMap((pairing) => [pairing.blackAccount, pairing.whiteAccount]).sort(),
    ["account-mapped-a", "account-mapped-b"].sort(),
  );
});

test("ambiguous mapping entries are rejected without guessing or writing", async () => {
  const first = { ...makePlayer("duplicate-id", true), account: "account-a" };
  const second = { ...makePlayer("duplicate-id", true), account: "account-b" };
  const fetcher = memoryStateFetch();
  const adapter = loadAdapter(fetcher);

  const result = plain(await adapter.syncCandidates({
    candidatePlayers: [first, second],
    checkedInPlayers: [first, second],
    mappingPlayers: [{
      mappingRowId: "mapping-ambiguous",
      candidatePlayerId: "duplicate-id",
      name: "不能猜",
      country: "account-a",
    }],
  }));

  assert.equal(result.ok, false);
  assert.match(result.message, /唯一关联/);
  assert.equal(fetcher.getWriteCount(), 0);
});

test("syncCandidates waits until an accepted delayed write is visible on readback", async () => {
  let state = { version: 2, step: "checkin", players: [makePlayer("old", true)] };
  const fetcher = async (_url, options = {}) => {
    if (options.method === "GET") return jsonResponse(200, { ok: true, state: plain(state) });
    const payload = JSON.parse(options.body);
    assert.equal(payload.source, "script");
    assert.equal(payload.operation, "sync-candidates");
    setTimeout(() => {
      state = { ...state, players: plain(payload.candidatePlayers) };
    }, 50);
    return jsonResponse(202, { ok: true, queued: true, retryAfterMs: 50 });
  };
  const adapter = loadAdapter(fetcher);
  const candidates = [makePlayer("new", true)];
  let finished = false;
  const resultPromise = adapter.syncCandidates({
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
  }).then((result) => {
    finished = true;
    return result;
  });

  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(finished, false);
  const result = plain(await resultPromise);
  assert.equal(result.ok, true);
  assert.deepEqual(state.players, candidates);
});

test("syncCandidates waits for a queued mapping-only change to reach PAPP records", async () => {
  const candidate = makePlayer("delayed-map", false);
  let state = {
    version: 2,
    step: "checkin",
    players: [candidate],
    pappPlayers: [{ ...candidate, name: "旧姓名", country: "old-account" }],
  };
  const mappingPlayers = [{
    mappingRowId: "delayed-row",
    candidatePlayerId: "delayed-map",
    name: "延迟新姓名",
    country: "new-account",
  }];
  const fetcher = async (_url, options = {}) => {
    if (options.method === "GET") return jsonResponse(200, { ok: true, state: plain(state) });
    const payload = JSON.parse(options.body);
    assert.deepEqual(payload.mappingPlayers, mappingPlayers);
    setTimeout(() => {
      state = {
        ...state,
        pappPlayers: [{ ...state.pappPlayers[0], name: "延迟新姓名", country: "new-account" }],
      };
    }, 60);
    return jsonResponse(202, { ok: true, queued: true, retryAfterMs: 60 });
  };
  const adapter = loadAdapter(fetcher);
  let finished = false;
  const resultPromise = adapter.syncCandidates({
    candidatePlayers: [candidate],
    checkedInPlayers: [],
    mappingPlayers,
  }).then((result) => {
    finished = true;
    return result;
  });

  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(finished, false, "unchanged candidate rows do not hide a pending mapping write");
  const result = plain(await resultPromise);
  assert.equal(result.ok, true);
  assert.equal(state.pappPlayers[0].name, "延迟新姓名");
});

test("candidate roster read and write failures return clear errors", async () => {
  const adapter = loadAdapter(async () => jsonResponse(503, {
    ok: false,
    error: "shared state unavailable",
  }));
  const candidates = [makePlayer("failed", true)];

  const read = plain(await adapter.getCandidates({ candidatePlayers: candidates }));
  const sync = plain(await adapter.syncCandidates({
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
  }));
  assert.equal(read.ok, false);
  assert.match(read.message, /shared state unavailable/);
  assert.equal(sync.ok, false);
  assert.match(sync.message, /shared state unavailable/);
});

test("syncCandidates keeps all five candidates while round one uses only three checked-in players", async () => {
  const adapter = loadAdapter();
  const candidates = [
    makePlayer(1, true),
    makePlayer(2, true),
    makePlayer(3, true),
    makePlayer(4, false),
    makePlayer(5, false),
  ];
  const oldHistory = [{
    round: 1,
    pairings: [{ table: 1, black: "历史选手", white: "旧对手", status: "completed", blackScore: 32, whiteScore: 32 }],
  }];
  const state = { players: candidates, scoreHelper: { rounds: oldHistory } };

  const synced = plain(await adapter.syncCandidates({
    candidatePlayers: candidates,
    candidatePlayerCount: candidates.length,
    checkedInPlayers: candidates.filter((player) => player.checkedIn === true),
    checkedInPlayerCount: 3,
    state,
  }));
  assert.deepEqual(synced, { ok: true, candidateCount: 5, checkedInCount: 3 });
  const syncedFromState = plain(await adapter.syncCandidates({ state }));
  assert.deepEqual(syncedFromState, { ok: true, candidateCount: 5, checkedInCount: 3 });

  const result = plain(await adapter.importPairings({
    round: 1,
    mode: "start-score-registration",
    rosterSource: "checkin",
  }));
  assert.equal(result.ok, true);
  assert.equal(result.pairings.length, 2);
  const ids = result.pairings.flatMap((pairing) => [
    pairing.metadata.papp.blackPlayerId,
    pairing.metadata.papp.whitePlayerId,
  ]).filter(Boolean).sort();
  assert.deepEqual(ids, ["1", "2", "3"]);
  assert.equal(result.pairings.some((pairing) => pairing.black === "选手4" || pairing.white === "选手4"), false);
  assert.equal(result.pairings.some((pairing) => pairing.black === "选手5" || pairing.white === "选手5"), false);
  assert.ok(["1", "2", "3"].includes(
    result.pairings.find((pairing) => pairing.status === "bye").metadata.papp.blackPlayerId,
  ), "the native C algorithm chooses one checked-in player for the Bye");
  assert.deepEqual(state.scoreHelper.rounds, oldHistory);
  const regularPairing = result.pairings.find((pairing) => pairing.status !== "bye");
  assert.ok(regularPairing.blackAccount.startsWith("account-"));
  assert.ok(regularPairing.whiteAccount.startsWith("account-"));
});

test("later-round native C pairings include only checked-in candidates", async () => {
  const adapter = loadAdapter();
  const candidates = [
    makePlayer("a", true),
    makePlayer("b", true),
    makePlayer("c", true),
    makePlayer("d", true),
    makePlayer("not-checked-in-1", false),
    makePlayer("not-checked-in-2", false),
  ];
  const checkedInPlayers = candidates.filter((player) => player.checkedIn);
  const preliminaryPairings = [
    { id: "r1-1", blackId: "a", whiteId: "b", blackScore: 40, whiteScore: 24, status: "completed" },
    { id: "r1-2", blackId: "c", whiteId: "d", blackScore: 40, whiteScore: 24, status: "completed" },
  ];
  const result = plain(await adapter.importPairings({
    round: 2,
    preliminaryRoundCount: 2,
    roundCount: 2,
    rosterSource: "checkin",
    candidatePlayers: candidates,
    checkedInPlayers,
    state: { players: candidates, scoreHelper: { rounds: [{ round: 1, pairings: preliminaryPairings }] } },
  }));

  assert.equal(result.ok, true);
  assert.equal(result.pairings.length, 2);
  const ids = result.pairings.flatMap((pairing) => [
    pairing.metadata.papp.blackPlayerId,
    pairing.metadata.papp.whitePlayerId,
  ]).sort();
  assert.deepEqual(ids, ["a", "b", "c", "d"]);
  assert.equal(result.pairings.some((pairing) =>
    pairing.black.startsWith("选手not-checked-in") || pairing.white.startsWith("选手not-checked-in")), false);
});

test("Brightwell configuration and playoff seeds are returned by PAPP C", async () => {
  const candidates = ["A", "B", "C", "D", "E"].map((id) => ({
    ...makePlayer(id, true),
    displayName: id,
  }));
  const historicalPairing = (black, white, blackScore, whiteScore, status = "completed") => ({
    black,
    white,
    blackScore,
    whiteScore,
    status,
    metadata: { papp: { blackPlayerId: black, whitePlayerId: white } },
  });
  const rounds = [
    {
      pairings: [
        historicalPairing("A", "B", 40, 24),
        historicalPairing("C", "D", 40, 24),
        historicalPairing("E", "", 1, 0, "bye"),
      ],
    },
    {
      pairings: [
        historicalPairing("A", "C", 40, 24),
        historicalPairing("B", "E", 35, 29),
        historicalPairing("D", "", 1, 0, "bye"),
      ],
    },
  ];
  const context = (stateParameters, tournamentParameters) => ({
    round: 3,
    roundCount: 2,
    preliminaryRoundCount: 2,
    rosterSource: "checkin",
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    tournamentParameters,
    state: {
      players: candidates,
      tournamentParameters: stateParameters,
      scoreHelper: { rounds },
    },
  });
  const adapter = loadAdapter();
  const defaultContext = context({}, undefined);
  const zeroContext = context({
    hasSemifinalAndFinal: true,
    brightwellConstant: 0,
  }, undefined);
  const defaultStandings = plain(await adapter.getPreliminaryStandings(defaultContext));
  const zeroStandings = plain(await adapter.getPreliminaryStandings(zeroContext));
  const defaultResult = plain(await adapter.importPairings(defaultContext));
  const zeroResult = plain(await adapter.importPairings(zeroContext));
  const assertSeedsMatchC = (result, standings) => {
    assert.equal(result.source, "papp-c");
    const expected = [
      [standings.standings[0].playerId, standings.standings[3].playerId],
      [standings.standings[1].playerId, standings.standings[2].playerId],
    ].map((pair) => pair.sort().join("/"));
    const actual = result.pairings.map((pairing) => [pairing.blackId, pairing.whiteId]
      .sort().join("/"));
    assert.deepEqual(actual, expected);
  };
  assertSeedsMatchC(defaultResult, defaultStandings);
  assertSeedsMatchC(zeroResult, zeroStandings);
  assert.ok(defaultStandings.standings.every((row) => Number.isFinite(row.brightwell)));
  assert.ok(zeroStandings.standings.every((row) => Number.isFinite(row.brightwell)));
});

test("fractional Brightwell settings affect standings and context parameters take precedence", async () => {
  const candidates = "ABCDEFGH".split("").map((id) => ({
    ...makePlayer(id, true),
    displayName: id,
  }));
  const historicalPairing = (black, white, blackScore, whiteScore) => ({
    black,
    white,
    blackScore,
    whiteScore,
    status: "completed",
    metadata: { papp: { blackPlayerId: black, whitePlayerId: white } },
  });
  const rounds = [
    {
      pairings: [
        historicalPairing("C", "F", 3, 61),
        historicalPairing("H", "A", 63, 1),
        historicalPairing("D", "G", 61, 3),
        historicalPairing("B", "E", 31, 33),
      ],
    },
    {
      pairings: [
        historicalPairing("D", "G", 50, 14),
        historicalPairing("E", "H", 50, 14),
        historicalPairing("B", "F", 3, 61),
        historicalPairing("A", "C", 21, 43),
      ],
    },
    {
      pairings: [
        historicalPairing("B", "E", 54, 10),
        historicalPairing("G", "C", 59, 5),
        historicalPairing("F", "A", 56, 8),
        historicalPairing("D", "H", 15, 49),
      ],
    },
    {
      pairings: [
        historicalPairing("D", "F", 55, 9),
        historicalPairing("H", "C", 13, 51),
        historicalPairing("B", "G", 8, 56),
        historicalPairing("E", "A", 64, 0),
      ],
    },
  ];
  const context = (constant) => ({
    round: 5,
    roundCount: 4,
    preliminaryRoundCount: 4,
    rosterSource: "checkin",
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    tournamentParameters: { hasSemifinalAndFinal: true, brightwellConstant: constant },
    state: {
      players: candidates,
      tournamentParameters: { hasSemifinalAndFinal: true, brightwellConstant: 99 },
      scoreHelper: { rounds },
    },
  });
  const adapter = loadAdapter();
  const integerContext = context(2);
  const fractionalContext = context(2.5);
  const integerStandings = plain(await adapter.getPreliminaryStandings(integerContext));
  const fractionalStandings = plain(await adapter.getPreliminaryStandings(fractionalContext));
  const integerResult = plain(await adapter.importPairings(integerContext));
  const fractionalResult = plain(await adapter.importPairings(fractionalContext));
  const assertSeedsMatchC = (result, standings) => {
    assert.equal(result.source, "papp-c");
    const expected = [
      [standings.standings[0].playerId, standings.standings[3].playerId],
      [standings.standings[1].playerId, standings.standings[2].playerId],
    ].map((pair) => pair.sort().join("/"));
    const actual = result.pairings.map((pairing) => [pairing.blackId, pairing.whiteId]
      .sort().join("/"));
    assert.deepEqual(actual, expected);
  };
  assertSeedsMatchC(integerResult, integerStandings);
  assertSeedsMatchC(fractionalResult, fractionalStandings);
  assert.ok(fractionalStandings.standings.every((row) => Number.isFinite(row.brightwell)));
});

test("playoffs pair the top four, advance semifinal winners, and send tied matches to the higher seed", async () => {
  const candidates = ["a", "b", "c", "d"].map((id) => makePlayer(id, true));
  const preliminaryPairings = [
    { id: "pre-a-b", blackId: "a", whiteId: "b", blackScore: 64, whiteScore: 0, status: "completed" },
    { id: "pre-c-d", blackId: "c", whiteId: "d", blackScore: 64, whiteScore: 0, status: "completed" },
  ];
  const adapter = loadAdapter();
  const semifinalContext = {
    round: 2,
    roundCount: 1,
    preliminaryRoundCount: 1,
    rosterSource: "checkin",
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    tournamentParameters: { hasSemifinalAndFinal: true, brightwellConstant: 6 },
    state: {
      players: candidates,
      scoreHelper: { rounds: [{ round: 1, pairings: preliminaryPairings }] },
    },
  };
  const preliminaryStandings = plain(await adapter.getPreliminaryStandings(semifinalContext));
  const semifinalResult = plain(await adapter.importPairings(semifinalContext));
  assert.equal(semifinalResult.ok, true);
  assert.equal(semifinalResult.source, "papp-c");
  assert.deepEqual(semifinalResult.pairings.map((pairing) => [pairing.blackId, pairing.whiteId]
    .sort().join("/")), [
    [preliminaryStandings.standings[0].playerId, preliminaryStandings.standings[3].playerId]
      .sort().join("/"),
    [preliminaryStandings.standings[1].playerId, preliminaryStandings.standings[2].playerId]
      .sort().join("/"),
  ]);

  const semifinalPairings = semifinalResult.pairings.map((pairing, index) => ({
    ...pairing,
    status: "completed",
    blackScore: index === 0 ? 40 : 32,
    whiteScore: index === 0 ? 24 : 32,
  }));
  const finalContext = {
    ...semifinalContext,
    round: 3,
    state: {
      ...semifinalContext.state,
      playoffRegistration: { semifinalPairings },
    },
  };
  const finalResult = plain(await adapter.refreshRound(finalContext));

  assert.equal(finalResult.ok, true);
  assert.deepEqual(finalResult.pairings.map((pairing) => pairing.metadata.papp.phase), [
    "final",
    "third-place",
  ]);
  const rankById = Object.fromEntries(preliminaryStandings.standings.map((player) => [player.playerId, player.rank]));
  const winners = semifinalPairings.map((pairing) => pairing.blackScore === pairing.whiteScore
    ? (rankById[pairing.blackId] < rankById[pairing.whiteId] ? pairing.blackId : pairing.whiteId)
    : pairing.blackScore > pairing.whiteScore ? pairing.blackId : pairing.whiteId);
  const losers = semifinalPairings.map((pairing, index) =>
    pairing.blackId === winners[index] ? pairing.whiteId : pairing.blackId);
  assert.deepEqual(finalResult.pairings.map((pairing) => [pairing.blackId, pairing.whiteId]
    .sort().join("/")), [
    winners.slice().sort().join("/"),
    losers.slice().sort().join("/"),
  ]);
});

test("final and third-place pairings are withheld until both semifinal scores are registered", async () => {
  const candidates = ["a", "b", "c", "d"].map((id) => makePlayer(id, true));
  const preliminaryPairings = [
    { id: "pre-a-b", blackId: "a", whiteId: "b", blackScore: 64, whiteScore: 0, status: "completed" },
    { id: "pre-c-d", blackId: "c", whiteId: "d", blackScore: 64, whiteScore: 0, status: "completed" },
  ];
  const semifinalPairing = (black, white) => ({
    black: `选手${black}`,
    white: `选手${white}`,
    blackScore: null,
    whiteScore: null,
    status: "imported",
    metadata: {
      papp: { blackPlayerId: black, whitePlayerId: white },
      playerIds: { black, white },
    },
  });
  const result = plain(await loadAdapter().importPairings({
    round: 3,
    roundCount: 1,
    preliminaryRoundCount: 1,
    rosterSource: "checkin",
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    tournamentParameters: { hasSemifinalAndFinal: true, brightwellConstant: 6 },
    state: {
      players: candidates,
      scoreHelper: { rounds: [{ round: 1, pairings: preliminaryPairings }] },
      playoffRegistration: {
        semifinalPairings: [semifinalPairing("a", "d"), semifinalPairing("b", "c")],
      },
    },
  }));

  assert.equal(result.ok, false);
  assert.equal(result.code, "semifinal-results-incomplete");
});

test("disabling semifinals and finals blocks new post-preliminary pairings", async () => {
  const candidates = ["a", "b", "c", "d"].map((id) => makePlayer(id, true));
  const context = {
    round: 2,
    roundCount: 1,
    preliminaryRoundCount: 1,
    rosterSource: "checkin",
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    tournamentParameters: { hasSemifinalAndFinal: false, brightwellConstant: 6 },
    pairingSource: [{ table: 1, black: "a", white: "b" }],
    state: { players: candidates, scoreHelper: { rounds: [{ pairings: [] }] } },
  };
  const adapter = loadAdapter();

  const imported = plain(await adapter.importPairings(context));
  const refreshed = plain(await adapter.refreshRound(context));

  assert.equal(imported.ok, false);
  assert.equal(imported.code, "playoffs-disabled");
  assert.equal(refreshed.ok, false);
  assert.equal(refreshed.code, "playoffs-disabled");
});

test("mapping rows supply missing OQ accounts without reading UI state", async () => {
  const adapter = loadAdapter();
  const candidates = [makePlayer("a", true), makePlayer("b", true)].map((player) => ({
    ...player,
    account: "",
  }));
  const context = {
    round: 1,
    rosterSource: "checkin",
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    state: {
      players: candidates,
      mapping: {
        rows: [
          { registrationNick: "选手a", oqAccount: "oq-a" },
          { registrationNick: "选手b", oqAccount: "oq-b" },
        ],
      },
    },
  };

  const result = plain(await adapter.importPairingsFromText(
    JSON.stringify({ pairings: [{ table: 1, black: "oq-a", white: "oq-b" }] }),
    context,
  ));
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.pairings[0].black, "选手a");
  assert.equal(result.pairings[0].white, "选手b");
  assert.equal(result.pairings[0].blackAccount, "oq-a");
  assert.equal(result.pairings[0].whiteAccount, "oq-b");
});

test("OQ polling uses mappings without a group nickname and includes earlier tournament rounds", async () => {
  let requestPayload = null;
  const adapter = loadAdapter(async (url, options = {}) => {
    if (url !== "/api/papp/oq/poll") return jsonResponse(200, { ok: true, state: {} });
    requestPayload = JSON.parse(options.body || "{}");
    return jsonResponse(200, {
      ok: true,
      source: "papp-c",
      round: requestPayload.round,
      ready: [],
      pending: [],
      skipped: [],
      gameAvailable: [],
    });
  });
  const pairing = (id, table, blackId, whiteId, black, white, blackAccount, whiteAccount) => ({
    id,
    table,
    blackId,
    whiteId,
    black,
    white,
    blackAccount,
    whiteAccount,
    status: "imported",
  });
  const r1 = pairing("r1-t1", 1, "p1", "p2", "旧名甲", "旧名乙", "stale-a", "stale-b");
  const r2 = pairing("r2-t1", 1, "p1", "p2", "旧名甲", "旧名乙", "stale-a", "stale-b");
  const semifinal = pairing("semi-t1", 1, "p3", "p4", "旧名丙", "旧名丁", "stale-c", "stale-d");
  const placement = pairing("placement-t1", 1, "p5", "p6", "旧名戊", "旧名己", "stale-e", "stale-f");
  const rows = [
    ["p1", "报名甲", "oq-a"],
    ["p2", "报名乙", "oq-b"],
    ["p3", "报名丙", "oq-c"],
    ["p4", "报名丁", "oq-d"],
    ["p5", "报名戊", "oq-e"],
    ["p6", "报名己", "oq-f"],
  ].map(([checkinPlayerId, registrationNick, oqAccount]) => ({
    checkinPlayerId,
    registrationNick,
    oqAccount,
    wechatNick: "",
  }));
  const state = {
    mapping: { rows },
    scoreHelper: {
      preliminaryRoundCount: 2,
      rounds: [
        { round: 1, roundStartAt: "2026-09-12T09:00:00Z", pairings: [r1] },
        { round: 2, roundStartAt: "2026-09-12T10:00:00Z", pairings: [r2] },
      ],
    },
    playoffRegistration: {
      semifinalRoundStartAt: "2026-09-12T11:00:00Z",
      placementRoundStartAt: "2026-09-12T12:00:00Z",
      semifinalPairings: [semifinal],
      placementPairings: [placement],
    },
  };

  const result = plain(await adapter.pollOqRound({
    round: 4,
    stage: "placement",
    roundStartAt: "2026-09-12T12:00:00Z",
    roundData: { stage: "placement", pairings: [placement] },
    pairings: [placement],
    state,
  }));

  assert.equal(result.source, "papp-c");
  assert.deepEqual(requestPayload.egRounds.map((group) => [group.round, group.stage]), [
    [1, "preliminary"],
    [2, "preliminary"],
    [3, "semifinal"],
    [4, "placement"],
  ]);
  assert.deepEqual(requestPayload.egRounds.map((group) => [
    group.pairings[0].black,
    group.pairings[0].blackAccount,
  ]), [
    ["报名甲", "oq-a"],
    ["报名甲", "oq-a"],
    ["报名丙", "oq-c"],
    ["报名戊", "oq-e"],
  ]);

  state.tournamentParameters = { hasSemifinalAndFinal: true, skipSemifinal: true };
  await adapter.pollOqRound({
    round: 3,
    stage: "placement",
    roundStartAt: "2026-09-12T12:00:00Z",
    roundData: { stage: "placement", pairings: [placement] },
    pairings: [placement],
    state,
  });
  assert.deepEqual(requestPayload.egRounds.map((group) => [group.round, group.stage]), [
    [1, "preliminary"],
    [2, "preliminary"],
    [3, "placement"],
  ]);
  assert.equal(requestPayload.egRounds[1].roundData.roundEndAt, "2026-09-12T12:00:00Z");
});

test("legacy JS pairings remain read-only history after a candidate is removed", async () => {
  const adapter = loadAdapter();
  const currentCandidates = [makePlayer("b", true), makePlayer("c", true), makePlayer("d", true)];
  const historicalRounds = [{
    round: 1,
    pairings: [{
      id: "historic-1",
      table: 1,
      black: "选手removed",
      white: "选手b",
      blackScore: 40,
      whiteScore: 24,
      status: "completed",
      source: "papp-adapter",
      metadata: { papp: { blackPlayerId: "removed", whitePlayerId: "b" } },
    }],
  }];
  const state = {
    players: currentCandidates,
    scoreHelper: { rounds: historicalRounds },
  };
  const before = JSON.stringify(state);
  const result = plain(await adapter.importPairings({
    round: 1,
    rosterSource: "checkin",
    state,
    candidatePlayers: currentCandidates,
    checkedInPlayers: currentCandidates,
    roundData: historicalRounds[0],
  }));

  assert.equal(result.ok, true);
  assert.equal(result.source, "legacy-history");
  assert.equal(result.readOnly, true);
  assert.deepEqual(result.pairings, historicalRounds[0].pairings);
  assert.equal(JSON.stringify(state), before);
  assert.deepEqual(state.scoreHelper.rounds, historicalRounds);
});

test("no checked-in players produces no first-round pairing or OQ pending/score", async () => {
  const requests = [];
  const stateFetch = memoryStateFetch();
  const adapter = loadAdapter(async (url, options = {}) => {
    if (url === "/api/state") return stateFetch(url, options);
    requests.push(JSON.parse(options.body));
    const response = await pollLocalOqRound(JSON.parse(options.body));
    return jsonResponse(response.ok === false ? 400 : 200, response);
  });
  const candidates = [makePlayer(1, false), makePlayer(2, false)];
  await adapter.syncCandidates({ candidatePlayers: candidates });

  const pairings = plain(await adapter.importPairings({
    round: 1,
    mode: "start-score-registration",
    rosterSource: "checkin",
    candidatePlayers: candidates,
    checkedInPlayers: [],
    state: { players: candidates, scoreHelper: { rounds: [] } },
  }));
  assert.equal(pairings.ok, false);
  assert.equal(pairings.code, "checked-in-players-missing");
  assert.equal(pairings.pairings, undefined);

  const oq = plain(await adapter.pollOqRound({
    round: 1,
    roundStartAt: "2026-09-12T09:00:00.000Z",
    roundData: { pairings: [] },
    candidatePlayers: candidates,
    checkedInPlayers: [],
    state: { players: candidates, scoreHelper: { rounds: [] } },
  }));
  assert.deepEqual(oq.ready, []);
  assert.deepEqual(oq.pending, []);
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].pairings, []);
  assert.equal("blackScore" in oq, false);
  assert.equal("whiteScore" in oq, false);
});

test("local OQ endpoint suppresses stale pending when the round has no pairings", async () => {
  const result = plain(await pollLocalOqRound({
    round: 1,
    roundStartAt: "2026-09-12T09:00:00.000Z",
    roundData: {
      pairings: [],
      metadata: {
        oqPollResult: {
          ok: true,
          ready: [],
          pending: [{ table: 1, black: "旧选手", white: "旧对手" }],
          skipped: [],
        },
      },
    },
  }));
  assert.deepEqual(result, {
    ok: true,
    source: "papp-c",
    round: 1,
    ready: [],
    pending: [],
    skipped: [],
    gameAvailable: [],
  });
});

test("local OQ polling queries both mapped accounts and fetches missing detail by game id", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "papp-oq-live-transcript-test-"));
  const requests = [];
  const summary = {
    id: "live-detail-game",
    created: "2026-09-12T10:10:00+08:00",
    players: [{ id: "acct_a" }, { id: "acct_b" }],
    blackScore: 25,
    whiteScore: 39,
    status: "SCORE",
  };
  const detail = {
    created: "2026-09-12T10:10:00+08:00",
    status: "SCORE",
    position: { moves: [{ m: "f5" }] },
  };
  const result = plain(await pollLocalOqRound({
    round: 1,
    roundStartAt: "2026-09-12T10:00:00+08:00",
    pairings: [{
      id: "live-pairing",
      table: 1,
      black: "PAPP 左侧选手",
      white: "PAPP 右侧选手",
      blackAccount: "acct_b",
      whiteAccount: "acct_a",
      status: "imported",
    }],
  }, {
    dataDir,
    baseUrl: "http://oq.test",
    fetchImpl: async (url, options) => {
      requests.push({ url: new URL(url).pathname, headers: options.headers });
      if (url.endsWith("/games/reversi/acct_a.json") || url.endsWith("/games/reversi/acct_b.json")) {
        return { ok: true, json: async () => ({ games: [summary] }) };
      }
      if (url.endsWith("/game/live-detail-game.json")) {
        return { ok: true, json: async () => detail };
      }
      throw new Error(`unexpected OQ URL: ${url}`);
    },
  }));

  assert.deepEqual(requests.map((request) => request.url).sort(), [
    "/game/live-detail-game.json",
    "/games/reversi/acct_a.json",
    "/games/reversi/acct_b.json",
  ]);
  assert.ok(requests.every((request) => request.headers["User-Agent"] === "onlicheck-local-oq-client/0.1"));
  assert.equal(result.ready.length, 1);
  assert.deepEqual([result.ready[0].blackScore, result.ready[0].whiteScore], [1, 63]);
  assert.equal(result.ready[0].oqGameId, "live-detail-game");
  assert.deepEqual(result.ready[0].oqAutoAudit.game.detail.position.moves, [{ m: "f5" }]);
});

test("local OQ polling backfills missing and remapped historical transcripts as PGN", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "papp-oq-transcript-test-"));
  const previous = {
    id: "r1-t1",
    table: 1,
    black: "报名甲",
    white: "报名乙",
    blackAccount: "oq-a",
    whiteAccount: "oq-b",
    status: "imported",
    metadata: {
      gameRecord: {
        source: "oq-poll",
        gameId: "old-r1-game",
        pappBlackAccount: "old-oq-a",
        pappWhiteAccount: "oq-b",
        moves: ["f5"],
        transcript: "f5",
      },
    },
  };
  const missing = {
    id: "r2-t1",
    table: 1,
    black: "报名甲",
    white: "报名乙",
    blackAccount: "oq-a",
    whiteAccount: "oq-b",
    status: "imported",
  };
  const current = {
    id: "r3-t1",
    table: 1,
    black: "报名甲",
    white: "报名乙",
    blackAccount: "oq-a",
    whiteAccount: "oq-b",
    status: "imported",
  };
  const pairingsByRound = { 1: [previous], 2: [missing], 3: [current] };
  const cCalls = [];
  const result = plain(await pollLocalOqRound({
    round: 3,
    stage: "preliminary",
    roundStartAt: "2026-09-12T11:00:00Z",
    roundData: {
      stage: "preliminary",
      roundStartAt: "2026-09-12T11:00:00Z",
      pairings: [current],
    },
    pairings: [current],
    egRounds: [1, 2, 3].map((round) => ({
      round,
      stage: "preliminary",
      roundData: {
        roundStartAt: "2026-09-12T" + String(8 + round).padStart(2, "0") + ":00:00Z",
        roundEndAt: "2026-09-12T" + String(9 + round).padStart(2, "0") + ":00:00Z",
      },
      pairings: pairingsByRound[round],
    })),
    oqPollResult: {
      rounds: {
        1: { roundStartAt: "2026-09-12T09:00:00Z", gamesByAccount: {} },
        2: { roundStartAt: "2026-09-12T10:00:00Z", gamesByAccount: {} },
        3: { roundStartAt: "2026-09-12T11:00:00Z", gamesByAccount: {} },
      },
    },
  }, {
    dataDir,
    invokePappC: async (payload) => {
      cCalls.push(plain(payload));
      const pairing = payload.pairings[0];
      if (payload.round === 1) {
        assert.equal(pairing.blackAccount, "oq-a");
        assert.equal(pairing.metadata.gameRecord, undefined, "stale transcript is cleared after mapping change");
      }
      const gameId = "new-game-r" + payload.round;
      return {
        ok: true,
        source: "papp-c",
        round: payload.round,
        ready: [],
        pending: [],
        skipped: [],
        gameAvailable: [{
          id: pairing.id,
          table: pairing.table,
          oqGameId: gameId,
          oqGameAvailable: true,
          oqGameAvailableAudit: {
            pappBlackAccount: pairing.blackAccount,
            pappWhiteAccount: pairing.whiteAccount,
            game: {
              gameId,
              blackName: pairing.blackAccount,
              whiteName: pairing.whiteAccount,
              detail: { position: { moves: [{ m: "f5" }] } },
            },
          },
        }],
        detailRequests: [],
        queryErrors: {},
      };
    },
  }));

  assert.deepEqual(cCalls.map((call) => call.round), [1, 2, 3]);
  assert.equal(result.source, "papp-c");
  assert.equal(result.oqGameRecords.length, 3);
  assert.equal(result.historicalTranscriptCount, 2);
  const pgnDirectory = path.join(dataDir, "ega-analysis", "pgns");
  const pgnFiles = fs.readdirSync(pgnDirectory);
  assert.equal(pgnFiles.length, 3);
  assert.ok(pgnFiles.every((filename) => fs.readFileSync(path.join(pgnDirectory, filename), "utf8").includes("f5")));
});

test("OQ polling leaves papp-adapter history read-only", async () => {
  const legacy = await pollLocalOqRound({
    round: 1,
    roundStartAt: "2026-09-12T09:00:00.000Z",
    roundData: {
      pairings: [{
        id: "old-r1-t1",
        source: "papp-adapter",
        status: "completed",
        black: "旧黑方",
        white: "旧白方",
        blackScore: 40,
        whiteScore: 24,
      }],
    },
    oqPollResult: { gamesByAccount: { stale: [] } },
  });

  assert.equal(legacy.source, "legacy-history");
  assert.equal(legacy.readOnly, true);
  assert.deepEqual(legacy.ready, []);
  assert.deepEqual(legacy.pending, []);
});

test("unstable OQ results yield pending without inventing scores", async () => {
  const adapter = loadAdapter(async (_url, options = {}) => {
    const response = await pollLocalOqRound(JSON.parse(options.body));
    return jsonResponse(response.ok === false ? 400 : 200, response);
  });
  const candidates = [makePlayer(1, true), makePlayer(2, true)];
  const result = plain(await adapter.pollOqRound({
    round: 1,
    roundStartAt: "2026-09-12T09:00:00.000Z",
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    state: { players: candidates },
    roundData: {
      pairings: [{
        id: "papp-r1-t1",
        table: 1,
        black: "选手1",
        white: "选手2",
        blackAccount: "account-1",
        whiteAccount: "account-2",
        status: "imported",
      }],
    },
    oqPollResult: { ok: true },
  }));

  assert.equal(result.ready.length, 0);
  assert.equal(result.pending.length, 1);
  assert.equal(result.pending[0].pendingKind, "oq-auto");
  assert.equal("blackScore" in result.pending[0], false);
  assert.equal("whiteScore" in result.pending[0], false);
});

test("C OQ ready results preserve pairing account fields and PAPP metadata", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "papp-oq-ready-transcript-test-"));
  const adapter = loadAdapter(async (_url, options = {}) => {
    const payload = JSON.parse(options.body || "{}");
    payload.oqPollResult = {
      gamesByAccount: {
        "account-1": [{
          id: "game-1",
          created: "2026-09-12T09:10:00Z",
          black_name: "account-1",
          white_name: "account-2",
          status: "SCORE",
          black_score: 36,
          white_score: 28,
          detail: { position: { moves: [{ m: "f5" }] } },
        }],
      },
    };
    const response = await pollLocalOqRound(payload, { dataDir });
    return jsonResponse(response.ok === false ? 400 : 200, response);
  });
  const candidates = [makePlayer(1, true), makePlayer(2, true)];
  const result = plain(await adapter.pollOqRound({
    round: 1,
    roundStartAt: "2026-09-12T09:00:00.000Z",
    candidatePlayers: candidates,
    checkedInPlayers: candidates,
    state: { players: candidates },
    roundData: {
      pairings: [{
        id: "papp-r1-t1",
        table: 1,
        black: "选手1",
        white: "选手2",
        blackAccount: "account-1",
        whiteAccount: "account-2",
        status: "imported",
        metadata: {
          papp: { blackPlayerId: "1", whitePlayerId: "2" },
          gameRecord: { transcript: "f5" },
        },
      }],
    },
  }));

  assert.equal(result.source, "papp-c");
  assert.equal(result.ready.length, 1);
  assert.equal(result.ready[0].id, "papp-r1-t1");
  assert.equal(result.ready[0].blackAccount, "account-1");
  assert.equal(result.ready[0].whiteAccount, "account-2");
  assert.equal(result.ready[0].metadata.papp.blackPlayerId, "1");
  assert.equal(result.ready[0].metadata.papp.whitePlayerId, "2");
  assert.equal(result.oqGameRecords.length, 1);
  assert.equal(result.oqGameRecords[0].gameRecord.transcript, "f5");
});

test("OQ adapter preserves replay audit, game-available audits, errors, and window metadata", async () => {
  const available = {
    id: "papp-r1-t1",
    table: 1,
    oqGameAvailable: true,
    oqGameAvailableAt: 123,
    oqGameAvailableAudit: { game: { gameId: "already-registered" }, verifiedBlackScore: 63 },
  };
  const adapter = loadAdapter(async () => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify({
      ok: true,
      ready: [],
      pending: [],
      skipped: [{ table: 1, reason: "already ready" }],
      gameAvailable: [available],
      queryErrors: { account: "detail source timeout" },
      window: { startLocal: "2026-09-12 09:00:00", endLocal: "2026-09-12 09:40:00", minutes: 40 },
    }),
  }));
  const result = plain(await adapter.pollOqRound({
    round: 1,
    roundStartAt: "2026-09-12T09:00:00.000Z",
    roundData: {
      pairings: [{
        id: "papp-r1-t1",
        table: 1,
        black: "选手1",
        white: "选手2",
        blackAccount: "account-1",
        whiteAccount: "account-2",
        status: "ready",
      }],
    },
  }));

  assert.deepEqual(result.gameAvailable, [available]);
  assert.deepEqual(result.queryErrors, { account: "detail source timeout" });
  assert.deepEqual(result.window, {
    startLocal: "2026-09-12 09:00:00",
    endLocal: "2026-09-12 09:40:00",
    minutes: 40,
  });
});
