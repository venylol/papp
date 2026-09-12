"use strict";

const fs = require("node:fs");
const path = require("node:path");

const MOVE_RE = /^[a-h][1-8]$/i;
const GAME_ANALYSIS_SCHEMA = "papp-eg-game-analysis-v1";
const SUMMARY_SCHEMA = "papp-eg-analysis-v1";

function objectOf(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function normalizeText(value) {
  return String(value == null ? "" : value).replace(/\s+/g, " ").trim();
}

function accountKey(value) {
  return normalizeText(value)
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]/g, "");
}

function splitTranscript(value) {
  if (Array.isArray(value)) {
    return value.flatMap((item) => {
      if (item && typeof item === "object") {
        return splitTranscript(item.m ?? item.move ?? item.coordinate);
      }
      return splitTranscript(String(item == null ? "" : item));
    });
  }
  const text = normalizeText(value).toLowerCase();
  if (!text) return [];
  const tokens = text.split(/[\s,;]+/).filter(Boolean);
  if (tokens.length > 1) {
    return tokens
      .map((token) => token.replace(/[^a-z0-9-]/g, ""))
      .filter((token) => MOVE_RE.test(token) || token === "-");
  }
  const compact = text.replace(/[^a-z0-9-]/g, "");
  if (compact === "-") return ["-"];
  if (/^(?:[a-h][1-8]|-)+$/i.test(compact)) {
    const moves = [];
    for (let index = 0; index < compact.length;) {
      if (compact[index] === "-") {
        moves.push("-");
        index += 1;
      } else {
        moves.push(compact.slice(index, index + 2).toLowerCase());
        index += 2;
      }
    }
    return moves;
  }
  return [];
}

function movesFromOqDetail(detail) {
  const position = objectOf(objectOf(detail).position);
  return splitTranscript(position.moves);
}

function detailFromPairing(pairing) {
  const metadata = objectOf(objectOf(pairing).metadata);
  const gameRecord = objectOf(metadata.gameRecord || metadata.oqRecord);
  const audit = objectOf(metadata.oqAutoAudit);
  const availableAudit = objectOf(metadata.oqGameAvailableAudit);
  const auditGame = objectOf(audit.game || availableAudit.game);
  const candidates = [
    gameRecord.detail,
    gameRecord.gameDetail,
    gameRecord.position ? gameRecord : null,
    auditGame.detail,
    auditGame.gameDetail,
    auditGame.position ? auditGame : null,
    metadata.detail,
    metadata.oqDetail,
  ];
  return candidates.find((candidate) => objectOf(objectOf(candidate).position).moves) || null;
}

function storedTranscriptRecord(pairing) {
  const item = objectOf(pairing);
  const metadata = objectOf(item.metadata);
  const gameRecord = objectOf(metadata.gameRecord || metadata.oqRecord);
  const audit = objectOf(metadata.oqAutoAudit);
  const availableAudit = objectOf(metadata.oqGameAvailableAudit);
  const auditGame = objectOf(audit.game || availableAudit.game);
  const detail = detailFromPairing(item);
  let moves = splitTranscript(
    gameRecord.moves || gameRecord.transcript || gameRecord.oqTranscript ||
    objectOf(detail).position && objectOf(detail.position).moves,
  );
  if (!moves.length) moves = movesFromOqDetail(detail);
  if (!moves.length) {
    moves = splitTranscript(item.moves || item.transcript || metadata.transcript || metadata.oqTranscript);
  }
  const transcript = moves.filter((move) => MOVE_RE.test(move)).join("");
  const board = normalizeBoard(
    gameRecord.board || objectOf(detail).startBoard || objectOf(objectOf(detail).position).startPos ||
    metadata.board || item.board || item.position,
  );
  if (!transcript && !board) return null;
  const gameId = normalizeText(
    item.oqGameId || gameRecord.gameId || auditGame.gameId || auditGame.id || "",
  );
  return {
    gameId,
    source: normalizeText(gameRecord.source || "oq"),
    transcript,
    moves,
    board,
    side: normalizeSide(gameRecord.sideToMove || gameRecord.side || objectOf(detail).sideToMove),
    createdAt: normalizeText(gameRecord.createdAt || auditGame.createdAt || auditGame.createdLocal || ""),
    pgnFile: normalizeText(gameRecord.pgnFile || ""),
    pappBlackAccount: normalizeText(gameRecord.pappBlackAccount || audit.pappBlackAccount || availableAudit.pappBlackAccount || ""),
    pappWhiteAccount: normalizeText(gameRecord.pappWhiteAccount || audit.pappWhiteAccount || availableAudit.pappWhiteAccount || ""),
    blackAccount: normalizeText(gameRecord.pappBlackAccount || gameRecord.blackAccount || audit.pappBlackAccount || availableAudit.pappBlackAccount || item.blackAccount),
    whiteAccount: normalizeText(gameRecord.pappWhiteAccount || gameRecord.whiteAccount || audit.pappWhiteAccount || availableAudit.pappWhiteAccount || item.whiteAccount),
    actualBlackAccount: normalizeText(gameRecord.actualBlackAccount || auditGame.blackName || ""),
    actualWhiteAccount: normalizeText(gameRecord.actualWhiteAccount || auditGame.whiteName || ""),
    blackName: normalizeText(gameRecord.blackName || item.black || item.blackName),
    whiteName: normalizeText(gameRecord.whiteName || item.white || item.whiteName),
  };
}

