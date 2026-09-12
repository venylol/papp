"use strict";

const fs = require("node:fs");
const path = require("node:path");

const REQUIRED_COLUMNS = [
  "记录类型",
  "比赛名称",
  "数据状态",
  "名次",
  "选手编号",
  "选手",
  "详细数据JSON",
];

function textOf(value) {
  return value == null ? "" : String(value).trim();
}

function parseCsv(source) {
  const text = String(source || "").replace(/^\uFEFF/u, "");
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  let afterQuote = false;

  const finishCell = () => {
    row.push(cell);
    cell = "";
    afterQuote = false;
  };
  const finishRow = () => {
    finishCell();
    rows.push(row);
    row = [];
  };

  for (let index = 0; index < text.length; index++) {
    const character = text[index];
    if (quoted) {
      if (character === '"') {
        if (text[index + 1] === '"') {
          cell += '"';
          index++;
        } else {
          quoted = false;
          afterQuote = true;
        }
      } else {
        cell += character;
      }
      continue;
    }
    if (afterQuote && character !== "," && character !== "\r" && character !== "\n") {
      throw new Error("CSV 引号字段结束后存在多余字符");
    }
    if (character === '"') {
      if (cell.length) throw new Error("CSV 非引号字段中包含无效引号");
      quoted = true;
    } else if (character === ",") {
      finishCell();
    } else if (character === "\r" || character === "\n") {
      if (character === "\r" && text[index + 1] === "\n") index++;
      finishRow();
    } else {
      cell += character;
    }
  }
  if (quoted) throw new Error("CSV 引号字段未结束");
  if (cell.length || row.length || afterQuote) finishRow();
  if (!rows.length) throw new Error("CSV 文件为空");

  const columns = rows.shift();
  if (columns.length === 1 && !columns[0]) throw new Error("CSV 缺少表头");
  const missing = REQUIRED_COLUMNS.filter(column => !columns.includes(column));
  if (missing.length) throw new Error(`CSV 缺少列：${missing.join("、")}`);
  if (new Set(columns).size !== columns.length) throw new Error("CSV 表头包含重复列");

  return rows
    .filter(values => values.some(value => value !== ""))
    .map((values, rowIndex) => {
      if (values.length !== columns.length) {
        throw new Error(`CSV 第 ${rowIndex + 2} 行列数与表头不一致`);
      }
      return Object.fromEntries(columns.map((column, index) => [column, values[index]]));
    });
}

function safeDetailedData(row) {
  const value = textOf(row["详细数据JSON"]);
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

function rankNumber(row) {
  const value = Number(textOf(row["名次"]));
  return Number.isInteger(value) && value > 0 ? value : null;
}

function unavailableTournament(file, competitionName, reason, extra = {}) {
  return {
    file,
    competitionName: competitionName || path.basename(file, path.extname(file)),
    available: false,
    reason,
    playerCount: 0,
    selectablePlayerCount: 0,
    players: [],
    ...extra,
  };
}

function inspectTournamentCsv(file, source) {
  let rows;
  try {
    rows = parseCsv(source);
  } catch (error) {
    return unavailableTournament(file, "", `无法读取比赛存档：${error.message}`);
  }

  const competitionName = textOf(rows.find(row => textOf(row["比赛名称"]))?.["比赛名称"]);
  const standings = rows.filter(row => textOf(row["记录类型"]) === "总排名");
  if (!standings.length) {
    return unavailableTournament(file, competitionName, "没有总排名，比赛可能尚未完赛");
  }
  if (standings.some(row => textOf(row["数据状态"]) !== "轮末已完成")) {
    return unavailableTournament(file, competitionName, "总排名为暂算或缺失状态，只能调查已完赛比赛", {
      playerCount: standings.length,
    });
  }
  if (standings.some(row => rankNumber(row) === null)) {
    return unavailableTournament(file, competitionName, "正式总排名包含无效名次", {
      playerCount: standings.length,
    });
  }

  const sorted = [...standings].sort((left, right) => rankNumber(left) - rankNumber(right));
  const ranks = sorted.map(rankNumber);
  if (new Set(ranks).size !== ranks.length) {
    return unavailableTournament(file, competitionName, "正式总排名包含重复名次", {
      playerCount: standings.length,
    });
  }

  const players = sorted.map(row => {
    const details = safeDetailedData(row);
    const account = textOf(details.account || details.oqAccount);
    const playerId = textOf(row["选手编号"] || details.playerId || details.id);
    const name = textOf(row["选手"] || details.displayName || details.name);
    const missing = [];
    if (!name) missing.push("选手名");
    if (!playerId) missing.push("选手编号");
    if (!account) missing.push("OQ 账号");
    return {
      rank: rankNumber(row),
      playerId,
      name,
      account,
      selectable: missing.length === 0,
      reason: missing.length ? `缺少${missing.join("、")}` : "",
    };
  });

  return {
    file,
    competitionName: competitionName || path.basename(file, path.extname(file)),
    available: true,
    reason: "",
    playerCount: players.length,
    selectablePlayerCount: players.filter(player => player.selectable).length,
    players,
  };
}

function resolveCsvFile(directory, file) {
  const name = textOf(file);
  if (!name || name !== path.basename(name) || path.extname(name).toLowerCase() !== ".csv") {
    const error = new Error("比赛存档文件名无效");
    error.statusCode = 400;
    throw error;
  }
  const root = path.resolve(directory);
  const target = path.resolve(root, name);
  if (path.dirname(target) !== root) {
    const error = new Error("比赛存档路径越界");
    error.statusCode = 400;
    throw error;
  }
  let stat;
  try {
    stat = fs.statSync(target);
  } catch (error) {
    if (error.code === "ENOENT") {
      const notFound = new Error("比赛存档不存在");
      notFound.statusCode = 404;
      throw notFound;
    }
    throw error;
  }
  if (!stat.isFile()) {
    const error = new Error("比赛存档不是文件");
    error.statusCode = 400;
    throw error;
  }
  return { name, target, stat };
}

function readTournament(directory, file) {
  const resolved = resolveCsvFile(directory, file);
  return {
    ...inspectTournamentCsv(resolved.name, fs.readFileSync(resolved.target, "utf8")),
    modifiedAt: resolved.stat.mtime.toISOString(),
  };
}

function listTournaments(directory) {
  const root = path.resolve(directory);
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isFile() && path.extname(entry.name).toLowerCase() === ".csv")
    .map(entry => readTournament(root, entry.name))
    .sort((left, right) => right.modifiedAt.localeCompare(left.modifiedAt) ||
      left.file.localeCompare(right.file, "zh-CN"))
    .map(({ players: _players, ...summary }) => summary);
}

module.exports = {
  inspectTournamentCsv,
  listTournaments,
  parseCsv,
  readTournament,
  resolveCsvFile,
};
