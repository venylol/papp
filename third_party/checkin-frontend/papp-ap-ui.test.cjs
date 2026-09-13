"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { remainingSeconds } = require("./papp-ap-ui.js");

test("countdown uses server deadline, including backgrounded and expired pages", () => {
  assert.equal(remainingSeconds({ deadlineAt: 10000 }, 0), 10);
  assert.equal(remainingSeconds({ deadlineAt: 10000 }, 9001), 1);
  assert.equal(remainingSeconds({ deadlineAt: 10000 }, 13000), 0);
  assert.equal(remainingSeconds({ deadlineAt: "2026-09-12T13:00:10Z" }, Date.parse("2026-09-12T13:00:00Z")), 10);
});

function harness() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      id, dataset: {}, events: {}, open: false, hidden: false,
      addEventListener(name, callback) { this.events[name] = callback; },
      setAttribute() {}, classList: { toggle() {} },
      showModal() { this.open = true; }, close() { this.open = false; },
    });
    return elements.get(id);
  }
  const actions = ["enable", "resume", "pause", "stop", "confirm"].map((action) => {
    const control = element(action);
    control.dataset.apAction = action;
    return control;
  });
  element("ap-dialog").querySelectorAll = () => actions;
  const state = { ap: {}, ui: { oqPollSeconds: 15 } };
  let response = { ok: true, ap: { enabled: false }, validation: [] };
  let postResponse = null;
  const calls = [];
  let persisted = 0;
  const context = {
    module: { exports: {} }, console,
    document: { getElementById: element, addEventListener() {} },
    window: { setInterval() {}, clearInterval() {} },
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      if (options.method === "POST") {
        const action = JSON.parse(options.body).action;
        if (postResponse && !postResponse.ok) return { ok: false, json: async () => postResponse };
        response = postResponse || { ok: true, ap: { enabled: true, status: action === "pause" ? "paused" : "running" }, validation: [] };
      }
      return { ok: true, json: async () => response };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "papp-ap-ui.js"), "utf8"), context);
  const ui = context.module.exports.mount({
    getState: () => state,
    persist: async () => { persisted++; return true; },
    onStatus: (ap) => { state.ap = ap; },
  });
  return { element, calls, ui, setResponse(value) { response = value; }, setPostResponse(value) { postResponse = value; }, get persisted() { return persisted; } };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const countdownResponse = (round, deadlineAt = Date.now() + 10000) => ({
  ok: true, ap: { enabled: true, status: "countdown", countdown: { target: { stage: "preliminary", round }, deadlineAt } },
});

test("successful confirm dismisses the modal through same-target deadline echoes; later stage opens", async () => {
  const h = harness();
  await settle();
  h.setResponse(countdownResponse(2));
  await h.ui.refresh();
  assert.equal(h.element("ap-dialog").open, true);
  h.setPostResponse(countdownResponse(2, Date.now()));
  h.element("confirm").events.click();
  await settle();
  assert.equal(h.element("ap-dialog").open, false);
  assert.equal(h.persisted, 1);
  h.setResponse(countdownResponse(2, Date.now() - 1));
  await h.ui.refresh();
  await h.ui.refresh();
  assert.equal(h.element("ap-dialog").open, false);
  h.element("btn-ap-mode").events.click();
  await settle();
  assert.equal(h.element("ap-dialog").open, true, "toolbar may intentionally reopen the same target");
  h.element("ap-dialog").close();
  h.setResponse(countdownResponse(3));
  await h.ui.refresh();
  assert.equal(h.element("ap-dialog").open, true);
});

test("automatic transition completion dismisses its countdown", async () => {
  const h = harness();
  await settle();
  h.setResponse(countdownResponse(2));
  await h.ui.refresh();
  h.setResponse({ ok: true, ap: { enabled: true, status: "running", countdown: null } });
  await h.ui.refresh();
  assert.equal(h.element("ap-dialog").open, false);
});

test("a confirm response reporting a paused PAPP error keeps the modal open", async () => {
  const h = harness();
  await settle();
  h.setResponse(countdownResponse(2));
  await h.ui.refresh();
  h.setPostResponse({ ok: true, ap: { enabled: true, status: "error", countdown: null, message: "PAPP 写入失败，已暂停" } });
  h.element("confirm").events.click();
  await settle();
  assert.equal(h.element("ap-dialog").open, true);
  assert.equal(h.element("ap-status").textContent, "PAPP 写入失败，已暂停");
  assert.equal(h.element("resume").hidden, false);
});

