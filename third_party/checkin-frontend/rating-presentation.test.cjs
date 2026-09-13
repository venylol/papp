"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { presentRating } = require("./papp-portal/player-investigation/rating-presentation.js");

test("rating presentation explains insufficient game samples and exclusions", () => {
  const result = presentRating({
    estimate: null,
    status: "insufficient_target_games",
    statusReasons: ["fewer_than_minimum_complete_recent_target_games"],
    selectedGameCount: 3,
    minimumGameCount: 10,
    formalMinimum: 1600,
    formalMaximum: 2500,
    excludedGameCount: 27,
    excludedReasons: [
      { reason: "opponent_out_of_reference_range", count: 22 },
      { reason: "incomplete_phase_data", count: 5 },
    ],
  });
  assert.equal(result.value, "样本不足");
  assert.match(result.detail, /完整对局 3 局，正式估值至少需要 10 局/);
  assert.match(result.detail, /正式估值范围：1,600–2,500/);
  assert.match(result.detail, /对手 Rating 不在参考范围 22 局/);
  assert.match(result.detail, /阶段数据不完整 5 局/);
});

test("rating presentation distinguishes upper and lower range overflow", () => {
  assert.equal(presentRating({
    status: "above_reference_range", formalMinimum: 1600, formalMaximum: 2500,
  }).value, "高于 2,500");
  assert.equal(presentRating({
    status: "below_reference_range", formalMinimum: 1600, formalMaximum: 2500,
  }).value, "低于 1,600");
});

test("rating presentation explains confidence intervals that touch a boundary", () => {
  const result = presentRating({
    estimate: 1945,
    status: "multiple_minima",
    formalMinimum: 1600,
    formalMaximum: 2500,
    intervals: [{ lower: 1600, upper: 2153, truncatedLower: true, truncatedUpper: false }],
  });
  assert.equal(result.value, "1,945");
  assert.match(result.detail, /真实下界可能更低/);
  assert.match(result.detail, /多个拟合程度接近的最低区域/);
});
