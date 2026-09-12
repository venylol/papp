"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const renderer = require("./papp-score-png-renderer.js");

function buildCanvasHarness(devicePixelRatio, callback) {
  const labels = [];
  let arcCount = 0;
  const gradient = { addColorStop() {} };
  const context = {
    scale() {},
    fillRect() {},
    save() {},
    restore() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    quadraticCurveTo() {},
    closePath() {},
    fill() {},
    stroke() {},
    arc() { arcCount += 1; },
    createRadialGradient() { return gradient; },
    measureText(text) { return { width: String(text).length * 8 }; },
    fillText(text) { labels.push(String(text)); },
  };
  const previousWindow = global.window;
  const previousDocument = global.document;
  const canvases = [];
  global.window = { devicePixelRatio };
  global.document = {
    createElement() {
      const canvas = {
        style: {},
        width: 0,
        height: 0,
        getContext() { return context; },
      };
      canvases.push(canvas);
      return canvas;
    },
  };

  try {
    callback({ labels, canvases, getArcCount: () => arcCount });
  } finally {
    if (previousWindow === undefined) delete global.window;
    else global.window = previousWindow;
    if (previousDocument === undefined) delete global.document;
    else global.document = previousDocument;
  }
}

function samplePairings(count) {
  return Array.from({ length: count }, (_, index) => ({
    table: index + 1,
    black: `Player A${index + 1}`,
    white: `Player B${index + 1}`,
    blackScore: 32,
    whiteScore: 32,
  }));
}

function assertTableLabels(labels, count) {
  const tables = labels.filter((label) => /^第 \d+ 台$/.test(label));
  assert.equal(tables.length, count);
  assert.equal(tables[tables.length - 1], `第 ${count} 台`);
  assert.equal(labels.some((label) => /黑方|白方|执黑|执白/.test(label)), false);
}

function assertCanvasWithinLimits(canvas) {
  assert.ok(canvas.width <= 8192);
  assert.ok(canvas.height <= 8192);
  assert.ok(canvas.width * canvas.height <= 16_000_000);
}

test("pairings PNG omits side markers and renders all tables within canvas limits", () => {
  buildCanvasHarness(3, ({ labels, canvases, getArcCount }) => {
    renderer.buildPairingsCanvas({ round: 3, pairings: samplePairings(50) });
    assertTableLabels(labels, 50);
    assert.equal(getArcCount(), 0);
    assertCanvasWithinLimits(canvases[0]);
  });
});

test("pairings PNG keeps BYE rows as 轮空 and omits pairing passwords", () => {
  const pairings = samplePairings(11);
  pairings[10] = {
    table: 11,
    black: "Player A11",
    white: "",
    status: "bye",
    blackScore: 40,
    whiteScore: 24,
  };
  pairings.push({
    table: 12,
    black: "BYE",
    white: "Player B12",
    status: "bye",
  });

  buildCanvasHarness(2, ({ labels, canvases }) => {
    renderer.buildPairingsCanvas({ round: 3, pairings });
    assertTableLabels(labels, 12);
    assert.equal(labels.filter((label) => label === "轮空").length, 2);
    assert.equal(labels.includes("0311"), false);
    assert.equal(labels.includes("0312"), false);
    assert.equal(labels.includes("自动记分"), false);
    assertCanvasWithinLimits(canvases[0]);
  });
});

test("score PNG omits side markers and renders all scores beyond ten tables", () => {
  buildCanvasHarness(3, ({ labels, canvases, getArcCount }) => {
    renderer.buildScoreCanvas(samplePairings(12), { round: 3 });
    assertTableLabels(labels, 12);
    assert.equal(labels.filter((label) => label === "32").length, 24);
    assert.equal(getArcCount(), 0);
    assertCanvasWithinLimits(canvases[0]);
  });
});

test("score PNG renders BYE as 轮空 and does not invent child scores", () => {
  const pairings = samplePairings(12);
  pairings[11] = {
    table: 12,
    black: "Player A12",
    white: "",
    status: "bye",
    blackScore: 40,
    whiteScore: 24,
  };

  const byeRow = renderer.buildScoreRow(pairings[11]);
  assert.deepEqual(byeRow, {
    table: 12,
    black: "Player A12",
    white: "轮空",
    blackScore: null,
    whiteScore: null,
    isBye: true,
  });

  buildCanvasHarness(3, ({ labels, canvases }) => {
    renderer.buildScoreCanvas(pairings, { round: 3 });
    assertTableLabels(labels, 12);
    assert.equal(labels.filter((label) => label === "轮空").length, 1);
    assert.equal(labels.filter((label) => label === "32").length, 22);
    assert.equal(labels.some((label) => ["40", "24", "33", "31"].includes(label)), false);
    assertCanvasWithinLimits(canvases[0]);
  });
});
