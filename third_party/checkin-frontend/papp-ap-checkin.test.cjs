"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { tick } = require("./papp-ap-checkin.js");
const time = value => new Date(`2026-09-12T${value}`).getTime();
function fixture() {
  return { step: "checkin", ap: {}, nextPlayerId: 2,
    eventSchedule: { registrationDeadline: "2026-09-12T20:00", checkinStart: "2026-09-12T20:05", checkinDeadline: "2026-09-12T20:30",
      wechatGroup: { username: "room@chatroom", displayName: "比赛群" } },
    players: [{ id: 1, displayName: "Alice", account: "alice123", checkedIn: false, checkedInAt: null }],
    mapping: { groupName: "比赛群", rows: [{ id: "row1", checkinPlayerId: "1", registrationNick: "Alice", wechatNick: "阿丽", oqAccount: "alice123" }] },
    wechatRelaySync: { enabled: true }, wechatAutoCheckin: { enabled: true, items: [] },
    scoreHelper: { rounds: [{ round: 1, startedAt: "2026-09-12 21:06:34", pairings: [{ id: "preserved" }] }] },
  };
}
function message(id, sent, content = "1", nick = "阿丽") {
  return { messageId: id, createTime: time(sent) / 1000, type: 1, senderGroupNick: nick, content };
}
function api(messages, calls = []) {
  return async path => { const query = new URL(path, "http://localhost").searchParams; calls.push(query);
    return { ok: true, messages: query.has("relayOnly") ? messages.filter(m => m.content.includes("接龙")) : messages };
  };
}
test("waits for configured start, then reads the five-minute lead window without changing stored pairings", async () => {
  const original = fixture();
  const first = await tick(original, { nowMs: time("20:04:59"), request: api([message("early", "20:00:00")]) });
  assert.equal(first.state.players[0].checkedIn, false);
  const second = await tick(first.state, { nowMs: time("20:05:00"), request: api([message("early", "20:00:00"), message("too-early", "19:59:59")]) });
  assert.equal(second.state.players[0].checkedIn, true);
  assert.equal(second.state.wechatAutoCheckin.items.length, 1);
  assert.deepEqual(second.state.scoreHelper, original.scoreHelper);
  assert.equal(original.players[0].checkedIn, false);
});
test("final scan admits deadline plus one minute, excludes later messages, and stops querying", async () => {
  const calls = [];
  const request = api([message("last", "20:31:00"), message("late", "20:31:01")], calls);
  const result = await tick(fixture(), { nowMs: time("20:31:02"), request });
  assert.equal(result.ready, true);
  assert.deepEqual(result.state.wechatAutoCheckin.items.map(item => item.messageId), ["last"]);
  const count = calls.length;
  const again = await tick(result.state, { nowMs: time("20:32:00"), request });
  assert.equal(calls.length, count);
  assert.equal(again.changed, false);
});
test("registration final scan only imports relays sent before cutoff even if query returns later", async () => {
  const result = await tick(fixture(), { nowMs: time("20:00:01"), request: api([
    message("valid", "20:00:00", "#接龙\n无差别组：\n1. Alice alice123\n2. Bob bob123"),
    message("late", "20:00:01", "#接龙\n无差别组：\n1. Alice alice123\n2. Carol carol123"),
  ]) });
  assert.equal(result.error, undefined);
  assert.deepEqual(result.state.players.map(player => player.displayName), ["Alice", "Bob"]);
  assert.equal(result.state.mapping.rows.length, 2);
  assert.equal(result.state.players[1].checkedIn, false);
  assert.equal(result.state.ap.checkin.relayDone, true);
});
test("incomplete mapping keeps unmatched checkins and attendance warnings pending for referee", async () => {
  const state = fixture();
  state.mapping.rows[0].wechatNick = "";
  const result = await tick(state, { nowMs: time("20:31:00"), request: api([message("unknown", "20:10:00"), message("withdraw", "20:20:00", "临时有事")]) });
  assert.equal(result.ready, true);
  assert.equal(result.state.players[0].checkedIn, false);
  assert.deepEqual(result.state.wechatAutoCheckin.items.map(item => item.pendingKind), ["unmapped-checkin", "attendance-keyword"]);
});
test("manual early first round stops polling and cannot re-check a planned withdrawn player", async () => {
  const state = fixture();
  state.step = "preliminary";
  state.plannedWithdrawals = [{ playerId: 1, round: 2 }];
  const result = await tick(state, { nowMs: time("20:20:00"), request: async () => { throw new Error("must not query"); } });
  assert.equal(result.changed, false);
  assert.equal(result.state.players[0].checkedIn, false);
});
test("failed final query does not mark completed and succeeds on retry", async () => {
  const state = fixture();
  let failed = false;
  const result = await tick(state, { nowMs: time("20:31:00"), request: async path => {
    if (!path.includes("relayOnly")) { failed = true; throw new Error("offline"); }
    return { ok: true, messages: [] };
  } });
  assert.equal(failed, true);
  assert.equal(result.error, "offline");
  assert.equal(result.ready, false);
  assert.equal(result.state.ap.checkin.done, undefined);
  const retried = await tick(result.state, { nowMs: time("20:31:01"), request: api([message("last", "20:31:00")]) });
  assert.equal(retried.ready, true);
  assert.equal(retried.state.players[0].checkedIn, true);
});
test("human resolution stays resolved when the full window is scanned again", async () => {
  const state = fixture();
  state.wechatAutoCheckin.items = [{ id: `room@chatroom|${time("20:05:00")}|${time("20:30:00")}|last`, messageId: "last", status: "ignored", resolvedBy: "human" }];
  const result = await tick(state, { nowMs: time("20:31:00"), request: api([message("last", "20:10:00")]) });
  assert.equal(result.state.players[0].checkedIn, false);
  assert.equal(result.state.wechatAutoCheckin.items[0].status, "ignored");
});
