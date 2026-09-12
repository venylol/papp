"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  createDefaultTournamentParameters,
  sanitizeTournamentParameters,
} = require("./app.js");

test("new tournament parameters default semifinals and final to auto", () => {
  assert.deepEqual(createDefaultTournamentParameters(), {
    semifinalAndFinalMode: "auto",
    hasSemifinalAndFinal: false,
    brightwellConstant: 6,
  });
});

test("missing semifinals preference defaults to auto based on checked-in players", () => {
  const eightCheckedIn = Array.from({ length: 8 }, () => ({ checkedIn: true }));
  assert.equal(
    sanitizeTournamentParameters({}, []).semifinalAndFinalMode,
    "auto",
  );
  assert.equal(
    sanitizeTournamentParameters({}, []).hasSemifinalAndFinal,
    false,
  );
  assert.equal(
    sanitizeTournamentParameters({}, eightCheckedIn).hasSemifinalAndFinal,
    true,
  );
});

test("legacy boolean tournament parameters migrate to on or off", () => {
  assert.equal(
    sanitizeTournamentParameters({ hasSemifinalAndFinal: true }).semifinalAndFinalMode,
    "on",
  );
  assert.equal(
    sanitizeTournamentParameters({ hasSemifinalAndFinal: false }).semifinalAndFinalMode,
    "off",
  );
});

test("automatic semifinals and final use the actual checked-in count", () => {
  const candidates = Array.from({ length: 8 }, (_, index) => ({
    checkedIn: index < 7,
  }));
  const belowThreshold = sanitizeTournamentParameters(
    { semifinalAndFinalMode: "auto" },
    candidates,
  );
  assert.equal(belowThreshold.hasSemifinalAndFinal, false);

  candidates[7].checkedIn = true;
  const atThreshold = sanitizeTournamentParameters(
    { semifinalAndFinalMode: "auto" },
    candidates,
  );
  assert.equal(atThreshold.hasSemifinalAndFinal, true);
});

test("off and on remain explicit regardless of the checked-in count", () => {
  const eightCheckedIn = Array.from({ length: 8 }, () => ({ checkedIn: true }));
  assert.equal(
    sanitizeTournamentParameters({ semifinalAndFinalMode: "off" }, eightCheckedIn)
      .hasSemifinalAndFinal,
    false,
  );
  assert.equal(
    sanitizeTournamentParameters({ semifinalAndFinalMode: "on" }, [])
      .hasSemifinalAndFinal,
    true,
  );
});