function normalizeSide(value) {
  const side = normalizeText(value).toUpperCase();
  if (side === "O" || side === "W" || side === "WHITE") return "O";
  return "X";
}

function normalizeBoard(value) {
  const text = normalizeText(value).replace(/\s+/g, "").toUpperCase();
  if (!/^[.\-XOBW]{64}$/.test(text)) return "";
  return text.replace(/[.]/g, "-").replace(/[B]/g, "X").replace(/[W]/g, "O");
}

function scoreRoundStages(state) {
  const tournament = objectOf(state);
  const helper = objectOf(tournament.scoreHelper);
  const rounds = Array.isArray(helper.rounds) ? helper.rounds : [];
  const preliminaryCount = Math.max(
    1,
    Math.trunc(Number(helper.preliminaryRoundCount || helper.roundCount || rounds.length) || 1),
  );
  const out = rounds.map((round, index) => ({
    round: index + 1,
    stage: "preliminary",
    roundData: objectOf(round),
    pairings: Array.isArray(objectOf(round).pairings) ? round.pairings : [],
  }));
  const playoff = objectOf(tournament.playoffRegistration);
  if (Array.isArray(playoff.semifinalPairings) && playoff.semifinalPairings.length) {
    out.push({
      round: preliminaryCount + 1,
      stage: "semifinal",
      roundData: {
        roundStartAt: playoff.semifinalRoundStartAt,
        roundEndAt: playoff.semifinalRoundEndAt,
        windowMinutes: playoff.semifinalWindowMinutes,
      },
      pairings: playoff.semifinalPairings,
    });
  }
  if (Array.isArray(playoff.placementPairings) && playoff.placementPairings.length) {
    out.push({
      round: preliminaryCount + 2,
      stage: "placement",
      roundData: {
        roundStartAt: playoff.placementRoundStartAt,
        roundEndAt: playoff.placementRoundEndAt,
        windowMinutes: playoff.placementWindowMinutes,
      },
      pairings: playoff.placementPairings,
    });
  }
  return out;
}

function collectEgRecords(payload) {
  const input = objectOf(payload);
  const state = objectOf(input.state);
  const rows = scoreRoundStages(state);
  if (!rows.length && Array.isArray(input.pairings)) {
    rows.push({
      round: Math.max(1, Math.trunc(Number(input.round) || 1)),
      stage: normalizeText(input.stage || "preliminary"),
      roundData: objectOf(input.roundData),
      pairings: input.pairings,
    });
  }
  const records = [];
  for (const group of rows) {
    for (const pairing of group.pairings) {
      const item = objectOf(pairing);
      const status = normalizeText(item.status).toLowerCase();
      if (status === "bye" || !normalizeText(item.white || item.whiteName) ||
          normalizeText(item.white || item.whiteName).toLowerCase() === "bye") continue;
      const game = storedTranscriptRecord(item);
      if (!game) continue;
      records.push({
        ...game,
        round: group.round,
        stage: group.stage,
        table: normalizeText(item.table || item.pendingTable),
        pairingId: normalizeText(item.id || item.pairingId || item.sourceLocalId),
        blackName: normalizeText(item.black || item.blackName || game.blackName),
        whiteName: normalizeText(item.white || item.whiteName || game.whiteName),
        blackAccount: normalizeText(item.blackAccount || game.blackAccount),
        whiteAccount: normalizeText(item.whiteAccount || game.whiteAccount),
        resultTime: normalizeText(item.resultTime || ""),
      });
    }
  }
  return records;
}

