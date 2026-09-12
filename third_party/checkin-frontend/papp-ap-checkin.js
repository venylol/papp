"use strict";

// Server-side orchestration of the existing relay parser and check-in rules.
// This module never saves state: the caller must publish via POST /api/state.
const logic = require("./app.js");
const POLL_MS = 10_000;
const LEAD_MS = 5 * 60_000;
const TRAIL_MS = 60_000;

async function messagesInRange(request, group, startMs, endMs, relayOnly) {
  const messages = [];
  const seen = new Set();
  const limit = 2000;
  for (let offset = 0; offset <= 10_000; offset += limit) {
    const query = new URLSearchParams({ group, limit: String(limit), offset: String(offset),
      startTime: String(Math.floor(startMs / 1000)), endTime: String(Math.floor(endMs / 1000)) });
    if (relayOnly) query.set("relayOnly", "true");
    const result = await request(`/api/wechat-chat-messages?${query}`);
    if (!result || result.ok !== true || !Array.isArray(result.messages)) {
      throw new Error(result && (result.detail || result.error) || "读取微信群消息失败");
    }
    for (const message of result.messages) {
      const timestamp = logic.wechatMessageTimestampMs(message);
      if (!message.messageId || seen.has(message.messageId) || timestamp < startMs || timestamp > endMs) continue;
      seen.add(message.messageId);
      messages.push(message);
    }
    if (result.messages.length < limit) return messages;
  }
  throw new Error("AP 查询消息超过读取上限，未完成最终复查");
}

function mergeRelay(state, checkpoint, message) {
  const sync = state.wechatRelaySync || {};
  const previousTime = Math.max(Number(checkpoint.lastRelayCreateTime) || 0, Number(sync.lastProcessedCreateTime) || 0);
  if (!message || message.messageId === checkpoint.lastRelayMessageId ||
      message.messageId === sync.lastProcessedMessageId || Number(message.createTime) < previousTime) return;
  const incoming = logic.parseImportTextsDetailed("", message.content).players || [];
  if (!incoming.length) return;
  const missing = state.players.filter(player => !incoming.some(candidate => logic.wechatRelayPlayersMatch(player, candidate)));
  checkpoint.lastRelayMessageId = message.messageId;
  checkpoint.lastRelayCreateTime = Number(message.createTime);
  if (missing.length) {
    checkpoint.relayWarning = `新接龙缺少 ${missing.length} 名现有选手，本次未合并。`;
    return;
  }
  checkpoint.relayWarning = "";
  const additions = incoming.filter(candidate => !state.players.some(player => logic.wechatRelayPlayersMatch(player, candidate)));
  let nextId = Math.max(Number(state.nextPlayerId) || 1, ...state.players.map(player => (Number(player.id) || 0) + 1));
  for (const candidate of additions) {
    state.players.push({ id: nextId++, displayName: candidate.displayName || "", account: candidate.account || "",
      club: candidate.club || "", platform: candidate.platform || "", group: candidate.group || "未分组",
      checkedIn: false, checkedInAt: null, isNew: true });
  }
  state.nextPlayerId = nextId;
  if (additions.length) logic.reconcileMappingRowsWithCandidates(state.mapping, state.players);
  state.wechatRelaySync = { ...state.wechatRelaySync, lastProcessedMessageId: message.messageId,
    lastProcessedCreateTime: Number(message.createTime) || 0 };
}

/** tick returns a detached state and a ready flag after the final grace-window scan.
 * request(path, body?) returns parsed local API JSON; it must reject transport errors.
 * Existing pending items remain in wechatAutoCheckin.items for the referee UI.
 */
async function tick(input, { nowMs = Date.now(), request }) {
  const state = structuredClone(input);
  const before = JSON.stringify(input);
  const finish = (ready, error) => ({ state, changed: JSON.stringify(state) !== before, ready, ...(error ? { error } : {}) });
  if (state.step !== "checkin") return finish(false);
  if (!Array.isArray(state.players) || !state.players.length) return finish(false);
  const schedule = state.eventSchedule || {};
  const registrationMs = Date.parse(schedule.registrationDeadline);
  const startMs = Date.parse(schedule.checkinStart);
  const deadlineMs = Date.parse(schedule.checkinDeadline);
  const group = schedule.wechatGroup || {};
  const groupUsername = group.username || group.queryIndex;
  if (![registrationMs, startMs, deadlineMs, nowMs].every(Number.isFinite) || startMs > deadlineMs || !groupUsername) {
    return finish(false, "AP 报名、签到时间或比赛群设置无效");
  }
  state.ap ||= {};
  const scope = `${groupUsername}|${registrationMs}|${startMs}|${deadlineMs}`;
  if (state.ap.checkin?.scope !== scope) state.ap.checkin = { scope };
  const checkpoint = state.ap.checkin;
  state.wechatAutoCheckin ||= logic.createDefaultWechatAutoCheckin();
  // Browser polling is disabled while the service owns this workflow.
  state.wechatAutoCheckin.enabled = false;
  state.wechatAutoCheckin.groupUsername = groupUsername;
  if (state.wechatRelaySync) state.wechatRelaySync.enabled = false;
  try {
    if (!checkpoint.relayDone && (nowMs >= registrationMs || !checkpoint.relayPolledAt || nowMs - checkpoint.relayPolledAt >= POLL_MS)) {
      const anchor = new Date(registrationMs);
      const monthStart = new Date(anchor.getFullYear(), anchor.getMonth(), 1).getTime();
      const endMs = Math.min(nowMs, registrationMs);
      const messages = await messagesInRange(request, groupUsername, monthStart, endMs, true);
      const latest = logic.latestWechatRelayFromMessages(messages, anchor.getMonth() + 1, anchor.getFullYear());
      mergeRelay(state, checkpoint, latest);
      checkpoint.relayPolledAt = nowMs;
      checkpoint.relayDone = nowMs >= registrationMs;
    }
    if (nowMs >= startMs && !checkpoint.done && (nowMs >= deadlineMs + TRAIL_MS || !checkpoint.polledAt || nowMs - checkpoint.polledAt >= POLL_MS)) {
      const messages = await messagesInRange(request, groupUsername, startMs - LEAD_MS, Math.min(nowMs, deadlineMs + TRAIL_MS), false);
      const result = logic.reconcileWechatAutoCheckinMessages({ autoCheckin: state.wechatAutoCheckin, messages,
        mapping: state.mapping, rosterPlayers: state.players, groupUsername, selectedGroupName: group.displayName,
        checkinStartMs: startMs, checkinDeadlineMs: deadlineMs, nowMs });
      state.wechatAutoCheckin.items = result.items;
      checkpoint.polledAt = nowMs;
      checkpoint.done = nowMs >= deadlineMs + TRAIL_MS;
    }
    return finish(Boolean(checkpoint.relayDone && checkpoint.done));
  } catch (error) {
    return finish(false, error.message);
  }
}

module.exports = { tick };
