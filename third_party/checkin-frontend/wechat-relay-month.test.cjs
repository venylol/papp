"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  formatWechatMessageTimestamp,
  isWechatRelayTemplateContent,
  latestWechatRelayFromMessages,
  sanitizeWechatRelaySync,
  wechatRelayMatchesEventMonth,
} = require("./app.js");

function localUnixSeconds(year, month, day, hour = 12) {
  return Math.floor(new Date(year, month - 1, day, hour).getTime() / 1000);
}

const augustRelay = {
  messageId: "august",
  createTime: localUnixSeconds(2026, 8, 31),
  content: "#接龙\n[8月新人组] 栢龙杯新人赛 比赛报名接龙\n1. Alice alice\n2. Bob bob",
};

const septemberRelay = {
  messageId: "september",
  createTime: localUnixSeconds(2026, 9, 10),
  content: "#接龙\n[9月新人组] 栢龙杯新人赛 比赛报名接龙\n1. Alice alice\n2. Bob bob",
};

test("matches relay messages by send month, independently of relay text", () => {
  const sentInSeptemberWithAugustHeading = {
    messageId: "september-send-august-heading",
    createTime: localUnixSeconds(2026, 9, 11),
    content: `${augustRelay.content}\n截止报名时间：2026年9月12日20:30`,
  };
  const sentInAugustWithSeptemberHeading = {
    messageId: "august-send-september-heading",
    createTime: localUnixSeconds(2026, 8, 30),
    content: `${septemberRelay.content}\n截止报名时间：2026年9月12日20:30`,
  };
  const sentInSeptemberWithoutMonthHeading = {
    messageId: "september-send-monthless-heading",
    createTime: localUnixSeconds(2026, 9, 9),
    content: "#接龙\n比赛报名接龙\n1. Alice alice\n2. Bob bob",
  };

  assert.equal(wechatRelayMatchesEventMonth(sentInSeptemberWithAugustHeading, 9, 2026), true);
  assert.equal(wechatRelayMatchesEventMonth(sentInAugustWithSeptemberHeading, 9, 2026), false);
  assert.equal(wechatRelayMatchesEventMonth(sentInSeptemberWithoutMonthHeading, 9, 2026), true);
  assert.equal(
    wechatRelayMatchesEventMonth(
      { ...septemberRelay, createTime: localUnixSeconds(2025, 9, 10) },
      9,
      2026,
    ),
    false,
  );
  assert.equal(isWechatRelayTemplateContent(sentInSeptemberWithAugustHeading.content), true);
});

test("finds the newest relay sent in the scheduled competition month", () => {
  const sentInSeptemberWithAugustHeading = {
    messageId: "september-send-august-heading",
    createTime: localUnixSeconds(2026, 9, 11),
    content: augustRelay.content,
  };
  const sentInAugustWithSeptemberHeading = {
    messageId: "august-send-september-heading",
    createTime: localUnixSeconds(2026, 8, 30),
    content: septemberRelay.content,
  };
  const relay = latestWechatRelayFromMessages(
    [augustRelay, septemberRelay, sentInSeptemberWithAugustHeading, sentInAugustWithSeptemberHeading],
    9,
    2026,
  );

  assert.equal(relay && relay.messageId, "september-send-august-heading");
  assert.equal(latestWechatRelayFromMessages([augustRelay], 9, 2026), null);
});

test("shows WeChat message times in an unambiguous China-local format", () => {
  const createTime = Date.UTC(2026, 7, 7, 13, 4, 10) / 1000;

  assert.equal(
    formatWechatMessageTimestamp({ createTime }),
    "2026-08-07 21:04:10",
  );
});

test("disables old per-line sync settings and drops their stale reference context", () => {
  const sync = sanitizeWechatRelaySync({
    syncModeVersion: 2,
    enabled: true,
    ready: true,
    ignoreLines: ["8. player"],
    referenceMessageId: "old-reference",
    referenceCreateTime: 100,
    referenceGroupUsername: "old-group",
    referenceDeadlineMs: 200,
  });

  assert.equal(sync.enabled, false);
  assert.equal(sync.syncModeVersion, 3);
  assert.equal(sync.referenceMessageId, "");
  assert.equal(sync.referenceCreateTime, 0);
  assert.equal(sync.referenceGroupUsername, "");
  assert.equal(sync.referenceDeadlineMs, 0);
  assert.equal("ignoreLines" in sync, false);
});
