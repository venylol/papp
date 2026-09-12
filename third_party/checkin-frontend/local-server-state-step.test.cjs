"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { validateState } = require("./local-server.js");

function stateForStep(step) {
  return { version: 2, step, players: [] };
}

test("shared state accepts every active workflow step, including finals", () => {
  for (const step of ["schedule", "import", "checkin", "score-helper", "final-registration"]) {
    assert.doesNotThrow(() => validateState(stateForStep(step)), step);
  }
});

test("shared state rejects steps outside the active workflow", () => {
  assert.throws(
    () => validateState(stateForStep("unknown-step")),
    /state\.step must be .*final-registration/,
  );
});
