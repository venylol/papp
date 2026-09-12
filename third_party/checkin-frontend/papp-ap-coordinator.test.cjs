"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { ApCoordinator, validateAp, mergeChanges } = require("./papp-ap-coordinator.js");

function fixture() {
  return {
    competitionName: "AP 测试", step: "checkin",
    eventSchedule: { registrationDeadline: "2026-09-12T12:00:00Z", checkinStart: "2026-09-12T12:05:00Z", checkinDeadline: "2026-09-12T12:10:00Z", competitionStart: "2026-09-12T12:10:00Z", wechatGroup: { username: "test@chatroom" } },
    mapping: { rows: [{ id: "mapping-1" }] },
    tournamentParameters: { semifinalAndFinalMode: "on", brightwellConstant: 3 },
    scoreHelper: { preliminaryRoundCount: 4, activeRound: 1 },
    wechatAutoCheckin: { items: [] }, players: [],
    ap: { enabled: false, status: "off" }, ui: { oqPollSeconds: 15 },
  };
}

function harness(saved = fixture()) {
  let state = structuredClone(saved);
  let now = Date.parse("2026-09-12T12:11:00Z");
  let ready = true, next = { stage: "preliminary", round: 2 };
  const calls = { checkin: 0, tournament: 0, enters: [] };
  const dependencies = {
    now: () => now,
    request: async (url, body) => {
      assert.equal(url, "/api/state");
      if (body) state = mergeChanges(body.baseState, body.state, state);
      return { ok: true, state: structuredClone(state) };
    },
    checkin: { tick: async (current) => { calls.checkin++; return { state: current, ready }; } },
    tournament: {
      tick: async (current) => { calls.tournament++; return { state: current, next }; },
      enterNext: async (current, target) => {
        calls.enters.push(structuredClone(target));
        current.step = "score-helper";
        current.scoreHelper.activeRound = target.round;
        return { state: current };
      },
    },
  };
  const coordinator = new ApCoordinator(dependencies);
  return { coordinator, calls, dependencies,
    get state() { return state; }, setReady(value) { ready = value; }, setNext(value) { next = value; },
    advance(ms) { now += ms; }, setNow(value) { now = Date.parse(value); },
  };
}

test("mapping merely needs to exist; OQ defaults to 15 seconds on enable", async () => {
  const saved = fixture();
  delete saved.ui;
  assert.deepEqual(validateAp(saved), []);
  const h = harness(saved);
  await h.coordinator.control("enable");
  assert.equal(h.state.ui.oqPollSeconds, 15);
});

test("first round waits for grace processing and every pending item", async () => {
  const h = harness();
  await h.coordinator.control("enable");
  h.setReady(false);
  await h.coordinator.tick();
  assert.equal(h.state.ap.countdown, null);
  h.setReady(true);
  h.state.wechatAutoCheckin.items.push({ id: "pending", status: "pending" });
  await h.coordinator.tick();
  assert.equal(h.state.ap.countdown, null);
  h.state.wechatAutoCheckin.items[0].status = "solved";
  await h.coordinator.tick();
  assert.equal(h.state.ap.countdown.target.round, 1);
  assert.equal(h.calls.enters.length, 0);
});

test("countdown remains stable, cancels, and resume starts a new full ten seconds", async () => {
  const h = harness();
  await h.coordinator.control("enable");
  await h.coordinator.tick();
  const deadline = h.state.ap.countdown.deadlineAt;
  h.advance(3000);
  await h.coordinator.tick();
  assert.equal(h.state.ap.countdown.deadlineAt, deadline);
  await h.coordinator.control("pause");
  h.advance(20000);
  await h.coordinator.tick();
  assert.equal(h.calls.enters.length, 0);
  assert.equal(h.state.ap.status, "paused");
  await h.coordinator.control("resume");
  await h.coordinator.tick();
  assert.equal(h.state.ap.countdown.deadlineAt, deadline + 23000);
  h.advance(10000);
  await h.coordinator.tick();
  assert.deepEqual(h.calls.enters, [{ stage: "preliminary", round: 1 }]);
});

test("manual first-round start bypasses checkin collection and paused AP continues current tournament work", async () => {
  const h = harness();
  await h.coordinator.control("enable");
  h.state.step = "score-helper";
  h.setNow("2026-09-12T12:08:00Z");
  await h.coordinator.control("pause");
  await h.coordinator.tick();
  assert.equal(h.calls.checkin, 0);
  assert.equal(h.calls.tournament, 1);
  assert.equal(h.calls.enters.length, 0);
});

test("service restart pauses even expired countdown until explicit resume", async () => {
  const saved = fixture();
  saved.ap = { enabled: true, status: "countdown", countdown: { target: { stage: "preliminary", round: 1 }, deadlineAt: 1 } };
  const h = harness(saved);
  await h.coordinator.tick();
  assert.equal(h.state.ap.status, "paused");
  assert.equal(h.state.ap.countdown, null);
  assert.equal(h.calls.enters.length, 0);
  await h.coordinator.control("resume");
  await h.coordinator.tick();
  assert.equal(h.state.ap.status, "countdown");
});

test("server advances through the final ranking without any browser requests", async () => {
  const h = harness();
  h.state.step = "final-registration";
  h.setNext({ stage: "overall", round: 6 });
  await h.coordinator.control("enable");
  await h.coordinator.tick();
  await h.coordinator.control("confirm");
  await h.coordinator.tick();
  assert.equal(h.state.ap.status, "complete");
  await h.coordinator.tick();
  assert.equal(h.calls.enters.length, 1);
});

test("pending arriving during countdown cancels the pending automatic start", async () => {
  const h = harness();
  await h.coordinator.control("enable");
  await h.coordinator.tick();
  h.state.wechatAutoCheckin.items.push({ id: "late", status: "pending" });
  h.advance(10000);
  await h.coordinator.tick();
  assert.equal(h.state.ap.countdown, null);
  assert.equal(h.calls.enters.length, 0);
});

test("referee cancellation during asynchronous collection prevents transition", async () => {
  const h = harness();
  h.state.step = "score-helper";
  await h.coordinator.control("enable");
  await h.coordinator.tick();
  h.advance(10000);
  h.dependencies.tournament.tick = async (state) => {
    await h.coordinator.control("pause");
    return { state, next: { stage: "preliminary", round: 2 } };
  };
  await h.coordinator.tick();
  assert.equal(h.calls.enters.length, 0);
  assert.equal(h.state.ap.status, "paused");
});

test("manual round change during collection cannot advance an obsolete countdown", async () => {
  const h = harness();
  h.state.step = "score-helper";
  await h.coordinator.control("enable");
  await h.coordinator.tick();
  h.advance(10000);
  h.dependencies.tournament.tick = async (state) => {
    h.state.scoreHelper.activeRound = 2;
    return { state, next: { stage: "preliminary", round: 2 } };
  };
  await h.coordinator.tick();
  assert.equal(h.calls.enters.length, 0);
});
