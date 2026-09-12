(function (root) {
  "use strict";

  function remainingSeconds(countdown, now = Date.now()) {
    if (!countdown) return 0;
    const value = countdown.deadlineAt;
    const deadline = typeof value === "number" ? value : Date.parse(value);
    return Number.isFinite(deadline) ? Math.max(0, Math.ceil((deadline - now) / 1000)) : 0;
  }

  function modeLabel(ap) {
    if (!ap || !ap.enabled) return "AP 自动编排";
    if (ap.countdown) return "AP 等待确认";
    if (ap.status === "paused" || ap.status === "error") return "AP 已暂停";
    if (ap.status === "complete") return "AP 已完成";
    return "AP 运行中";
  }

  function mount(options) {
    const button = document.getElementById("btn-ap-mode");
    const dialog = document.getElementById("ap-dialog");
    if (!button || !dialog) return;
    const el = (id) => document.getElementById(id);
    let snapshot = { ap: options.getState().ap || {}, validation: [] };
    let busy = false;
    let connected = false;
    let pollInFlight = false;
    let controlGeneration = 0;
    let lastCountdown = "";
    let confirmedTarget = "";
    let lastPaused = false;
    let errorText = "";
    const actions = Array.from(dialog.querySelectorAll("[data-ap-action]"));

    function open() {
      if (!dialog.open) dialog.showModal();
      render();
    }

    function render() {
      const ap = snapshot.ap || {};
      const countdown = ap.countdown;
      const paused = ap.status === "paused" || ap.status === "error";
      const validation = Array.isArray(snapshot.validation) ? snapshot.validation : [];
      button.setAttribute("aria-pressed", String(Boolean(ap.enabled)));
      button.classList.toggle("ap-mode--active", Boolean(ap.enabled));
      button.title = modeLabel(ap) + (ap.message ? `：${ap.message}` : "");
      button.setAttribute("aria-label", button.title);
      el("ap-dialog-title").textContent = countdown ? countdown.label || "即将进入下一阶段，是否确认？" : modeLabel(ap);
      el("ap-description").hidden = Boolean(ap.enabled);
      el("ap-status").textContent = ap.message || (ap.enabled ? "本地服务正在执行自动编排。" : "请先确认比赛参数、映射表和 AP 参数设置正确。");
      el("ap-interval").textContent = `OQ 轮询间隔：${Number(options.getState().ui?.oqPollSeconds) || 15} 秒（默认 15 秒）。`;
      el("ap-countdown").hidden = !countdown;
      el("ap-countdown").textContent = countdown ? `${remainingSeconds(countdown)} 秒后自动进入；取消只暂停阶段推进。` : "";
      el("ap-errors").textContent = errorText || (!ap.enabled && validation.length ? `尚未满足开启条件：\n${validation.join("\n")}` : "");
      actions.forEach((control) => {
        const action = control.dataset.apAction;
        control.hidden = action === "enable" ? Boolean(ap.enabled)
          : action === "resume" ? !ap.enabled || !paused
          : action === "pause" ? !ap.enabled || paused
          : action === "confirm" ? !countdown
          : action === "stop" ? !ap.enabled : false;
        control.disabled = busy || !connected || (action === "enable" && validation.length > 0);
        if (action === "pause") control.textContent = countdown ? "取消自动进入" : "暂停阶段推进";
      });
      el("ap-close").hidden = Boolean(countdown);
      el("ap-close").disabled = busy;
    }

    function accept(result) {
      snapshot = result;
      connected = true;
      errorText = "";
      options.onStatus(result.ap || {});
      const ap = result.ap || {};
      const target = ap.countdown ? JSON.stringify(ap.countdown.target) : "";
      const key = ap.countdown ? `${target}:${ap.countdown.deadlineAt}` : "";
      // Confirm sets the server deadline to now; that echo is still the same
      // accepted transition, not a fresh prompt. A later stage can prompt again.
      if (!key || target !== confirmedTarget) confirmedTarget = "";
      const paused = ap.enabled && (ap.status === "paused" || ap.status === "error");
      if ((key && key !== lastCountdown && target !== confirmedTarget) || (paused && !lastPaused)) open();
      if (!key && lastCountdown && dialog.open && !paused) dialog.close();
      lastCountdown = key;
      lastPaused = paused;
      render();
    }

    async function readStatus() {
      if (pollInFlight || busy) return;
      pollInFlight = true;
      const generation = controlGeneration;
      try {
        const response = await fetch("/api/ap/status", { cache: "no-store" });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || `HTTP ${response.status}`);
        if (generation === controlGeneration) accept(result);
      } catch (error) {
        connected = false;
        errorText = `无法连接 AP 服务：${error.message}。请检查本地服务；当前运行状态尚未确认。`;
        render();
      } finally {
        pollInFlight = false;
      }
    }

    async function control(action) {
      if (busy || !connected) return;
      busy = true;
      const requestedTarget = snapshot.ap?.countdown ? JSON.stringify(snapshot.ap.countdown.target) : "";
      controlGeneration += 1;
      errorText = "";
      render();
      try {
        if (action === "enable" || action === "resume" || action === "confirm") {
          if (!await options.persist()) throw new Error("比赛状态尚未保存，请等待同步完成后重试");
        }
        const response = await fetch("/api/ap/control", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action }),
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || `HTTP ${response.status}`);
        if (action === "confirm") {
          confirmedTarget = requestedTarget;
          dialog.close();
        }
        accept(result);
        if ((action === "enable" || action === "resume") && !result.ap?.countdown) dialog.close();
      } catch (error) {
        errorText = error.message;
        open();
      } finally {
        busy = false;
        render();
      }
    }

    button.addEventListener("click", () => { open(); void readStatus(); });
    actions.forEach((controlButton) => controlButton.addEventListener("click", () => void control(controlButton.dataset.apAction)));
    el("ap-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("cancel", (event) => {
      if (snapshot.ap?.countdown || busy) {
        event.preventDefault();
        if (!busy) void control("pause");
      }
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) void readStatus(); });
    render();
    void readStatus();
    const pollTimer = window.setInterval(() => void readStatus(), 1000);
    const clockTimer = window.setInterval(() => { if (dialog.open && snapshot.ap?.countdown) render(); }, 200);
    return { refresh: readStatus, dispose() { window.clearInterval(pollTimer); window.clearInterval(clockTimer); } };
  }

  const api = { mount, remainingSeconds, modeLabel };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PappApUi = api;
})(typeof window === "object" ? window : globalThis);
