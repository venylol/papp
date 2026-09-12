"use strict";

const clone = value => value === undefined ? undefined : structuredClone(value);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const object = value => value && typeof value === "object" && !Array.isArray(value);

// Reapply only the AP changes to the latest state. Concurrent referee edits win
// at the field level; untouched players, rounds and planned withdrawals survive.
function mergeChanges(base, proposed, current) {
  if (equal(base, proposed)) return clone(current);
  if (equal(base, current)) return clone(proposed);
  // A referee's score edit invalidates the receipt for the whole game, not
  // merely its numeric field. Never combine a new human score with an old AP receipt.
  if (object(base) && object(current) && ("blackScore" in base || "whiteScore" in base)) return clone(current);
  if (object(base) && object(proposed) && object(current)) {
    const result = clone(current);
    for (const key of new Set([...Object.keys(base), ...Object.keys(proposed)])) {
      if (["savedAt", "localSync"].includes(key)) continue;
      const value = mergeChanges(base[key], proposed[key], current[key]);
      if (value === undefined) delete result[key];
      else result[key] = value;
    }
    return result;
  }
  if (Array.isArray(base) && Array.isArray(proposed) && Array.isArray(current)) {
    const all = [...base, ...proposed, ...current];
    const key = ["id", "round", "playerId"].find(k => all.every(v => object(v) && v[k] !== undefined) &&
      [base, proposed, current].every(rows => new Set(rows.map(v => String(v[k]))).size === rows.length));
    if (key) {
      const index = rows => new Map(rows.map(v => [String(v[key]), v]));
      const old = index(base), next = index(proposed), latest = index(current);
      const rows = [];
      for (const id of new Set([...latest.keys(), ...next.keys()])) {
        const value = mergeChanges(old.get(id), next.get(id), latest.get(id));
        if (value !== undefined) rows.push(value);
      }
      return rows;
    }
  }
  return clone(current);
}

function validateAp(state) {
  if (!state) return ["尚未保存比赛数据"];
  const missing = [];
  if (!String(state.competitionName || "").trim()) missing.push("比赛名称");
  const schedule = state.eventSchedule || {};
  let previous = -Infinity;
  for (const [key, label] of [["registrationDeadline", "报名截止时间"], ["checkinStart", "签到开始时间"], ["checkinDeadline", "签到截止时间"], ["competitionStart", "比赛正式开始时间"]]) {
    const time = Date.parse(schedule[key]);
    if (!Number.isFinite(time)) missing.push(label);
    else if (time < previous) missing.push(`${label}不能早于前一时间`);
    previous = time;
  }
  const group = schedule.wechatGroup || {};
  if (!(group.username || group.queryIndex)) missing.push("比赛群聊");
  if (!Array.isArray(state.mapping && state.mapping.rows) || !state.mapping.rows.length) missing.push("映射表");
  const parameters = state.tournamentParameters || {};
  if (!["auto", "on", "off"].includes(parameters.semifinalAndFinalMode)) missing.push("半决赛及决赛设置");
  if (parameters.brightwellConstant === "" || parameters.brightwellConstant == null || !Number.isFinite(Number(parameters.brightwellConstant)) || Number(parameters.brightwellConstant) < 0) missing.push("Brightwell 常数");
  if (!Number.isInteger(Number(state.scoreHelper && state.scoreHelper.preliminaryRoundCount)) || Number(state.scoreHelper.preliminaryRoundCount) < 1) missing.push("预赛轮数");
  const seconds = state.ui && state.ui.oqPollSeconds;
  if (seconds != null && (!Number.isFinite(Number(seconds)) || Number(seconds) < 5)) missing.push("OQ 轮询间隔（至少 5 秒，默认 15 秒）");
  return missing;
}

function pendingCheckins(state) {
  return (state.wechatAutoCheckin && state.wechatAutoCheckin.items || []).filter(item => item.status === "pending").length;
}

