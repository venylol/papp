"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createArchive, csvCell } = require("./papp-tournament-archive.js");

// Parse quoted CSV, including embedded commas, quotes and newlines.
function readRows(file) {
  const bytes = fs.readFileSync(file);
  assert.deepEqual([...bytes.subarray(0, 3)], [0xef, 0xbb, 0xbf]);
  const text = bytes.toString("utf8").slice(1);
  const rows = [];
  let row = [], cell = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i++; }
      else quoted = !quoted;
    } else if (c === "," && !quoted) { row.push(cell); cell = ""; }
    else if (c === "\r" && text[i + 1] === "\n" && !quoted) {
      row.push(cell); rows.push(row); row = []; cell = ""; i++;
    } else cell += c;
  }
  assert.equal(quoted, false);
  const columns = rows.shift();
  return rows.map(values => {
    assert.equal(values.length, columns.length);
    return Object.fromEntries(columns.map((name, index) => [name, values[index]]));
  });
}

function fixture() {
  return {
    competitionName: '中文比赛,"存档"\n第二行',
    tournamentParameters: { hasSemifinalAndFinal: false },
    players: [
      { id: "1", displayName: "甲", checkedIn: true },
      { id: "2", displayName: "乙", checkedIn: true },
      { id: "3", displayName: "丙", checkedIn: true },
    ],
    scoreHelper: { pappWorkfileId: "archive-test", preliminaryRoundCount: 2, rounds: [
      { pairings: [
        { id: "r1-t1", table: 1, black: "甲", white: "乙", blackId: "1", whiteId: "2",
          blackAccount: "alpha", whiteAccount: "beta", blackScore: 64, whiteScore: 0,
          status: "completed", pappReadbackAt: 1, transcript: "f5d6",
          metadata: { note: '棋谱,"注释"\n下一行' } },
        { id: "r1-t2", table: 2, black: "丙", blackId: "3", white: "", status: "bye" },
      ] },
      { pairings: [{ id: "r2-t1", table: 1, black: "甲", white: "乙", blackId: "1", whiteId: "2", status: "pending" }] },
    ] },
    egAnalysis: { pairingLossByRound: { 1: { 1: {
      stage: "preliminary", pairingId: "r1-t1", blackAccount: "alpha", whiteAccount: "beta",
      players: [
        { ftdSide: "black", account: "alpha", totalLoss: 0, averageLoss: 0 },
        { ftdSide: "white", account: "beta", totalLoss: 2.5, averageLoss: 0.25 },
      ],
    } } } },
    standingsSnapshots: [{ kind: "preliminary", round: 2, source: "papp-c", standings: [{ rank: 1, name: "STALE_CACHE" }] }],
  };
}

