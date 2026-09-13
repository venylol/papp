"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");
const sea = require("node:sea");

let renderers;
function getRenderers() {
  if (renderers) return renderers;
  const { createCanvas } = require("../ap-runtime/node_modules/@napi-rs/canvas");
  const sandbox = {
    document: { createElement(tag) {
      if (tag !== "canvas") throw new Error(`Unexpected renderer element: ${tag}`);
      const canvas = createCanvas(1, 1);
      canvas.style = {};
      const getContext = canvas.getContext.bind(canvas);
      canvas.getContext = type => {
        const ctx = getContext(type);
        // Skia's font shorthand parser accepts the traditional 100-step weights.
        // Browser variable weights such as 650/750/850 otherwise become huge sizes.
        return new Proxy(ctx, {
          get(target, property) {
            const value = Reflect.get(target, property, target);
            return typeof value === "function" ? value.bind(target) : value;
          },
          set(target, property, value) {
            if (property === "font") value = String(value).replace(/^(\d{3})\s+(?=\d+(?:\.\d+)?px\b)/,
              (_, weight) => `${Math.min(900, Math.max(100, Math.round(Number(weight) / 100) * 100))} `);
            return Reflect.set(target, property, value, target);
          },
        });
      };
      return canvas;
    } },
  };
  vm.createContext(sandbox);
  for (const file of ["papp-score-png-renderer.js", "papp-standings-png-renderer.js"]) {
    const source = sea.isSea()
      ? sea.getAsset(`web/${file}`, "utf8")
      : fs.readFileSync(path.join(__dirname, file), "utf8");
    vm.runInContext(source, sandbox, { filename: file });
  }
  renderers = { score: sandbox.PAPP_SCORE_PNG_RENDERER, standings: sandbox.PAPP_STANDINGS_PNG_RENDERER };
  return renderers;
}

let downloads;
function downloadsDirectory() {
  if (process.env.PAPP_AP_DOWNLOADS_DIR) return path.resolve(process.env.PAPP_AP_DOWNLOADS_DIR);
  if (downloads) return downloads;
  if (process.platform === "win32") {
    // Resolve the user's Windows Downloads folder, including redirected folders.
    const script = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); $p=(Get-ItemProperty -LiteralPath 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders').'{374DE290-123F-4565-9164-39C4925E467B}'; if (-not $p) { throw 'Downloads folder is not configured' }; [Environment]::ExpandEnvironmentVariables($p)";
    downloads = execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], { encoding: "utf8", windowsHide: true }).trim();
  } else downloads = path.join(os.homedir(), "Downloads");
  return downloads;
}

function safeName(value) {
  return String(value || "比赛").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").replace(/[. ]+$/, "").slice(0, 90) || "比赛";
}

async function exportImage(kind, payload, key) {
  const state = payload.state || {};
  const target = payload.target || {};
  const title = kind === "preliminary" ? "预赛排名" : kind === "overall" ? "最终排名"
    : `${target.stage === "semifinal" ? "半决赛" : target.stage === "placement" ? "决赛及三四名赛" : `第${target.round}轮`}_${kind === "pairings" ? "配对" : "比分"}`;
  const name = `${safeName(state.competitionName)}_${title}_${safeName(state.ap && state.ap.sessionId || "AP")}_${safeName(key)}.png`;
  const directory = downloadsDirectory();
  fs.mkdirSync(directory, { recursive: true });
  const file = path.join(directory, name);
  // Stable operation names allow recovery after a successful download but interrupted state save.
  if (fs.existsSync(file)) return { file, existing: true };
  const renderer = getRenderers();
  const pairings = payload.pairings || payload.result && payload.result.pairings || [];
  const common = { ...payload, competitionName: state.competitionName, round: target.round, stage: target.stage, pairings };
  let canvas;
  if (kind === "pairings") {
    const accounts = new Map(pairings.flatMap(p => [[p.black, p.blackAccount], [p.white, p.whiteAccount]]));
    canvas = renderer.score.buildPairingsCanvas(common, name => accounts.get(name) || "");
  } else if (kind === "scores") canvas = renderer.score.buildScoreCanvas(pairings, common);
  else if (kind === "preliminary" || kind === "overall") {
    canvas = renderer.standings.buildStandingsCanvas({
      ...common,
      standings: payload.standings || payload.result && payload.result.standings,
      showPreliminaryRank: kind === "overall" && state.tournamentParameters && state.tournamentParameters.hasSemifinalAndFinal,
      labels: { title },
    });
  } else throw new Error(`Unknown AP image kind: ${kind}`);
  const png = await canvas.encode("png");
  fs.writeFileSync(file, png, { flag: "wx" });
  return { file, width: canvas.width, height: canvas.height };
}

module.exports = { exportImage, downloadsDirectory, getRenderers };
