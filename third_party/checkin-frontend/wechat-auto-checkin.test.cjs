"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  classifyWechatAutoCheckinMessage,
  createDefaultWechatAutoCheckin,
  isWechatAutoCheckinMappingComplete,
  isWechatAutoCheckinMessageInWindow,
  reconcileWechatAutoCheckinMessages,
  resolveWechatAutoCheckinPlayer,
} = require("./app.js");

const selectedGroupName = "September Club Group";
const groupUsername = "clubroom@chatroom";
const checkinStartMs = Date.UTC(2026, 8, 12, 12, 0, 0);
const checkinDeadlineMs = checkinStartMs + 30 * 60_000;
const nowMs = checkinDeadlineMs + 30_000;

function players() {
  return [
    { id: "player-1", displayName: "Player One", checkedIn: false, checkedInAt: null },
    { id: "player-2", displayName: "Player Two", checkedIn: false, checkedInAt: null },
  ];
}

function mapping(rows, groupName = selectedGroupName) {
  return { groupName, rows };
}

function row(checkinPlayerId, wechatNick) {
  const registrationNick = {
    "player-1": "Player One",
    "player-2": "Player Two",
  }[checkinPlayerId] || "";
  return { checkinPlayerId, registrationNick, wechatNick };
}

function message(messageId, senderGroupNick, content, createTime = checkinStartMs / 1000, extra = {}) {
  return {
    messageId,
    createTime,
    type: 1,
    senderGroupNick,
    content,
    ...extra,
  };
}

function reconcile({ autoCheckin, messages, rosterPlayers, playerMapping, now = nowMs }) {
  return reconcileWechatAutoCheckinMessages({
    autoCheckin,
    messages,
    mapping: playerMapping,
    rosterPlayers,
    groupUsername,
    selectedGroupName,
    checkinStartMs,
    checkinDeadlineMs,
    nowMs: now,
  });
}

test("classifies only standalone 1, 2, 111, and 222 messages after punctuation normalization", () => {
  for (const [content, token] of [
    ["1", "1"],
    ["2", "2"],
    ["111", "111"],
    ["222", "222"],
    ["（１）！", "1"],
    [" ２。 ", "2"],
  ]) {
    assert.deepEqual(classifyWechatAutoCheckinMessage({ type: 1, content }), {
      kind: "checkin",
      token,
    });
  }

  for (const content of ["我1", "1号", "12", "1111", "1,2", "签到1", "1 2"]) {
    assert.equal(classifyWechatAutoCheckinMessage({ type: 1, content }), null, content);
  }
  assert.equal(classifyWechatAutoCheckinMessage({ type: 49, content: "1" }), null);
});

test("classifies absence and withdrawal phrases as pending before check-in tokens", () => {
  assert.deepEqual(
    classifyWechatAutoCheckinMessage({ type: 1, content: "我今天不比了，先报个1" }),
    { kind: "keyword", keyword: "不比了" },
  );
  assert.deepEqual(
    classifyWechatAutoCheckinMessage({ type: 1, content: "临时有事，今天请假" }),
    { kind: "keyword", keyword: "临时有事" },
  );
});

test("includes both edges of the configured attendance window", () => {
  const first = checkinStartMs - 5 * 60_000;
  const last = checkinDeadlineMs + 60_000;
  assert.equal(isWechatAutoCheckinMessageInWindow(first, checkinStartMs, checkinDeadlineMs), true);
  assert.equal(isWechatAutoCheckinMessageInWindow(last, checkinStartMs, checkinDeadlineMs), true);
  assert.equal(isWechatAutoCheckinMessageInWindow(first - 1, checkinStartMs, checkinDeadlineMs), false);
  assert.equal(isWechatAutoCheckinMessageInWindow(last + 1, checkinStartMs, checkinDeadlineMs), false);
});

test("considers mappings complete only for the selected group with unique nicknames for every player", () => {
  const roster = players();
  assert.equal(
    isWechatAutoCheckinMappingComplete(
      mapping([row("player-1", "One"), row("player-2", "Two")]),
      roster,
      selectedGroupName,
    ),
    true,
  );
  assert.equal(
    isWechatAutoCheckinMappingComplete(
      mapping([row("player-1", "One"), row("player-2", "")]),
      roster,
      selectedGroupName,
    ),
    false,
  );
  assert.equal(
    isWechatAutoCheckinMappingComplete(
      mapping([
        row("player-1", "One"),
        { ...row("player-2", "Two"), registrationNick: "" },
      ]),
      roster,
      selectedGroupName,
    ),
    false,
  );
  assert.equal(
    isWechatAutoCheckinMappingComplete(
      mapping([row("player-1", "Same"), row("player-2", "Same")]),
      roster,
      selectedGroupName,
    ),
    false,
  );
  assert.equal(
    isWechatAutoCheckinMappingComplete(
      mapping([row("player-1", "One"), row("player-2", "Two")], "Other Group"),
      roster,
      selectedGroupName,
    ),
    false,
  );
});

test("does not use a contact display name when the group nickname is unavailable", () => {
  const roster = players();
  const playerMapping = mapping([row("player-1", "Contact Name")]);
  assert.deepEqual(
    resolveWechatAutoCheckinPlayer("", playerMapping, roster, selectedGroupName),
    { status: "unmatched", player: null },
  );

  const result = reconcile({
    autoCheckin: createDefaultWechatAutoCheckin(),
    messages: [message("no-group-nick", "", "1", undefined, { sender: "Contact Name" })],
    rosterPlayers: roster,
    playerMapping,
  });
  assert.equal(result.items[0].status, "pending");
  assert.equal(roster[0].checkedIn, false);
});