async function main() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "papp-archive-test-"));
  const calls = [];
  const request = async (url, payload) => {
    calls.push({ url, payload });
    if (payload.operation === "round-standings" && payload.round === 2) throw new Error("测试：C 排名不可用");
    return { ok: true, source: "papp-c", operation: payload.operation, round: payload.round,
      progress: { complete: true }, stageProgress: { complete: true },
      standings: [{ rank: 7, playerId: "2", displayName: "C返回的选手", totalPoints: 9.5, brightwell: 111, totalDiscs: 43 }] };
  };
  const options = { request, directory, now: new Date("2026-09-12T01:02:03.000Z") };
  const first = await createArchive(fixture(), options);
  const rows = readRows(first.file);
  assert(rows.every(row => row["比赛名称"] === fixture().competitionName));
  const game = rows.find(row => row["配对编号"] === "r1-t1");
  assert.equal(game["白方比分"], "0");
  assert.equal(game["黑方总子损"], "0");
  assert.equal(game["黑方平均子损"], "0");
  assert.equal(game["数据状态"], "资料齐全");
  assert.equal(game["棋谱"].toLowerCase(), "f5d6");
  assert.equal(JSON.parse(game["详细数据JSON"]).metadata.note, '棋谱,"注释"\n下一行');
  const bye = rows.find(row => row["配对编号"] === "r1-t2");
  assert.equal(bye["比分状态"], "轮空");
  assert.equal(bye["棋谱"], "不适用（轮空）");
  assert.equal(bye["黑方总子损"], "不适用（轮空）");
  assert(!bye["数据状态"].includes("缺失"));
  const absent = rows.find(row => row["配对编号"] === "r2-t1");
  for (const col of ["黑方比分", "白方比分", "棋谱", "黑方总子损", "白方平均子损"]) assert.equal(absent[col], "缺失");
  const ranking = rows.find(row => row["记录类型"] === "轮末排名" && row["轮次"] === "1");
  assert.equal(ranking["名次"], "7");
  assert.equal(ranking["积分"], "9.5");
  assert.equal(ranking["选手"], "C返回的选手");
  assert.equal(ranking["排名来源"], "PAPP C");
  assert(rows.some(row => row["记录类型"] === "总排名" && row["名次"] === "7"));
  const failed = rows.find(row => row["记录类型"] === "轮末排名" && row["轮次"] === "2");
  assert.match(failed["数据状态"], /缺失.*C 排名不可用/);
  assert.equal(failed["名次"], "");
  assert(!JSON.stringify(rows).includes("STALE_CACHE"));
  assert.equal(first.missingCount, 2);
  assert.deepEqual(calls.map(call => [call.payload.operation, call.payload.round]), [
    ["round-standings", 1], ["round-standings", 2], ["overall-standings", 2],
  ]);
  assert(calls.every(call => call.url.includes("/api/papp")));

  const second = await createArchive(fixture(), options);
  assert.notEqual(second.file, first.file);
  assert.equal(path.basename(second.file), path.basename(first.file, ".csv") + "1.csv");
  assert.equal(path.dirname(first.file), directory);
  assert.equal(path.dirname(second.file), directory);
  assert(fs.existsSync(first.file) && fs.existsSync(second.file));
  assert.deepEqual(readRows(second.file), rows);

  const foreign = await createArchive(fixture(), { ...options, request: async () => ({
    ok: true, source: "javascript", standings: [{ rank: 1, name: "FAKE_JS_RANK" }],
  }) });
  assert.equal(path.basename(foreign.file), path.basename(first.file, ".csv") + "2.csv");
  const foreignRows = readRows(foreign.file).filter(row => row["记录类型"] !== "对局");
  assert.equal(foreignRows.length, 3);
  assert(foreignRows.every(row => row["数据状态"].startsWith("缺失：") && row["名次"] === ""));
  assert.equal(csvCell('中文,"引号"'), '"中文,""引号"""');
  assert.equal(csvCell("=1+1"), '"\'=1+1"');
  await testRealC(directory);
  console.log(`Archive tests passed. Temporary CSV files retained: ${directory}`);
}

async function testRealC(directory) {
  process.env.PAPP_DATA_DIR = path.join(directory, "local-service-data");
  process.env.PAPP_TOURNAMENT_WORKFILES_DIR = path.join(directory, "c-workfiles");
  const { invokePappC } = require("./local-server.js");
  const state = fixture();
  state.competitionName = "真实C两轮比赛";
  state.players = state.players.slice(0, 2);
  state.scoreHelper.rounds = [
    { pairings: [{ ...state.scoreHelper.rounds[0].pairings[0], blackScore: 40, whiteScore: 24 }] },
    { pairings: [{ id: "r2-t1", table: 1, black: "乙", white: "甲", blackId: "2", whiteId: "1",
      blackAccount: "beta", whiteAccount: "alpha", blackScore: 32, whiteScore: 32,
      status: "completed", pappReadbackAt: 1 }] },
  ];
  const results = [];
  const archive = await createArchive(state, { directory, request: async (_url, payload) => {
    const result = await invokePappC(payload);
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.source, "papp-c");
    results.push(result);
    return result;
  } });
  const rows = readRows(archive.file);
  assert.equal(path.basename(archive.file), "真实C两轮比赛.csv");
  const rankRows = rows.filter(row => row["记录类型"] !== "对局");
  assert.equal(rankRows.length, 6);
  for (const [kind, round, expected] of [
    ["轮末排名", "1", { "1": ["1", "1", "40"], "2": ["2", "0", "24"] }],
    ["轮末排名", "2", { "1": ["1", "1.5", "72"], "2": ["2", "0.5", "56"] }],
    ["总排名", "", { "1": ["1", "1.5", "72"], "2": ["2", "0.5", "56"] }],
  ]) {
    const selected = rankRows.filter(row => row["记录类型"] === kind && row["轮次"] === round);
    assert.equal(selected.length, 2);
    for (const row of selected) {
      assert.deepEqual([row["名次"], row["积分"], row["总棋子数"]], expected[row["选手编号"]]);
      assert.equal(row["排名来源"], "PAPP C");
      assert(!row["数据状态"].includes("缺失"));
    }
  }
  assert.equal(results.length, 3);
  assert.equal(results[1].standings.find(player => player.playerId === "1").totalPoints, 1.5);
}

main().catch(error => { console.error(error); process.exitCode = 1; });
