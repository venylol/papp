"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const renderer = require("./papp-standings-png-renderer.js");

function buildCanvasHarness(devicePixelRatio, callback) {
  const labels = [];
  const context = {
    scale() {},
    fillRect() {},
    save() {},
    restore() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    stroke() {},
    measureText(text) { return { width: String(text).length * 7 }; },
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
    callback({ labels, canvases });
  } finally {
    if (previousWindow === undefined) delete global.window;
    else global.window = previousWindow;
    if (previousDocument === undefined) delete global.document;
    else global.document = previousDocument;
  }
}

function assertCanvasWithinLimits(canvas) {
  assert.ok(canvas.width <= 8192);
  assert.ok(canvas.height <= 8192);
  assert.ok(canvas.width * canvas.height <= 16_000_000);
}

test("round standings PNG preserves PAPP order, ranks, metrics, and player accounts", () => {
  buildCanvasHarness(3, ({ labels, canvases }) => {
    const rows = [
      {
        rank: 4,
        displayName: "选手甲",
        account: "account-a",
        totalPoints: 4.5,
        brightwell: 12.25,
        totalDiscs: 128,
      },
      {
        rank: 1,
        displayName: "选手乙",
        totalPoints: 6,
        brightwell: 18,
        totalDiscs: 134,
      },
    ];

    renderer.buildStandingsCanvas({
      competitionName: "秋季赛",
      standings: rows,
      labels: {
        title: "第 2 轮排名",
        metadata: "2 位选手 · 更新时间：测试",
        rank: "名次",
        player: "选手",
        totalPoints: "总积分",
        brightwell: "Brightwell",
        totalDiscs: "总棋子数",
      },
    });

    assert.ok(labels.includes("第 2 轮排名"));
    assert.ok(labels.includes("account-a"));
    assert.ok(labels.includes("4.5"));
    assert.ok(labels.includes("12.25"));
    assert.ok(labels.indexOf("选手甲") < labels.indexOf("选手乙"));
    assert.equal(canvases.length, 1);
    assertCanvasWithinLimits(canvases[0]);
  });
});

test("overall standings PNG includes preliminary rank and does not invent missing ranks", () => {
  buildCanvasHarness(2, ({ labels }) => {
    renderer.buildStandingsCanvas({
      standings: [
        {
          rank: null,
          preliminaryRank: 3,
          displayName: "待确认选手",
          totalPoints: 5,
          brightwell: 17,
          totalDiscs: 126,
        },
        {
          rank: 8,
          preliminaryRank: 1,
          displayName: "另一位选手",
          totalPoints: 4,
          brightwell: 10,
          totalDiscs: 119,
        },
      ],
      showPreliminaryRank: true,
      labels: {
        title: "赛事总排名",
        rank: "名次",
        preliminaryRank: "预赛名次",
      },
    });

    assert.ok(labels.includes("预赛名次"));
    assert.ok(labels.includes("—"));
    assert.ok(labels.indexOf("待确认选手") < labels.indexOf("另一位选手"));
    assert.ok(labels.includes("排名数据由本地 PAPP C 提供"));
  });
});

test("large standings PNG keeps every row within browser canvas limits", () => {
  buildCanvasHarness(3, ({ labels, canvases }) => {
    const standings = Array.from({ length: 180 }, (_, index) => ({
      rank: index + 1,
      displayName: `选手${index + 1}`,
      totalPoints: index / 2,
      brightwell: index * 3,
      totalDiscs: 64,
    }));
    renderer.buildStandingsCanvas({ standings });

    assert.ok(labels.includes("选手1"));
    assert.ok(labels.includes("选手180"));
    assertCanvasWithinLimits(canvases[0]);
  });
});

test("standings PNG renderer rejects an empty list", () => {
  assert.throws(() => renderer.buildStandingsCanvas({ standings: [] }), /没有可导出的排名数据/);
});
