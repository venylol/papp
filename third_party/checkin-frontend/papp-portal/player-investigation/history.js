(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const refreshButton = $("#btn-history-refresh");
  const status = $("#investigation-history-status");
  const list = $("#investigation-history-list");
  const error = $("#investigation-history-error");

  function text(value) {
    return String(value ?? "").replace(/\s+/gu, " ").trim();
  }

  function displayCount(value) {
    const count = Number(value);
    return Number.isFinite(count) ? String(count) : "—";
  }

  function formatDate(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return text(value) || "时间未知";
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function makeHistoryItem(report) {
    const single = report.type === "single";
    if (!single && report.type !== "batch") return null;

    const destination = single
      ? `./analysis.html?runId=${encodeURIComponent(report.runId)}&from=history`
      : `./batch-analysis.html?batchId=${encodeURIComponent(report.batchId)}&from=history`;
    const titleText = single
      ? text(report.account) || text(report.runId) || "单人调查"
      : text(report.competitionName) || text(report.tournamentFile) || "多人哨兵分析";
    const typeText = single
      ? report.mode === "sentinel" ? "单人哨兵报告" : "单人举报局报告"
      : "批量哨兵报告";
    const detailText = single
      ? `举报局 ${displayCount(report.reportedGameCount)} 局 · 对照局 ${displayCount(report.controlGameCount)} 局${text(report.classification) ? ` · ${text(report.classification)}` : ""}`
      : `成功 ${displayCount(report.completedCount)} 位 · 失败 ${displayCount(report.failedCount)} 位 · 共 ${displayCount(report.totalCount)} 位`;

    const link = document.createElement("a");
    link.className = "investigation-history-item";
    link.href = destination;
    link.setAttribute("role", "listitem");
    link.setAttribute("aria-label", `查看${typeText}：${titleText}`);

    const main = document.createElement("div");
    main.className = "investigation-history-item__main";
    const topline = document.createElement("div");
    topline.className = "investigation-history-item__topline";
    const type = document.createElement("span");
    type.className = "investigation-history-item__type";
    type.textContent = typeText;
    const date = document.createElement("time");
    date.className = "investigation-history-item__date";
    date.dateTime = text(report.generatedAt);
    date.textContent = formatDate(report.generatedAt);
    topline.append(type, date);

    const title = document.createElement("h3");
    title.className = "investigation-history-item__title";
    title.textContent = titleText;
    const detail = document.createElement("p");
    detail.className = "investigation-history-item__detail";
    detail.textContent = detailText;
    main.append(topline, title, detail);

    const action = document.createElement("span");
    action.className = "investigation-history-item__action";
    action.textContent = "查看报告 →";
    link.append(main, action);
    return link;
  }

  async function loadHistory() {
    refreshButton.disabled = true;
    status.textContent = "正在读取本机报告…";
    error.textContent = "";
    list.replaceChildren();
    try {
      const response = await fetch("/api/player-investigation/history", { cache: "no-store" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok !== true || !Array.isArray(payload.reports)) {
        throw new Error(payload.error || payload.detail || "本地服务返回了无效的历史报告列表");
      }
      const items = payload.reports.map(makeHistoryItem).filter(Boolean);
      list.replaceChildren(...items);
      status.textContent = items.length ? `找到 ${items.length} 份历史报告。` : "目前没有已完成的历史分析报告。";
    } catch (failure) {
      status.textContent = "历史报告列表读取失败。";
      error.textContent = text(failure && failure.message) || "请检查本地服务状态后重试。";
    } finally {
      refreshButton.disabled = false;
    }
  }

  refreshButton.addEventListener("click", loadHistory);
  void loadHistory();
})();