function targetLabel(target) {
  if (target.stage === "semifinal") return "半决赛即将开始";
  if (target.stage === "placement") return "决赛及三四名赛即将开始";
  if (target.stage === "overall") return "即将生成最终排名";
  return `第 ${target.round} 轮比赛即将开始`;
}

class ApCoordinator {
  constructor({ request, exportImage, checkin, tournament, now = Date.now }) {
    this.request = request;
    this.exportImage = exportImage;
    this.checkin = checkin;
    this.tournament = tournament;
    this.now = now;
    this.bootChecked = false;
    this.busy = false;
    this.controlEpoch = 0;
    this.lastCompletedEgPollAt = 0;
  }

  async read() { return (await this.request("/api/state")).state; }
  async save(base, state, source = "script") {
    if (equal(base, state)) return;
    return this.request("/api/state", { operation: "ap-patch", baseState: base, state, source });
  }
  async status() {
    const state = await this.read();
    return { ok: true, ap: state && state.ap || { enabled: false, status: "off", countdown: null }, validation: validateAp(state), oqPollSeconds: Number(state && state.ui && state.ui.oqPollSeconds) || 15 };
  }

  async control(action) {
    this.controlEpoch++;
    const base = await this.read();
    if (!base) throw new Error("请先保存比赛参数及名单");
    const state = clone(base);
    state.ap = state.ap || {};
    const ap = state.ap;
    if (action === "enable") {
      const missing = validateAp(state);
      if (missing.length) throw new Error(`无法进入 AP：${missing.join("、")}`);
      if (ap.enabled) return this.status();
      Object.assign(ap, { enabled: true, status: "running", phase: state.step, message: "AP 已开启", countdown: null,
        sessionId: ap.sessionId || `${this.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}` });
      state.ui = { ...state.ui, oqPollSeconds: Number(state.ui && state.ui.oqPollSeconds) || 15 };
    } else if (action === "stop") {
      Object.assign(ap, { enabled: false, status: "off", countdown: null, message: "AP 已关闭" });
    } else {
      if (!ap.enabled) throw new Error("AP 尚未开启");
      if (action === "pause") Object.assign(ap, { status: "paused", countdown: null, message: "阶段推进已暂停；本轮 OQ 和 EG 继续运行" });
      else if (action === "resume") {
        const missing = validateAp(state);
        if (missing.length) throw new Error(`请先修正参数：${missing.join("、")}`);
        Object.assign(ap, { status: "running", countdown: null, message: "AP 已恢复，待条件满足后重新倒计时" });
      } else if (action === "confirm") {
        if (!ap.countdown || ap.status !== "countdown") throw new Error("当前没有待确认的比赛阶段");
        ap.countdown.deadlineAt = this.now();
      } else throw new Error("未知 AP 操作");
    }
    ap.updatedAt = this.now();
    this.bootChecked = true;
    await this.save(base, state, "human");
    return this.status();
  }

