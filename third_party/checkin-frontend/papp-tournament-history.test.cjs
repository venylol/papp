"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const history = require("./papp-tournament-history.js");

const COLUMNS = ["记录类型", "比赛名称", "数据状态", "名次", "选手编号", "选手", "详细数据JSON"];

function cell(value) {
  return `"${String(value == null ? "" : value).replace(/"/g, '""')}"`;
}

function csv(rows) {
  return "\uFEFF" + [
    COLUMNS.map(cell).join(","),
    ...rows.map(row => COLUMNS.map(column => cell(row[column])).join(",")),
  ].join("\r\n") + "\r\n";
}

function completed(rank, id, name, details = {}) {
  return {
    "记录类型": "总排名",
    "比赛名称": '秋季,"新人"\n赛',
    "数据状态": "轮末已完成",
    "名次": rank,
    "选手编号": id,
    "选手": name,
    "详细数据JSON": JSON.stringify(details),
  };
}

test("parses quoted UTF-8 CSV and returns sorted official standings", () => {
  const source = csv([
    completed(2, "p2", '乙,"二"\n行', { account: "beta", oqAccount: "ignored" }),
    completed(1, "p1", "甲", { account: "alpha" }),
    completed(3, "p3", "丙", {}),
  ]);
  const tournament = history.inspectTournamentCsv("秋季赛.csv", source);
  assert.equal(tournament.competitionName, '秋季,"新人"\n赛');
  assert.equal(tournament.available, true);
  assert.equal(tournament.playerCount, 3);
  assert.equal(tournament.selectablePlayerCount, 2);
  assert.deepEqual(tournament.players.map(player => player.rank), [1, 2, 3]);
  assert.deepEqual(tournament.players[1], {
    rank: 2,
    playerId: "p2",
    name: '乙,"二"\n行',
    account: "beta",
    selectable: true,
    reason: "",
  });
  assert.equal(tournament.players[2].selectable, false);
  assert.equal(tournament.players[2].reason, "缺少OQ 账号");
});

test("rejects provisional, missing and structurally invalid overall standings", () => {
  const provisional = history.inspectTournamentCsv("暂算.csv", csv([
    completed(1, "p1", "甲", { account: "alpha" }),
    { ...completed(2, "p2", "乙", { account: "beta" }), "数据状态": "暂算排名（本轮未完成）" },
  ]));
  assert.equal(provisional.available, false);
  assert.match(provisional.reason, /暂算或缺失/);

  const noOverall = history.inspectTournamentCsv("未完成.csv", csv([{
    ...completed(1, "p1", "甲", { account: "alpha" }),
    "记录类型": "轮末排名",
  }]));
  assert.equal(noOverall.available, false);
  assert.match(noOverall.reason, /没有总排名/);

  const malformed = history.inspectTournamentCsv("损坏.csv", '\uFEFF"记录类型","比赛名称"\r\n"总排名","未结束');
  assert.equal(malformed.available, false);
  assert.match(malformed.reason, /无法读取比赛存档/);
});

test("scans only CSV files directly under the archive directory and blocks traversal", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "papp-history-"));
  fs.writeFileSync(path.join(directory, "正式.csv"), csv([
    completed(1, "p1", "甲", { account: "alpha" }),
  ]), "utf8");
  fs.writeFileSync(path.join(directory, "说明.txt"), "不是比赛", "utf8");
  const nested = path.join(directory, "nested");
  fs.mkdirSync(nested);
  fs.writeFileSync(path.join(nested, "不应发现.csv"), csv([
    completed(1, "p2", "乙", { account: "beta" }),
  ]), "utf8");

  const list = history.listTournaments(directory);
  assert.equal(list.length, 1);
  assert.equal(list[0].file, "正式.csv");
  assert.equal(Object.hasOwn(list[0], "players"), false);
  assert.equal(history.readTournament(directory, "正式.csv").players[0].account, "alpha");
  assert.throws(() => history.readTournament(directory, "../正式.csv"), /文件名无效/);
  assert.throws(() => history.readTournament(directory, "不存在.csv"), error => error.statusCode === 404);
});

test("HTTP APIs expose the archive list and one selected tournament", async () => {
  const originalList = history.listTournaments;
  const originalRead = history.readTournament;
  history.listTournaments = () => [{ file: "比赛.csv", competitionName: "比赛", available: true }];
  history.readTournament = (_directory, file) => ({ file, players: [{ rank: 1, account: "alpha" }] });
  const { server } = require("./local-server.js");
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const listResponse = await fetch(`${base}/api/player-investigation/tournaments`);
    assert.equal(listResponse.status, 200);
    assert.deepEqual(await listResponse.json(), {
      ok: true,
      tournaments: [{ file: "比赛.csv", competitionName: "比赛", available: true }],
    });

    const detailResponse = await fetch(`${base}/api/player-investigation/tournaments/detail?file=${encodeURIComponent("比赛.csv")}`);
    assert.equal(detailResponse.status, 200);
    assert.deepEqual(await detailResponse.json(), {
      ok: true,
      tournament: { file: "比赛.csv", players: [{ rank: 1, account: "alpha" }] },
    });
  } finally {
    history.listTournaments = originalList;
    history.readTournament = originalRead;
    await new Promise(resolve => server.close(resolve));
  }
});