test("auto-checks a uniquely mapped player and retains the original WeChat timestamp", () => {
  const roster = [players()[0]];
  const timestamp = checkinStartMs / 1000 + 12;
  const result = reconcile({
    autoCheckin: createDefaultWechatAutoCheckin(),
    messages: [message("checkin-one", "One In Group", "1", timestamp)],
    rosterPlayers: roster,
    playerMapping: mapping([row("player-1", "One In Group")]),
  });

  assert.equal(roster[0].checkedIn, true);
  assert.equal(roster[0].checkedInAt, timestamp * 1000);
  assert.equal(Boolean(roster[0].isNew), false);
  assert.equal(result.items[0].status, "auto-checked-in");
  assert.equal(result.items[0].playerId, "player-1");
  assert.equal(result.items[0].resolvedBy, "script");
});

test("auto-check-in tokens 2 and 222 check in mapped players and mark them as newcomers", () => {
  for (const token of ["2", "222"]) {
    const roster = [players()[0]];
    const result = reconcile({
      autoCheckin: createDefaultWechatAutoCheckin(),
      messages: [message(`newcomer-${token}`, "One In Group", token)],
      rosterPlayers: roster,
      playerMapping: mapping([row("player-1", "One In Group")]),
    });

    assert.equal(roster[0].checkedIn, true, token);
    assert.equal(roster[0].isNew, true, token);
    assert.equal(result.items[0].status, "auto-checked-in", token);
  }
});

test("rechecks old messages and resolves an unmapped pending check-in after a mapping is added", () => {
  const roster = players();
  const checkin = message("later-mapped", "New Nickname", "2");
  const first = reconcile({
    autoCheckin: createDefaultWechatAutoCheckin(),
    messages: [checkin],
    rosterPlayers: roster,
    playerMapping: mapping([row("player-1", "One")]),
  });
  assert.equal(first.items[0].status, "pending");
  assert.equal(first.items[0].pendingKind, "unmapped-checkin");
  assert.equal(roster[1].checkedIn, false);

  const second = reconcile({
    autoCheckin: { ...createDefaultWechatAutoCheckin(), items: first.items },
    messages: [checkin],
    rosterPlayers: roster,
    playerMapping: mapping([row("player-1", "One"), row("player-2", "New Nickname")]),
  });
  assert.equal(second.items[0].status, "auto-checked-in");
  assert.equal(second.items[0].pendingKind, "");
  assert.equal(roster[1].checkedIn, true);
  assert.equal(roster[1].isNew, true);
});

test("suppresses unknown check-ins for a complete map and closes older unmatched pending items", () => {
  const roster = players();
  const unknown = message("unknown", "Guest", "111");
  const incomplete = mapping([row("player-1", "One")]);
  const pending = reconcile({
    autoCheckin: createDefaultWechatAutoCheckin(),
    messages: [unknown],
    rosterPlayers: roster,
    playerMapping: incomplete,
  });
  assert.equal(pending.items.length, 1);
  assert.equal(pending.items[0].status, "pending");

  const complete = reconcile({
    autoCheckin: { ...createDefaultWechatAutoCheckin(), items: pending.items },
    messages: [unknown],
    rosterPlayers: roster,
    playerMapping: mapping([row("player-1", "One"), row("player-2", "Two")]),
  });
  assert.equal(complete.items[0].status, "auto-ignored");
  assert.equal(complete.items.some((item) => item.status === "pending"), false);

  const noPending = reconcile({
    autoCheckin: createDefaultWechatAutoCheckin(),
    messages: [unknown],
    rosterPlayers: roster,
    playerMapping: mapping([row("player-1", "One"), row("player-2", "Two")]),
  });
  assert.equal(noPending.items.length, 0);
});

test("keeps mapped absence and withdrawal messages pending", () => {
  const roster = [players()[0]];
  const result = reconcile({
    autoCheckin: createDefaultWechatAutoCheckin(),
    messages: [message("leave", "One In Group", "请假，我不能来")],
    rosterPlayers: roster,
    playerMapping: mapping([row("player-1", "One In Group")]),
  });
  assert.equal(result.items[0].status, "pending");
  assert.equal(result.items[0].kind, "keyword");
  assert.equal(result.items[0].playerId, "player-1");
  assert.equal(roster[0].checkedIn, false);
});

test("preserves human ignore and solved decisions on subsequent full scans", () => {
  for (const status of ["ignored", "solved"]) {
    const roster = [players()[0]];
    const item = {
      id: `${groupUsername}|${checkinStartMs}|${checkinDeadlineMs}|terminal-${status}`,
      messageId: `terminal-${status}`,
      scopeKey: `${groupUsername}|${checkinStartMs}|${checkinDeadlineMs}`,
      groupUsername,
      createTime: checkinStartMs / 1000,
      kind: "checkin",
      pendingKind: "unmapped-checkin",
      status,
      resolvedBy: "human",
      resolvedAt: nowMs,
    };
    const result = reconcile({
      autoCheckin: { ...createDefaultWechatAutoCheckin(), items: [item] },
      messages: [message(`terminal-${status}`, "One In Group", "1")],
      rosterPlayers: roster,
      playerMapping: mapping([row("player-1", "One In Group")]),
    });
    assert.equal(result.items[0].status, status);
    assert.equal(roster[0].checkedIn, false);
  }
});