  async tick() {
    if (this.busy) return;
    this.busy = true;
    let base, state;
    const epoch = this.controlEpoch;
    try {
      base = await this.read();
      if (!base || !base.ap || !base.ap.enabled) { this.bootChecked = true; return; }
      state = clone(base);
      if (!this.bootChecked) {
        this.bootChecked = true;
        Object.assign(state.ap, { status: "paused", countdown: null, message: "本地服务已恢复，请确认后继续 AP" });
        await this.save(base, state);
        return;
      }
      if (state.ap.status === "complete") {
        if (this.now() - this.lastCompletedEgPollAt >= 15000) {
          this.lastCompletedEgPollAt = this.now();
          try {
            const result = await this.request("/api/papp/eg/status", {state});
            if (result.analysis) state.egAnalysis = clone(result.analysis);
            await this.save(base, state);
          } catch (_) { /* EG remains independent of the completed tournament. */ }
        }
        return;
      }
      const nowMs = this.now();
      const options = { nowMs, request: this.request, exportImage: this.exportImage, allowCommit: state.ap.status !== "error",
        assertCurrent: async () => {
          const latest = await this.read();
          const keys = ["step", "players", "mapping", "tournamentParameters", "eventSchedule", "scoreHelper", "playoffRegistration", "plannedWithdrawals", "wechatAutoCheckin"];
          if (keys.some(key => !equal(latest[key], base[key]))) throw new Error("裁判已修改比赛数据，AP 已暂停；请检查后恢复");
          if (!latest.ap || !latest.ap.enabled) throw new Error("AP 已关闭");
        } };
      let next;
      const errors = validateAp(state);
      if (errors.length) {
        Object.assign(state.ap, { status: "error", countdown: null, message: `请修正 AP 参数：${errors.join("、")}` });
      } else if (state.step === "checkin") {
        const result = await this.checkin.tick(state, options);
        state = result.state;
        state.ap.phase = "checkin";
        const pending = pendingCheckins(state);
        if (result.error) state.ap.message = result.error;
        else if (pending) state.ap.message = `有 ${pending} 条签到 pending，等待裁判处理`;
        else if (!result.ready) state.ap.message = "正在刷新接龙或等待签到宽限结束";
        else state.ap.message = "签到已结束，等待正式开赛时间";
        if (result.ready && !result.error && !pending && nowMs >= Date.parse(state.eventSchedule.competitionStart)) next = { stage: "preliminary", round: 1 };
      } else if (["score-helper", "final-registration"].includes(state.step)) {
        // A referee may enter round one before the automatic check-in window closes.
        const result = await this.tournament.tick(state, options);
        state = result.state;
        state.ap.phase = "tournament";
        if (result.error) Object.assign(state.ap, { status: "error", countdown: null, message: result.error });
        else next = result.next;
      } else {
        state.ap.message = "等待导入签到列表";
        state.ap.countdown = null;
      }
      const ap = state.ap;
      if (ap.status !== "paused" && ap.status !== "error" && ap.status !== "complete") {
        if (!next) { ap.countdown = null; ap.status = "running"; }
        else if (!ap.countdown || !equal(ap.countdown.target, next)) {
          ap.countdown = { target: next, label: targetLabel(next), deadlineAt: this.now() + 10_000 };
          ap.status = "countdown";
          ap.message = `${targetLabel(next)}，10 秒内可取消`;
        } else if (nowMs >= Number(ap.countdown.deadlineAt) && epoch === this.controlEpoch) {
          // Check again after asynchronous OQ/check-in work so a referee's cancel
          // or manual step change cannot launch an obsolete next round.
          const latest = await this.read();
          const relevantFields = ["step", "players", "mapping", "tournamentParameters", "eventSchedule", "scoreHelper", "playoffRegistration", "plannedWithdrawals", "wechatAutoCheckin"];
          const refereeChangedState = relevantFields.some(key => !equal(latest[key], base[key]));
          if (refereeChangedState) {
            Object.assign(state.ap, { status: "paused", countdown: null, message: "裁判已更新比赛数据，阶段推进已暂停，请确认后恢复 AP" });
          } else if (epoch === this.controlEpoch && equal(latest.ap.countdown, base.ap.countdown)) {
            const result = await this.tournament.enterNext(state, next, options);
            state = result.state;
            if (result.error) Object.assign(state.ap, { status: "error", countdown: null, message: result.error });
            else Object.assign(state.ap, { countdown: null, status: next.stage === "overall" ? "complete" : "running", message: next.stage === "overall" ? "比赛已完成，最终排名图已导出" : "已进入比赛阶段" });
          }
        }
      }
      if (epoch !== this.controlEpoch) {
        // Preserve the latest control command while retaining completed collection.
        const current = await this.read();
        state.ap = { ...state.ap, enabled: current.ap.enabled, status: current.ap.status, countdown: current.ap.countdown, message: current.ap.message };
      }
      await this.save(base, state);
    } catch (error) {
      if (base && state) {
        Object.assign(state.ap, { status: "error", countdown: null, message: String(error.message || error) });
        await this.save(base, state);
      } else throw error;
    } finally { this.busy = false; }
  }
}

module.exports = { ApCoordinator, mergeChanges, validateAp, pendingCheckins, targetLabel };