test("failed confirm keeps its error visible and does not suppress later prompts", async () => {
  const h = harness();
  await settle();
  h.setResponse(countdownResponse(2));
  await h.ui.refresh();
  h.setPostResponse({ ok: false, error: "PAPP 尚未就绪" });
  h.element("confirm").events.click();
  await settle();
  assert.equal(h.element("ap-dialog").open, true);
  assert.equal(h.element("ap-errors").textContent, "PAPP 尚未就绪");
  h.element("ap-dialog").close();
  h.setResponse(countdownResponse(2, Date.now() + 20000));
  await h.ui.refresh();
  assert.equal(h.element("ap-dialog").open, true);
});

for (const action of ["enable", "resume"]) {
  test(`${action} dismisses settings but keeps a returned countdown available to cancel`, async () => {
    const h = harness();
    await settle();
    h.element("btn-ap-mode").events.click();
    await settle();
    h.element(action).events.click();
    await settle();
    assert.equal(h.element("ap-dialog").open, false);
    h.element("btn-ap-mode").events.click();
    await settle();
    h.setPostResponse(countdownResponse(1));
    h.element(action).events.click();
    await settle();
    assert.equal(h.element("ap-dialog").open, true);
    assert.equal(h.element("pause").hidden, false);
  });
}

test("successful stop closes the AP settings modal instead of showing enable AP", async () => {
  const h = harness();
  await settle();
  h.setResponse({ ok: true, ap: { enabled: true, status: "running" }, validation: [] });
  await h.ui.refresh();
  h.element("btn-ap-mode").events.click();
  await settle();
  assert.equal(h.element("ap-dialog").open, true);
  h.setPostResponse({ ok: true, ap: { enabled: false, status: "off", message: "AP 已关闭" }, validation: [] });
  h.element("stop").events.click();
  await settle();
  assert.equal(h.element("ap-dialog").open, false);
  assert.equal(h.element("enable").hidden, false);
});

test("missing parameters block enable and show all missing fields", async () => {
  const h = harness();
  await settle();
  h.setResponse({ ok: true, ap: { enabled: false }, validation: ["缺少映射表", "缺少开赛时间"] });
  await h.ui.refresh();
  assert.equal(h.element("enable").disabled, true);
  assert.match(h.element("ap-errors").textContent, /缺少映射表\n缺少开赛时间/);
  assert.match(h.element("ap-interval").textContent, /15 秒/);
});

test("server countdown opens modal and Escape pauses without posting shared state", async () => {
  const h = harness();
  await settle();
  h.setResponse({ ok: true, ap: { enabled: true, status: "countdown", countdown: { target: "round-2", label: "第二轮即将开始", deadlineAt: Date.now() + 10000 } } });
  await h.ui.refresh();
  assert.equal(h.element("ap-dialog").open, true);
  assert.equal(h.element("ap-dialog-title").textContent, "第二轮即将开始");
  assert.equal(h.element("ap-close").hidden, true);
  let prevented = false;
  h.element("ap-dialog").events.cancel({ preventDefault() { prevented = true; } });
  await settle();
  assert.equal(prevented, true);
  assert.equal(JSON.parse(h.calls.at(-1).options.body).action, "pause");
  assert.equal(h.persisted, 0);
  assert.equal(h.element("resume").hidden, false);
});

test("expired countdown waits for server and never advances from a client timer", async () => {
  const h = harness();
  await settle();
  h.setResponse({ ok: true, ap: { enabled: true, countdown: { target: "round-2", deadlineAt: 1 } } });
  await h.ui.refresh();
  assert.match(h.element("ap-countdown").textContent, /^0 秒/);
  assert.equal(h.calls.some(({ options }) => options.method === "POST"), false);
});

test("AP metadata survives normalization without changing existing round pairings or timestamps", () => {
  const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8")
    .replace("module.exports = {", "module.exports = { initialState, sanitizeLoadedState,");
  const context = { module: { exports: {} }, console, require };
  vm.runInNewContext(source, context);
  const { initialState, sanitizeLoadedState } = context.module.exports;
  const saved = sanitizeLoadedState(initialState());
  saved.scoreHelper.rounds[0].roundStartAt = "2026-09-12 21:06:34";
  saved.ap = { enabled: true, status: "paused", countdown: null, exports: [{ path: "Downloads/比赛.png" }], futureField: { keep: true } };
  const before = JSON.stringify(saved.scoreHelper.rounds);
  const sanitized = sanitizeLoadedState(saved);
  assert.equal(JSON.stringify(sanitized.ap), JSON.stringify(saved.ap));
  assert.notEqual(sanitized.ap, saved.ap);
  assert.equal(JSON.stringify(sanitized.scoreHelper.rounds), before);
});