function safePgnHeader(value) {
  return normalizeText(value).replace(/[\\"]/g, "").slice(0, 120) || "?";
}

function formatOqPgn(record) {
  const item = objectOf(record);
  const moves = Array.isArray(item.moves) ? item.moves : splitTranscript(item.transcript);
  const dateValue = normalizeText(item.createdAt || "");
  const date = /^\d{4}[-.]\d{2}[-.]\d{2}/.test(dateValue)
    ? dateValue.slice(0, 10).replace(/-/g, ".")
    : "????.??.??";
  const headers = [
    `[Event "PAPP Othello Tournament"]`,
    `[Site "Othello Quest"]`,
    `[Date "${date}"]`,
    `[Round "${safePgnHeader(item.round)}"]`,
    `[Board "${safePgnHeader(item.table)}"]`,
    `[Black "${safePgnHeader(item.actualBlackAccount || item.blackAccount)}"]`,
    `[White "${safePgnHeader(item.actualWhiteAccount || item.whiteAccount)}"]`,
    `[Result "*"]`,
    `[GameId "${safePgnHeader(item.gameId)}"]`,
  ];
  const body = [];
  let moveNo = 1;
  let pendingBlack = false;
  for (const raw of moves) {
    const move = normalizeText(raw).toLowerCase();
    if (!MOVE_RE.test(move) && move !== "-") continue;
    if (!pendingBlack) {
      body.push(`${moveNo}.`);
      pendingBlack = true;
    }
    body.push(move === "-" ? "--" : move);
    if (pendingBlack && move !== "-") {
      if (body[body.length - 2] === `${moveNo}.`) pendingBlack = true;
      else {
        pendingBlack = false;
        moveNo += 1;
      }
    }
    if (move === "-") {
      pendingBlack = false;
      moveNo += 1;
    }
  }
  body.push("*");
  return `${headers.join("\n")}\n\n${body.join(" ")}\n`;
}

class OthelloBoard {
  constructor(boardText = "", side = "X") {
    const normalized = normalizeBoard(boardText);
    this.board = normalized
      ? normalized.split("")
      : Array.from({ length: 64 }, (_, index) => {
          if (index === 27 || index === 36) return "O";
          if (index === 28 || index === 35) return "X";
          return "-";
        });
    this.current = normalizeSide(side);
    this.normalizeTurn();
  }

  opponent(color) {
    return color === "X" ? "O" : "X";
  }

  captures(row, column, color) {
    const index = row * 8 + column;
    if (row < 0 || row > 7 || column < 0 || column > 7 || this.board[index] !== "-") return [];
    const other = this.opponent(color);
    const flips = [];
    for (let dr = -1; dr <= 1; dr += 1) {
      for (let dc = -1; dc <= 1; dc += 1) {
        if (!dr && !dc) continue;
        let r = row + dr;
        let c = column + dc;
        const line = [];
        while (r >= 0 && r < 8 && c >= 0 && c < 8 && this.board[r * 8 + c] === other) {
          line.push(r * 8 + c);
          r += dr;
          c += dc;
        }
        if (line.length && r >= 0 && r < 8 && c >= 0 && c < 8 && this.board[r * 8 + c] === color) {
          flips.push(...line);
        }
      }
    }
    return flips;
  }

  legalMoves(color = this.current) {
    const out = [];
    for (let row = 0; row < 8; row += 1) {
      for (let column = 0; column < 8; column += 1) {
        if (this.captures(row, column, color).length) out.push([row, column]);
      }
    }
    return out;
  }

  normalizeTurn() {
    if (this.legalMoves(this.current).length) return;
    const other = this.opponent(this.current);
    if (this.legalMoves(other).length) this.current = other;
  }

  applyMove(moveValue) {
    const move = normalizeText(moveValue).toLowerCase();
    if (!MOVE_RE.test(move)) throw new Error(`OQ 棋谱中有无效着法：${move}`);
    this.normalizeTurn();
    const row = Number(move[1]) - 1;
    const column = move.charCodeAt(0) - 97;
    const flips = this.captures(row, column, this.current);
    if (!flips.length) throw new Error(`OQ 棋谱着法 ${move} 在当前局面不合法`);
    const playedBy = this.current;
    const index = row * 8 + column;
    this.board[index] = playedBy;
    flips.forEach((flipIndex) => { this.board[flipIndex] = playedBy; });
    this.current = this.opponent(playedBy);
    this.normalizeTurn();
    return playedBy;
  }

  isTerminal() {
    return !this.legalMoves("X").length && !this.legalMoves("O").length;
  }

  finalScores() {
    const black = this.board.filter((cell) => cell === "X").length;
    const white = this.board.filter((cell) => cell === "O").length;
    const empty = 64 - black - white;
    if (black > white) return [black + empty, white];
    if (white > black) return [black, white + empty];
    return [black + Math.floor(empty / 2), white + Math.ceil(empty / 2)];
  }

  toSetboard() {
    return `${this.board.join("")}${this.current}`;
  }
}

function parseHintOutput(output) {
  const rows = String(output || "").split(/\r?\n/).map((line) => line.trim())
    .filter((line) => line.startsWith("|"));
  if (rows.length < 2) throw new Error(`Egaroucid hint 输出无法识别：${normalizeText(output).slice(0, 160)}`);
  const columns = rows[1].split("|").slice(1, -1).map((cell) => cell.trim());
  const bestEval = Number.parseInt(String(columns[3] || "").replace(/\+/g, ""), 10);
  const bestMove = normalizeText(columns[2]).toLowerCase();
  if (!Number.isFinite(bestEval) || (!MOVE_RE.test(bestMove) && bestMove !== "pass")) {
    throw new Error(`Egaroucid hint 行无法解析：${rows[1]}`);
  }
  return { bestMove, bestEval, depth: normalizeText(columns[1]) };
}

function playerSideForAccount(account, record, fallback) {
  const key = accountKey(account);
  if (key && key === accountKey(record.blackAccount)) return "black";
  if (key && key === accountKey(record.whiteAccount)) return "white";
  return fallback;
}

async function analyzeOqRecord(recordValue, engine) {
  const record = objectOf(recordValue);
  const moves = Array.isArray(record.moves) ? record.moves : splitTranscript(record.transcript);
  const board = new OthelloBoard(record.board, record.side);
  const nodes = [];
  const sideData = {
    black: { name: normalizeText(record.actualBlackName || record.blackName), account: normalizeText(record.actualBlackAccount || record.blackAccount), losses: [], nodes: [] },
    white: { name: normalizeText(record.actualWhiteName || record.whiteName), account: normalizeText(record.actualWhiteAccount || record.whiteAccount), losses: [], nodes: [] },
  };
  await engine.setboard(board.toSetboard());
  let currentHint = await engine.hint();
  let ply = 0;
  for (let sourceMoveIndex = 0; sourceMoveIndex < moves.length; sourceMoveIndex += 1) {
    const move = moves[sourceMoveIndex];
    if (move === "-") continue;
    if (!MOVE_RE.test(move)) continue;
    board.normalizeTurn();
    const sideBefore = board.current;
    const boardBefore = board.toSetboard();
    const legalMoveCount = board.legalMoves().length;
    const playerColor = sideBefore === "X" ? "black" : "white";
    const player = sideData[playerColor];
    const actualSide = playerSideForAccount(player.account, record, playerColor);
    const bestEval = Number(currentHint.bestEval);
    if (!Number.isFinite(bestEval)) throw new Error("Egaroucid hint 缺少有效评分");

    board.applyMove(move);
    await engine.play(move);
    let actualEval;
    let nextHint = null;
    if (board.isTerminal()) {
      const [blackScore, whiteScore] = board.finalScores();
      const blackMargin = blackScore - whiteScore;
      actualEval = sideBefore === "X" ? blackMargin : -blackMargin;
    } else {
      nextHint = await engine.hint();
      const nextEval = Number(nextHint.bestEval);
      if (!Number.isFinite(nextEval)) throw new Error("Egaroucid after-move hint 缺少有效评分");
      actualEval = board.current === sideBefore ? nextEval : -nextEval;
    }
    const lossPositive = bestEval - actualEval;
    const lossClipped = Math.max(0, lossPositive);
    ply += 1;
    const node = {
      ply,
      plyGroup: Math.ceil(ply / 2),
      move,
      playerColor,
      playerName: player.name,
      playerAccount: player.account,
      ftdSide: actualSide,
      sourceMoveIndex,
      boardBefore,
      legalMoveCount,
      bestMove: normalizeText(currentHint.bestMove),
      bestEval,
      actualEval,
      lossPositive,
      lossClipped,
      lossSignedUser: actualEval - bestEval,
      engineJudge: lossClipped >= 4 ? "Mistake" : lossClipped > 0 ? "Disagree" : "",
      bestDepth: normalizeText(currentHint.depth || ""),
      nextDepth: nextHint ? normalizeText(nextHint.depth || "") : "End",
    };
    nodes.push(node);
    player.losses.push(lossClipped);
    player.nodes.push(node);
    if (nextHint) currentHint = nextHint;
  }

  const players = Object.entries(sideData).map(([color, side]) => ({
    key: accountKey(side.account) || `name:${normalizeText(side.name).toLowerCase()}`,
    name: side.name,
    account: side.account,
    color,
    ftdSide: playerSideForAccount(side.account, record, color),
    nodeCount: side.losses.length,
    totalLoss: Number(side.losses.reduce((sum, value) => sum + value, 0).toFixed(3)),
    averageLoss: side.losses.length
      ? Number((side.losses.reduce((sum, value) => sum + value, 0) / side.losses.length).toFixed(3))
      : null,
  }));
  return {
    schema: GAME_ANALYSIS_SCHEMA,
    analyzedAt: new Date().toISOString(),
    round: record.round,
    stage: record.stage,
    table: record.table,
    pairingId: record.pairingId,
    gameId: record.gameId,
    transcript: normalizeText(record.transcript),
    blackAccount: normalizeText(record.blackAccount),
    whiteAccount: normalizeText(record.whiteAccount),
    blackName: normalizeText(record.blackName),
    whiteName: normalizeText(record.whiteName),
    players,
    nodes,
    moveCount: nodes.length,
  };
}

function analysisRecordKey(recordValue) {
  const record = objectOf(recordValue);
  return [
    record.round, record.stage, record.table, record.pairingId, record.gameId,
    accountKey(record.blackAccount), accountKey(record.whiteAccount),
    Array.isArray(record.moves) ? record.moves.join("") : record.transcript,
  ].join("|");
}

function safeFileToken(value) {
  return normalizeText(value).replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^\.+|\.+$/g, "").slice(0, 80) || "unknown";
}

function analysisCacheFile(cacheDirectory, record) {
  return path.join(
    cacheDirectory,
    "games",
    `game_r${Number(record.round) || 0}_t${safeFileToken(record.table)}_${safeFileToken(record.gameId || record.pairingId)}_${safeFileToken(record.blackAccount)}_${safeFileToken(record.whiteAccount)}.json`,
  );
}

function readCachedGameAnalysis(cacheDirectory, record) {
  const filename = analysisCacheFile(cacheDirectory, record);
  if (!fs.existsSync(filename)) return null;
  try {
    const cached = JSON.parse(fs.readFileSync(filename, "utf8"));
    return cached && cached.schema === GAME_ANALYSIS_SCHEMA &&
      cached.cacheKey === analysisRecordKey(record) &&
      cached.transcript === normalizeText(record.transcript)
      ? cached
      : null;
  } catch (_) {
    return null;
  }
}

function writeCachedGameAnalysis(cacheDirectory, record, analysis) {
  const filename = analysisCacheFile(cacheDirectory, record);
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  const payload = { ...analysis, cacheKey: analysisRecordKey(record) };
  fs.writeFileSync(filename, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return filename;
}

function summarizeGames(records, analyses, options = {}) {
  const byRound = {};
  const players = new Map();
  const games = [];
  for (const record of records) {
    const key = analysisRecordKey(record);
    const analysis = analyses.get(key);
    if (!analysis) continue;
    const roundKey = String(record.round);
    const tableKey = String(record.table);
    const gamePlayers = Array.isArray(analysis.players) ? analysis.players : [];
    byRound[roundKey] ||= {};
    byRound[roundKey][tableKey] = {
      round: record.round,
      stage: record.stage,
      table: record.table,
      pairingId: record.pairingId,
      gameId: record.gameId || "",
      transcript: record.transcript,
      blackName: record.blackName,
      whiteName: record.whiteName,
      blackAccount: record.blackAccount,
      whiteAccount: record.whiteAccount,
      pgnFile: record.pgnFile || "",
      players: gamePlayers,
    };
    games.push({ record, analysis });
    for (const side of gamePlayers) {
      const playerKey = accountKey(side.account) || `name:${normalizeText(side.name).toLowerCase()}`;
      if (!playerKey) continue;
      const item = players.get(playerKey) || {
        key: playerKey,
        name: normalizeText(side.name),
        account: normalizeText(side.account),
        gameCount: 0,
        nodeCount: 0,
        totalLoss: 0,
        losses: [],
        games: [],
        plyGroups: new Map(),
      };
      const totalLoss = Number(side.totalLoss) || 0;
      item.gameCount += 1;
      item.nodeCount += Number(side.nodeCount) || 0;
      item.totalLoss += totalLoss;
      item.games.push({
        round: record.round,
        stage: record.stage,
        table: record.table,
        gameId: record.gameId || "",
        totalLoss,
        averageLoss: side.averageLoss,
        nodeCount: Number(side.nodeCount) || 0,
        offlineFilled: false,
      });
      const sideNodes = (Array.isArray(analysis.nodes) ? analysis.nodes : [])
        .filter((node) => normalizeText(node.playerColor).toLowerCase() === normalizeText(side.color).toLowerCase());
      for (const node of sideNodes) {
        const group = String(node.plyGroup || Math.ceil(Number(node.ply || 1) / 2));
        const entry = item.plyGroups.get(group) || { totalLoss: 0, count: 0 };
        entry.totalLoss += Number(node.lossClipped) || 0;
        entry.count += 1;
        item.plyGroups.set(group, entry);
      }
      players.set(playerKey, item);
    }
  }
  const playerRows = Array.from(players.values()).map((player) => ({
    key: player.key,
    name: player.name,
    account: player.account,
    gameCount: player.gameCount,
    nodeCount: player.nodeCount,
    totalLoss: Number(player.totalLoss.toFixed(3)),
    averageLoss: player.nodeCount ? Number((player.totalLoss / player.nodeCount).toFixed(3)) : null,
    averageGameLoss: player.gameCount ? Number((player.totalLoss / player.gameCount).toFixed(3)) : null,
    games: player.games.sort((a, b) => Number(a.round) - Number(b.round) || Number(a.table) - Number(b.table)),
    plyGroups: Object.fromEntries(Array.from(player.plyGroups.entries()).map(([group, value]) => [
      group,
      { averageLoss: value.count ? Number((value.totalLoss / value.count).toFixed(3)) : null, count: value.count },
    ])),
  })).sort((left, right) =>
    Number(left.averageGameLoss ?? Infinity) - Number(right.averageGameLoss ?? Infinity) ||
    Number(left.averageLoss ?? Infinity) - Number(right.averageLoss ?? Infinity) ||
    left.name.localeCompare(right.name, "zh-Hans-CN"),
  );
  return {
    schema: SUMMARY_SCHEMA,
    updatedAt: normalizeText(options.updatedAt) || new Date().toISOString(),
    scope: "preliminary-and-playoffs",
    roundLimit: Math.max(0, ...records.map((record) => Number(record.round) || 0)),
    summaryFile: normalizeText(options.summaryFile || ""),
    gameCount: games.length,
    playerCount: playerRows.length,
    topPlayers: playerRows.slice(0, 10),
    players: playerRows,
    pairingLossByRound: byRound,
    engine: objectOf(options.engine),
  };
}

module.exports = {
  GAME_ANALYSIS_SCHEMA,
  SUMMARY_SCHEMA,
  accountKey,
  analysisCacheFile,
  analysisRecordKey,
  analyzeOqRecord,
  collectEgRecords,
  detailFromPairing,
  formatOqPgn,
  movesFromOqDetail,
  normalizeBoard,
  parseHintOutput,
  readCachedGameAnalysis,
  scoreRoundStages,
  splitTranscript,
  storedTranscriptRecord,
  summarizeGames,
  writeCachedGameAnalysis,
};
