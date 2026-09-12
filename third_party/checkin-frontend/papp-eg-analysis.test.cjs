"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const EgAnalysis = require("./papp-eg-analysis.js");

function pairing(id, table, stage, round) {
  return {
    id,
    table,
    black: "黑方",
    white: "白方",
    blackAccount: "oq-black",
    whiteAccount: "oq-white",
    status: "imported",
    metadata: {
      gameRecord: {
        source: "oq-poll",
        gameId: "oq-game-" + id,
        pappBlackAccount: "oq-black",
        pappWhiteAccount: "oq-white",
        moves: ["f5"],
        transcript: "f5",
      },
    },
    stage,
    round,
  };
}

test("EG collects OQ transcripts from preliminary and playoff rounds for loss analysis", async () => {
  const state = {
    scoreHelper: {
      preliminaryRoundCount: 2,
      rounds: [
        { pairings: [pairing("r1-t1", 1, "preliminary", 1)] },
        { pairings: [pairing("r2-t1", 1, "preliminary", 2)] },
      ],
    },
    playoffRegistration: {
      semifinalPairings: [pairing("semi-t1", 1, "semifinal", 3)],
      placementPairings: [pairing("placement-t1", 1, "placement", 4)],
    },
  };
  const records = EgAnalysis.collectEgRecords({ state });

  assert.deepEqual(records.map((record) => [record.round, record.stage, record.gameId]), [
    [1, "preliminary", "oq-game-r1-t1"],
    [2, "preliminary", "oq-game-r2-t1"],
    [3, "semifinal", "oq-game-semi-t1"],
    [4, "placement", "oq-game-placement-t1"],
  ]);
  assert.equal(records[0].transcript, "f5");
  assert.equal(records[0].blackAccount, "oq-black");

  const engineCalls = { setboard: [], play: [], hint: 0 };
  const engine = {
    async setboard(board) { engineCalls.setboard.push(board); },
    async play(move) { engineCalls.play.push(move); },
    async hint() {
      engineCalls.hint += 1;
      return { bestMove: "f5", bestEval: engineCalls.hint === 1 ? 6 : 4, depth: "22" };
    },
  };
  const gameAnalysis = await EgAnalysis.analyzeOqRecord(records[0], engine);
  assert.deepEqual(engineCalls.play, ["f5"]);
  assert.equal(engineCalls.hint, 2);
  assert.equal(gameAnalysis.nodes[0].move, "f5");
  assert.equal(gameAnalysis.players.find((player) => player.color === "black").nodeCount, 1);

  const analyses = new Map(records.map((record) => [EgAnalysis.analysisRecordKey(record), {
    players: [
      { color: "black", ftdSide: "black", name: "黑方", account: "oq-black", nodeCount: 1, totalLoss: 1.5, averageLoss: 1.5 },
      { color: "white", ftdSide: "white", name: "白方", account: "oq-white", nodeCount: 1, totalLoss: 2.5, averageLoss: 2.5 },
    ],
    nodes: [
      { playerColor: "black", ply: 1, plyGroup: 1, lossClipped: 1.5 },
      { playerColor: "white", ply: 2, plyGroup: 1, lossClipped: 2.5 },
    ],
  }]));
  const summary = EgAnalysis.summarizeGames(records, analyses);
  assert.equal(summary.scope, "preliminary-and-playoffs");
  assert.equal(summary.gameCount, 4);
  assert.equal(summary.pairingLossByRound["3"]["1"].stage, "semifinal");
  assert.equal(summary.pairingLossByRound["4"]["1"].stage, "placement");
  assert.equal(summary.pairingLossByRound["4"]["1"].players[0].totalLoss, 1.5);
});
