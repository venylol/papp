(function () {
  "use strict";

  if (typeof window === "undefined") return;

  const previous =
    window.PAPP_TOURNAMENT_ADAPTER && typeof window.PAPP_TOURNAMENT_ADAPTER === "object"
      ? window.PAPP_TOURNAMENT_ADAPTER
      : {};
  const VERSION = "papp-tournament-adapter-v14";
  const MAX_SCORE = 64;
  const STATE_API_URL = "/api/state";
  const CANDIDATE_SYNC_WAIT_MS = 10000;
  const CANDIDATE_SYNC_POLL_MS = 100;
  let candidatePlayerPool = [];
  let pappPlayerRecords = [];

  function objectOf(value) {
    return value && typeof value === "object" ? value : {};
  }

  function textOf(value) {
    return String(value == null ? "" : value).replace(/\s+/g, " ").trim();
  }

  function keyOf(value) {
    const text = textOf(value);
    if (!text) return "";
    try {
      return text.normalize("NFKC").toLowerCase();
    } catch (_) {
      return text.toLowerCase();
    }
  }

  function copy(value) {
    if (value === undefined) return undefined;
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (_) {
      return value;
    }
  }

  function numberOrNull(value) {
    if (value === "" || value === null || value === undefined) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function scoreOrNull(value) {
    const parsed = numberOrNull(value);
    return parsed === null || parsed < 0 || parsed > MAX_SCORE ? null : Math.trunc(parsed);
  }

  function roundOf(context) {
    const parsed = Number(context && context.round);
    return Number.isFinite(parsed) && parsed >= 1 ? Math.trunc(parsed) : 1;
  }

  function tournamentParametersOf(context) {
    const value = objectOf(context);
    const stateParameters = objectOf(objectOf(value.state).tournamentParameters);
    const contextParameters = objectOf(value.tournamentParameters);
    const source = { ...stateParameters, ...contextParameters };
    const rawConstant = source.brightwellConstant;
    const hasNumericType = typeof rawConstant === "number" ||
      (typeof rawConstant === "string" && rawConstant.trim() !== "");
    const constant = hasNumericType ? Number(rawConstant) : NaN;
    return {
      ...source,
      hasSemifinalAndFinal: typeof source.hasSemifinalAndFinal === "boolean"
        ? source.hasSemifinalAndFinal
        : true,
      brightwellConstant: Number.isFinite(constant) && constant >= 0 ? constant : 6,
    };
  }

  function preliminaryRoundCountOf(context) {
    const value = objectOf(context);
    const helper = objectOf(objectOf(value.state).scoreHelper);
    for (const candidate of [
      value.preliminaryRoundCount,
      value.roundCount,
      helper.preliminaryRoundCount,
      helper.roundCount,
      Array.isArray(helper.rounds) ? helper.rounds.length : 0,
    ]) {
      const count = Number(candidate);
      if (Number.isFinite(count) && count >= 1) return Math.trunc(count);
    }
    return Math.max(1, roundOf(context));
  }

  function playerId(player) {
    const value = objectOf(player);
    return textOf(value.id || value.playerId || value.uid || value.number);
  }

  function playerName(player) {
    const value = objectOf(player);
    return textOf(value.pappName || value.nickname || value.name || value.displayName || value.playerName || value.nick || playerId(value));
  }

  function playerAccount(player) {
    const value = objectOf(player);
    return textOf(value.account || value.oqAccount || value.username || value.userName);
  }

  function mappedAccountForIdentity(name, id, context) {
    const mapping = objectOf(objectOf(context && context.state).mapping);
    const rows = Array.isArray(mapping.rows) ? mapping.rows : [];
    const identity = textOf(id);
    let matches = identity
      ? rows.filter(function (row) { return textOf(objectOf(row).checkinPlayerId) === identity; })
      : [];
    if (!matches.length) {
      const nameKey = keyOf(name);
      if (nameKey) {
        matches = rows.filter(function (row) {
          const value = objectOf(row);
          return keyOf(value.registrationNick || value.displayName || value.playerName) === nameKey;
        });
      }
    }
    if (!matches.length) return { found: false, registrationNick: "", account: "" };
    if (matches.length !== 1) return { found: true, registrationNick: "", account: "" };
    const row = objectOf(matches[0]);
    const registrationNick = textOf(row.registrationNick || row.displayName || row.playerName);
    const account = textOf(row.oqAccount || row.account);
    return {
      found: true,
      registrationNick: registrationNick && account ? registrationNick : "",
      account: registrationNick && account ? account : "",
    };
  }

  function accountForPlayer(player, context) {
    const resolved = objectOf(player);
    const mapped = mappedAccountForIdentity(playerName(resolved), playerId(resolved), context);
    if (mapped.found) return mapped.account;
    const direct = playerAccount(player);
    return direct;
  }

  function candidatePlayersFromContext(context) {
    const value = objectOf(context);
    if (Array.isArray(value.candidatePlayers)) return value.candidatePlayers;
    const state = objectOf(value.state);
    return Array.isArray(state.players) ? state.players : null;
  }

  function candidateIdentity(player) {
    const value = objectOf(player);
    const account = keyOf(value.account);
    if (account) return "account:" + keyOf(value.platform) + ":" + account;
    const name = keyOf(value.displayName || value.name);
    return name ? "name:" + name : "";
  }

  function checkedInIdentityCounts(players) {
    const counts = new Map();
    for (const player of players) {
      if (!player || typeof player !== "object" || Array.isArray(player)) {
        throw new Error("PAPP 候选名单包含无效选手项");
      }
      const identity = candidateIdentity(player);
      if (!identity) throw new Error("选手缺少姓名或账号，无法核对 PAPP 候选名单");
      counts.set(identity, (counts.get(identity) || 0) + 1);
    }
    return counts;
  }

  function sameValue(left, right) {
    if (left === right) return true;
    if (Array.isArray(left) || Array.isArray(right)) {
      return Array.isArray(left) && Array.isArray(right) && left.length === right.length &&
        left.every(function (value, index) { return sameValue(value, right[index]); });
    }
    if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length && leftKeys.every(function (key, index) {
      return key === rightKeys[index] && sameValue(left[key], right[key]);
    });
  }

  function sameCounts(left, right) {
    if (left.size !== right.size) return false;
    for (const entry of left) {
      if (right.get(entry[0]) !== entry[1]) return false;
    }
    return true;
  }

  function parseStateResponse(response, action) {
    return response.text().then(function (responseText) {
      let payload;
      try {
        payload = responseText ? JSON.parse(responseText) : {};
      } catch (_) {
        throw new Error("PAPP 本地服务返回了无效 JSON");
      }
      if (!response.ok || payload.ok === false) {
        throw new Error(textOf(payload.message || payload.error || payload.detail) ||
          ("PAPP 本地服务" + action + "失败（HTTP " + response.status + "）"));
      }
      return payload;
    });
  }

  async function readPersistedState() {
    const fetcher = window.fetch || (typeof fetch === "function" ? fetch : null);
    if (typeof fetcher !== "function") {
      throw new Error("当前页面无法连接 PAPP 本地服务");
    }

    let response;
    try {
      response = await fetcher(STATE_API_URL, { method: "GET", cache: "no-store" });
    } catch (error) {
      throw new Error("PAPP 本地服务连接失败：" + (textOf(error && error.message) || "网络错误"));
    }
    const payload = await parseStateResponse(response, "读取共享状态");
    return objectOf(payload.state);
  }

  function normalizePersistedCandidatePlayers(state) {
    if (!Array.isArray(state.players)) {
      throw new Error("PAPP 本地服务未返回已持久化的候选名单 state.players");
    }
    const players = state.players.map(function (player, index) {
      if (!player || typeof player !== "object" || Array.isArray(player)) {
        throw new Error("PAPP 已持久化候选名单第 " + (index + 1) + " 项无效");
      }
      return { ...copy(player), checkedIn: objectOf(player).checkedIn === true };
    });
    checkedInIdentityCounts(players);
    return players;
  }

  async function readPersistedCandidatePlayers() {
    const state = await readPersistedState();
    const players = normalizePersistedCandidatePlayers(state);
    pappPlayerRecords = Array.isArray(state.pappPlayers) ? copy(state.pappPlayers) : [];
    candidatePlayerPool = pappFieldsForPlayers(players, {});
    return players;
  }

  function normalizeCandidatePlayers(context) {
    const suppliedPlayers = candidatePlayersFromContext(context);
    if (!Array.isArray(suppliedPlayers)) {
      throw new Error("PAPP 候选选手同步需要 context.candidatePlayers 数组");
    }
    const candidates = suppliedPlayers.map(function (player, index) {
      if (!player || typeof player !== "object" || Array.isArray(player)) {
        throw new Error("PAPP 候选名单第 " + (index + 1) + " 项无效");
      }
      return { ...copy(player), checkedIn: objectOf(player).checkedIn === true };
    });
    checkedInIdentityCounts(candidates);

    const value = objectOf(context);
    if (Array.isArray(value.checkedInPlayers)) {
      const expectedCounts = checkedInIdentityCounts(candidates.filter(function (player) {
        return player.checkedIn;
      }));
      const suppliedCheckedIn = value.checkedInPlayers;
      if (suppliedCheckedIn.some(function (player) {
        return !player || objectOf(player).checkedIn !== true;
      })) {
        throw new Error("context.checkedInPlayers 只能包含 checkedIn 为 true 的选手");
      }
      const actualCounts = checkedInIdentityCounts(suppliedCheckedIn);
      if (!sameCounts(expectedCounts, actualCounts)) {
        throw new Error("context.checkedInPlayers 与 candidatePlayers 的签到状态不一致");
      }
    }
    return candidates;
  }

  function normalizeMappingPlayers(context, candidates) {
    const value = objectOf(context);
    if (!Object.prototype.hasOwnProperty.call(value, "mappingPlayers")) return [];
    if (!Array.isArray(value.mappingPlayers)) {
      throw new Error("context.mappingPlayers 必须是数组");
    }

    const candidateIdCounts = new Map();
    candidates.forEach(function (player) {
      const id = playerId(player);
      if (id) candidateIdCounts.set(id, (candidateIdCounts.get(id) || 0) + 1);
    });
    const mappingRowIds = new Set();
    const candidateIds = new Set();
    return value.mappingPlayers.map(function (mapping, index) {
      if (!mapping || typeof mapping !== "object" || Array.isArray(mapping)) {
        throw new Error("context.mappingPlayers 第 " + (index + 1) + " 项无效");
      }
      const mappingRowId = textOf(mapping.mappingRowId);
      const candidatePlayerId = textOf(mapping.candidatePlayerId);
      if (!mappingRowId || !candidatePlayerId) {
        throw new Error("映射项缺少 mappingRowId 或 candidatePlayerId");
      }
      if (typeof mapping.name !== "string" || typeof mapping.country !== "string") {
        throw new Error("映射项的 name 和 country 必须是字符串");
      }
      if (mappingRowIds.has(mappingRowId)) {
        throw new Error("映射表包含重复的 mappingRowId");
      }
      if (candidateIds.has(candidatePlayerId)) {
        throw new Error("多个映射行指向同一签到选手");
      }
      if (candidateIdCounts.get(candidatePlayerId) !== 1) {
        throw new Error("映射项未能唯一关联到一个签到候选人");
      }
      mappingRowIds.add(mappingRowId);
      candidateIds.add(candidatePlayerId);
      return {
        mappingRowId: mappingRowId,
        candidatePlayerId: candidatePlayerId,
        name: mapping.name,
        country: mapping.country,
      };
    });
  }

  function pappFieldsForPlayers(players, context) {
    const recordsById = new Map();
    pappPlayerRecords.forEach(function (record) {
      const id = playerId(record);
      if (id && !recordsById.has(id)) recordsById.set(id, record);
    });

    const mappingListsById = new Map();
    const value = objectOf(context);
    const mappings = Array.isArray(value.mappingPlayers) ? value.mappingPlayers : [];
    mappings.forEach(function (mapping) {
      const id = textOf(objectOf(mapping).candidatePlayerId);
      if (!id) return;
      const entries = mappingListsById.get(id) || [];
      entries.push(mapping);
      mappingListsById.set(id, entries);
    });

    return players.map(function (player) {
      const result = { ...copy(player) };
      const id = playerId(player);
      const stored = recordsById.get(id);
      if (stored) {
        if (Object.prototype.hasOwnProperty.call(stored, "name")) {
          result.pappName = copy(stored.name);
        }
        if (Object.prototype.hasOwnProperty.call(stored, "country")) {
          result.pappCountry = copy(stored.country);
        }
      }
      const currentMappings = mappingListsById.get(id) || [];
      if (currentMappings.length === 1) {
        const mapping = currentMappings[0];
        if (typeof mapping.name === "string" && typeof mapping.country === "string") {
          result.pappName = mapping.name;
          result.pappCountry = mapping.country;
        }
      }
      return result;
    });
  }

  function delay(milliseconds) {
    return new Promise(function (resolve) {
      const timer = window.setTimeout || (typeof setTimeout === "function" ? setTimeout : null);
      if (timer) timer(resolve, milliseconds);
      else resolve();
    });
  }

  async function getCandidates() {
    try {
      const candidatePlayers = await readPersistedCandidatePlayers();
      return { ok: true, candidatePlayers: candidatePlayers };
    } catch (error) {
      return {
        ok: false,
        code: "candidate-read-failed",
        message: textOf(error && error.message) || "读取 PAPP 已持久化候选名单失败",
      };
    }
  }

  function legacyPlayers(players) {
    return players.filter(function (player) {
      const value = objectOf(player);
      const status = keyOf(value.status || value.state);
      if (["withdrawn", "withdraw", "scratched", "removed", "cancelled", "canceled", "退赛"].includes(status)) {
        return false;
      }
      return value.active !== false && value.disabled !== true;
    });
  }

  function playersOf(context) {
    const value = objectOf(context);
    if (Array.isArray(value.checkedInPlayers)) {
      return pappFieldsForPlayers(value.checkedInPlayers.filter(function (player) {
        return objectOf(player).checkedIn === true;
      }), value);
    }

    const candidates = candidatePlayersFromContext(value);
    if (candidates) {
      if (Array.isArray(value.candidatePlayers) || value.rosterSource === "checkin" ||
          candidates.some(function (player) {
            return Object.prototype.hasOwnProperty.call(objectOf(player), "checkedIn");
          })) {
        return pappFieldsForPlayers(candidates.filter(function (player) {
          return objectOf(player).checkedIn === true;
        }), value);
      }
      return pappFieldsForPlayers(legacyPlayers(candidates), value);
    }

    if (value.rosterSource === "checkin") {
      return pappFieldsForPlayers(candidatePlayerPool.filter(function (player) {
        return objectOf(player).checkedIn === true;
      }), value);
    }
    return pappFieldsForPlayers(legacyPlayers(candidatePlayerPool), value);
  }

  function hasCheckinRoster(context) {
    const value = objectOf(context);
    if (Array.isArray(value.checkedInPlayers) || Array.isArray(value.candidatePlayers) ||
        value.rosterSource === "checkin") return true;
    const candidates = candidatePlayersFromContext(value);
    return Boolean(candidates && candidates.some(function (player) {
      return Object.prototype.hasOwnProperty.call(objectOf(player), "checkedIn");
    }));
  }

  function resolvePlayer(reference, players, context) {
    if (reference === null || reference === undefined || reference === "") return null;
    const value = objectOf(reference);
    const candidates = [playerId(value), playerName(value), playerAccount(value), textOf(reference)]
      .map(keyOf)
      .filter(Boolean);
    if (!candidates.length) return null;
    return players.find(function (player) {
      const keys = [playerId(player), playerName(player), playerAccount(player)]
        .map(keyOf)
        .filter(Boolean);
      const mappedAccount = keyOf(accountForPlayer(player, context));
      if (mappedAccount) keys.push(mappedAccount);
      return candidates.some(function (candidate) { return keys.includes(candidate); });
    }) || null;
  }

  function sideReference(raw, side) {
    const value = objectOf(raw);
    return value[side] ?? value[side + "Name"] ?? value[side + "Player"] ??
      value[side + "Id"] ?? value[side + "PlayerId"] ?? "";
  }

  function sideName(raw, side, resolved) {
    const reference = sideReference(raw, side);
    if (resolved) return playerName(resolved);
    if (reference && typeof reference === "object") return playerName(reference);
    return textOf(reference);
  }

  function sideId(raw, side, resolved) {
    const metadata = objectOf(objectOf(raw).metadata);
    const papp = objectOf(metadata.papp);
    return playerId(resolved) || textOf(papp[side + "PlayerId"]);
  }

  function sideAccount(raw, side, resolved, context) {
    const value = objectOf(raw);
    const metadataPapp = objectOf(objectOf(value.metadata).papp);
    const identity = textOf(value[side + "Id"] || value[side + "PlayerId"] || metadataPapp[side + "PlayerId"] || playerId(resolved));
    const name = sideName(raw, side, resolved);
    const mapped = mappedAccountForIdentity(name, identity, context);
    if (mapped.found) return mapped.account;
    return textOf(value[side + "Account"] || value[side + "OqAccount"] ||
      value[side + "Username"] || accountForPlayer(resolved, context));
  }

  function mapPairingAccounts(pairing, context) {
    const value = copy(objectOf(pairing)) || {};
    const metadata = objectOf(value.metadata);
    const metadataPapp = objectOf(metadata.papp);
    ["black", "white"].forEach(function (side) {
      const id = textOf(value[side + "Id"] || value[side + "PlayerId"] || metadataPapp[side + "PlayerId"]);
      const name = textOf(value[side] || value[side + "Name"]);
      const mapped = mappedAccountForIdentity(name, id, context);
      if (!mapped.found) return;
      if (mapped.registrationNick) {
        value[side] = mapped.registrationNick;
        value[side + "Name"] = mapped.registrationNick;
      }
      value[side + "Account"] = mapped.account;
      value[side + "OqAccount"] = mapped.account;
      delete value[side + "Username"];
    });
    return value;
  }

  function oqRoundsForPolling(context, currentPairings) {
    const value = objectOf(context);
    const state = objectOf(value.state);
    const helper = objectOf(state.scoreHelper);
    const rounds = Array.isArray(helper.rounds) ? helper.rounds : [];
    const currentRound = roundOf(value);
    const stage = keyOf(value.stage || objectOf(value.roundData).stage || "preliminary");
    const preliminaryCount = preliminaryRoundCountOf(value);
    const maxPreliminary = stage === "preliminary" ? currentRound : preliminaryCount;
    const playoff = objectOf(state.playoffRegistration);
    const skipSemifinal = objectOf(state.tournamentParameters).skipSemifinal === true;
    const output = [];

    rounds.slice(0, maxPreliminary).forEach(function (roundData, index) {
      const roundNumber = index + 1;
      const row = objectOf(roundData);
      const pairings = roundNumber === currentRound && stage === "preliminary"
        ? currentPairings
        : Array.isArray(row.pairings) ? row.pairings : [];
      const mappedPairings = pairings.map(function (pairing) { return mapPairingAccounts(pairing, context); });
      const nextPreliminary = objectOf(rounds[roundNumber]);
      const nextRoundStartAt = textOf(nextPreliminary.roundStartAt) ||
        (roundNumber === preliminaryCount
          ? textOf(skipSemifinal ? playoff.placementRoundStartAt : playoff.semifinalRoundStartAt)
          : "");
      const roundDataWithWindow = { ...copy(row), pairings: copy(mappedPairings) };
      if (!roundDataWithWindow.roundEndAt && roundNumber < currentRound && nextRoundStartAt) {
        roundDataWithWindow.roundEndAt = nextRoundStartAt;
      }
      output.push({
        round: roundNumber,
        stage: "preliminary",
        roundData: roundDataWithWindow,
        pairings: mappedPairings,
      });
    });

    if (stage !== "preliminary") {
      const includeSemifinal = !skipSemifinal && (stage === "semifinal" || stage === "placement");
      const includePlacement = stage === "placement";
      if (includeSemifinal && Array.isArray(playoff.semifinalPairings)) {
        const roundNumber = preliminaryCount + 1;
        const pairings = roundNumber === currentRound ? currentPairings : playoff.semifinalPairings;
        const mappedPairings = pairings.map(function (pairing) { return mapPairingAccounts(pairing, context); });
        const roundEndAt = textOf(playoff.semifinalRoundEndAt) ||
          (stage === "placement" ? textOf(playoff.placementRoundStartAt) : "");
        output.push({
          round: roundNumber,
          stage: "semifinal",
          roundData: {
            roundStartAt: playoff.semifinalRoundStartAt,
            roundEndAt: roundEndAt,
            windowMinutes: playoff.semifinalWindowMinutes,
            pairings: copy(mappedPairings),
          },
          pairings: mappedPairings,
        });
      }
      if (includePlacement && Array.isArray(playoff.placementPairings)) {
        const roundNumber = preliminaryCount + (skipSemifinal ? 1 : 2);
        const pairings = roundNumber === currentRound ? currentPairings : playoff.placementPairings;
        const mappedPairings = pairings.map(function (pairing) { return mapPairingAccounts(pairing, context); });
        output.push({
          round: roundNumber,
          stage: "placement",
          roundData: {
            roundStartAt: playoff.placementRoundStartAt,
            roundEndAt: playoff.placementRoundEndAt,
            windowMinutes: playoff.placementWindowMinutes,
            pairings: copy(mappedPairings),
          },
          pairings: mappedPairings,
        });
      }
    }

    if (!output.some(function (row) { return row.round === currentRound && row.stage === stage; })) {
      const roundData = objectOf(value.roundData);
      const pairings = currentPairings;
      const mappedPairings = pairings.map(function (pairing) { return mapPairingAccounts(pairing, context); });
      output.push({
        round: currentRound,
        stage: stage || "preliminary",
        roundData: { ...copy(roundData), pairings: copy(mappedPairings) },
        pairings: mappedPairings,
      });
    }
    return output;
  }

  function pairingSource(context) {
    const value = objectOf(context);
    const candidates = [
      value.pairingSource,
      value.importedPairings,
      value.pappPairings,
      value.pairings,
      window.PAPP_TOURNAMENT_PAIRINGS,
    ];
    return candidates.find(function (candidate) {
      return candidate !== undefined && candidate !== null;
    }) ?? null;
  }

  function hasPairingSource(context) {
    const value = objectOf(context);
    return ["pairingSource", "importedPairings", "pappPairings", "pairings"].some(function (key) {
      return value[key] !== undefined && value[key] !== null;
    });
  }

  function parsePairingText(source) {
    const result = [];
    let table = 1;
    const text = String(source == null ? "" : source).replace(/^\uFEFF/, "");
    text.split(/\r?\n/).forEach(function (line) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith("//")) return;

      const papp = trimmed.match(/^\(?\s*([^\s,;()]+)\s+([^\s,;()]+)\s*\)?\s*;?$/);
      if (papp && !/^table$/i.test(papp[1]) && !/^black$/i.test(papp[1])) {
        result.push({ table: table, black: papp[1], white: papp[2], metadata: { sourceFormat: "papp-text" } });
        table += 1;
        return;
      }

      const fields = trimmed.replace(/[()]/g, "").split(/[,，\t|;]/)
        .map(textOf).filter(Boolean);
      if (fields.length >= 3 && !/^(table|台|桌)$/i.test(fields[0])) {
        result.push({
          table: fields[0],
          black: fields[1],
          white: fields[2],
          blackAccount: fields[3] || "",
          whiteAccount: fields[4] || "",
          oqGameId: fields[5] || "",
          metadata: { sourceFormat: "delimited-text" },
        });
        table += 1;
        return;
      }

      const tokens = trimmed.replace(/[(),;]/g, " ").split(/\s+/).filter(Boolean);
      if (tokens.length >= 3 && !/^(table|台|桌)$/i.test(tokens[0])) {
        result.push({
          table: tokens[0],
          black: tokens[1],
          white: tokens.slice(2).join(" "),
          metadata: { sourceFormat: "space-text" },
        });
        table += 1;
      } else if (tokens.length === 2) {
        result.push({
          table: table,
          black: tokens[0],
          white: tokens[1],
          metadata: { sourceFormat: "two-column-text" },
        });
        table += 1;
      }
    });
    return result;
  }

  function unwrapSource(source, round) {
    if (Array.isArray(source)) return source;
    if (typeof source === "string") {
      const trimmed = source.trim();
      if (!trimmed) return [];
      if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
        try {
          return unwrapSource(JSON.parse(trimmed), round);
        } catch (_) {
          return parsePairingText(source);
        }
      }
      return parsePairingText(source);
    }
    const value = objectOf(source);
    if (Array.isArray(value.pairings)) return value.pairings;
    if (Array.isArray(value.matches)) return value.matches;
    if (Array.isArray(value.games)) return value.games;
    if (value.rounds && typeof value.rounds === "object") {
      const selected = Array.isArray(value.rounds)
        ? value.rounds[round - 1]
        : value.rounds[String(round)] ?? value.rounds[round];
      if (selected !== undefined) return unwrapSource(selected, round);
    }
    if (value.data !== undefined) return unwrapSource(value.data, round);
    return [];
  }

  function normalizePairing(raw, index, context, source) {
    const value = objectOf(raw);
    const players = playersOf(context);
    const blackPlayer = resolvePlayer(sideReference(value, "black"), players, context);
    const whitePlayer = resolvePlayer(sideReference(value, "white"), players, context);
    const black = sideName(value, "black", blackPlayer);
    const white = sideName(value, "white", whitePlayer);
    const statusValue = keyOf(value.status);
    const blackScore = scoreOrNull(value.blackScore);
    const whiteScore = scoreOrNull(value.whiteScore);
    const bye = statusValue === "bye" || value.bye === true || !white;
    if (!black || (!white && !bye)) {
      throw new Error("第 " + index + " 项配对缺少黑方或白方");
    }
    if (hasCheckinRoster(context) && (!blackPlayer || (!bye && !whitePlayer))) {
      throw new Error("第 " + index + " 项配对包含不在当前已签到参赛集合中的选手");
    }

    const rawTable = textOf(value.table || value.pendingTable || index) || String(index);
    const table = /^bye$/i.test(rawTable) ? String(index) : rawTable;
    const allowed = ["imported", "pending", "ready", "completed", "bye"];
    const status = allowed.includes(statusValue)
      ? statusValue
      : bye
        ? "bye"
        : blackScore !== null && whiteScore !== null
          ? "ready"
          : "imported";
    const metadata = value.metadata && typeof value.metadata === "object" ? copy(value.metadata) : {};
    metadata.papp = {
      ...objectOf(metadata.papp),
      source: source || "papp-file",
      round: roundOf(context),
      table: table,
      blackPlayerId: sideId(value, "black", blackPlayer),
      whitePlayerId: sideId(value, "white", whitePlayer),
    };

    return {
      id: textOf(value.id || value.pairingId) || "papp-r" + roundOf(context) + "-t" + table,
      table: table,
      black: black,
      white: bye ? "" : white,
      blackAccount: sideAccount(value, "black", blackPlayer, context),
      whiteAccount: bye ? "" : sideAccount(value, "white", whitePlayer, context),
      oqGameId: textOf(value.oqGameId || value.gameId),
      status: status,
      blackScore: blackScore,
      whiteScore: whiteScore,
      metadata: metadata,
    };
  }

  function normalizeSource(source, context, label) {
    const items = unwrapSource(source, roundOf(context));
    if (!Array.isArray(items)) throw new Error("PAPP 配对表格式无效：未找到 pairings 数组");
    return items.map(function (item, index) {
      return normalizePairing(item, index + 1, context, label);
    });
  }

  async function getPreliminaryStandings(context) {
    return invokePappTournament(context, "preliminary-standings");
  }

  async function getRoundStandings(context) {
    const value = objectOf(context);
    const round = value.round === undefined || value.round === null
      ? roundOf(value)
      : Number(value.round);
    return invokePappTournament({ ...value, round: round }, "round-standings", {
      round: round,
    });
  }

  async function getStageStatus(context) {
    const value = objectOf(context);
    return invokePappTournament(value, "stage-status", {
      stage: keyOf(value.stage) || "preliminary",
      round: roundOf(value),
    });
  }

  async function getOverallStandings(context) {
    return invokePappTournament(context, "overall-standings");
  }

  async function syncCandidates(context) {
    let candidates;
    let mappings;
    try {
      candidates = normalizeCandidatePlayers(context);
      mappings = normalizeMappingPlayers(context, candidates);
    } catch (error) {
      return {
        ok: false,
        code: "candidate-players-invalid",
        message: textOf(error && error.message) || "PAPP 候选选手数据无效",
      };
    }

    const fetcher = window.fetch || (typeof fetch === "function" ? fetch : null);
    if (typeof fetcher !== "function") {
      return {
        ok: false,
        code: "fetch-unavailable",
        message: "当前页面无法连接 PAPP 本地服务",
      };
    }

    let response;
    try {
      response = await fetcher(STATE_API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({
          operation: "sync-candidates",
          candidatePlayers: candidates,
          mappingPlayers: mappings,
          source: "script",
        }),
      });
    } catch (error) {
      return {
        ok: false,
        code: "candidate-sync-failed",
        message: "PAPP 本地服务连接失败：" + (textOf(error && error.message) || "网络错误"),
      };
    }

    let writeResult;
    try {
      writeResult = await parseStateResponse(response, "同步候选名单");
    } catch (error) {
      return {
        ok: false,
        code: "candidate-sync-failed",
        message: textOf(error && error.message) || "PAPP 候选名单写入失败",
      };
    }

    const mappingSyncRequested = Object.prototype.hasOwnProperty.call(
      objectOf(context),
      "mappingPlayers",
    );
    const matchesPersistedSync = async function () {
      const state = await readPersistedState();
      const persistedPlayers = normalizePersistedCandidatePlayers(state);
      if (!sameValue(persistedPlayers, candidates)) return false;

      pappPlayerRecords = Array.isArray(state.pappPlayers) ? copy(state.pappPlayers) : [];
      if (mappingSyncRequested) {
        const recordsById = new Map();
        pappPlayerRecords.forEach(function (record) {
          const id = playerId(record);
          if (id && !recordsById.has(id)) recordsById.set(id, record);
        });
        const candidateIds = candidates.map(playerId).filter(Boolean);
        if (candidateIds.some(function (id) { return !recordsById.has(id); })) return false;
        if (mappings.some(function (mapping) {
          const record = recordsById.get(mapping.candidatePlayerId);
          return !record || record.name !== mapping.name || record.country !== mapping.country;
        })) return false;
      }

      candidatePlayerPool = pappFieldsForPlayers(persistedPlayers, { mappingPlayers: mappings });
      return true;
    };
    const deadline = Date.now() + CANDIDATE_SYNC_WAIT_MS;
    let lastReadError = "";
    do {
      try {
        if (await matchesPersistedSync()) {
          return {
            ok: true,
            candidateCount: candidates.length,
            checkedInCount: candidates.filter(function (player) {
              return player.checkedIn;
            }).length,
          };
        }
      } catch (error) {
        lastReadError = textOf(error && error.message) || "读取 PAPP 已持久化候选名单失败";
      }
      if (!writeResult.queued) break;
      await delay(CANDIDATE_SYNC_POLL_MS);
    } while (Date.now() < deadline);

    return {
      ok: false,
      code: "candidate-sync-not-persisted",
      message: "PAPP 本地服务未能在等待期限内持久化候选名单" +
        (lastReadError ? "：" + lastReadError : ""),
    };
  }

  function existingPairings(context) {
    const roundData = objectOf(context && context.roundData);
    if (Array.isArray(roundData.pairings) && roundData.pairings.length) {
      return copy(roundData.pairings);
    }
    return Array.isArray(context && context.pairings) && context.pairings.length
      ? copy(context.pairings)
      : null;
  }

  function isLegacyPairing(pairing) {
    const value = objectOf(pairing);
    const metadata = objectOf(value.metadata);
    const source = keyOf(value.source || metadata.source || objectOf(metadata.papp).source);
    return source === "papp-adapter" || source === "papp-local" ||
      source === "papp-local-playoff";
  }

  function legacyReadOnlyResult(pairings) {
    return {
      ok: true,
      pairings: copy(pairings),
      source: "legacy-history",
      readOnly: true,
    };
  }

  function mapCResultPairings(result, context, sourceOverride) {
    if (!result || result.ok === false) return result;
    const players = cPlayerPool(context);
    return {
      ...result,
      pairings: (Array.isArray(result.pairings) ? result.pairings : []).map(function (pairing) {
        return mapCPairingToUi(pairing, players, context, sourceOverride);
      }),
    };
  }

  async function importPairings(context) {
    try {
      if (objectOf(context).mode === "start-score-registration" &&
          roundOf(context) === 1 && !cPlayerPool(context).some(function (player) {
            return objectOf(player).checkedIn === true;
          })) {
        return {
          ok: false,
          code: "checked-in-players-missing",
          message: "没有已签到选手，未生成第一轮配对",
        };
      }
      const current = existingPairings(context);
      if (current && current.some(isLegacyPairing)) return legacyReadOnlyResult(current);

      if (hasPairingSource(context)) {
        const pairings = normalizeSource(pairingSource(context), context, "papp-file");
        if (!pairings.length) return { ok: false, code: "pairings-empty", message: "导入的 PAPP 配对表为空" };
        const pool = cPlayerPool(context);
        const result = await invokePappTournament(context, "validate-pairings", {
          pairings: pairings.map(function (pairing) {
            return cPairingInput(pairing, pool, context);
          }),
        });
        return mapCResultPairings(result, context, "papp-file");
      }
      if (current) {
        if (current.every(function (pairing) { return keyOf(objectOf(pairing).source) === "papp-c"; })) {
          return { ok: true, pairings: current, source: "papp-c", validationSource: "papp-c" };
        }
        const result = await invokePappTournament(context, "validate-pairings", {
          pairings: current.map(function (pairing) {
            return cPairingInput(pairing, cPlayerPool(context), context);
          }),
        });
        return mapCResultPairings(result, context, "papp-file");
      }
      const stage = requestedCStage(context);
      const result = await invokePappTournament(context, "pairings", { stage: stage });
      return mapCResultPairings(result, context, "papp-c");
    } catch (error) {
      return {
        ok: false,
        code: "pairings-import-invalid",
        message: textOf(error && error.message) || "PAPP 配对表格式无效",
      };
    }
  }

  async function refreshRound(context) {
    return importPairings(context);
  }

  async function registerScore(context) {
    const pairing = objectOf(context && context.pairing);
    return requestPappC({
      operation: "validate-score",
      blackScore: pairing.blackScore,
      whiteScore: pairing.whiteScore,
    });
  }

  function scoreBatchForPapp(context, requirePairingIds) {
    const value = objectOf(context);
    const stage = textOf(value.stage);
    if (!["preliminary", "semifinal", "placement"].includes(stage)) {
      throw new Error("比分批次的比赛阶段无效");
    }
    const round = Number(value.round);
    if (!Number.isInteger(round) || round < 1) {
      throw new Error("比分批次轮次必须是正整数");
    }
    const batchId = textOf(value.batchId);
    if (!batchId) throw new Error("比分批次缺少 batchId");
    const workfileId = textOf(value.pappWorkfileId ||
      objectOf(objectOf(value.state).scoreHelper).pappWorkfileId);
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(workfileId)) {
      throw new Error("比分批次缺少有效的比赛 workfile ID，请刷新页面后重试");
    }
    if (!Array.isArray(value.pairings) || value.pairings.length === 0) {
      throw new Error("比分批次必须包含至少一场配对");
    }

    const pool = cPlayerPool(value);
    const seenIds = new Set();
    const seenTables = new Set();
    const pairings = value.pairings.map(function (raw, index) {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        throw new Error("比分批次第 " + (index + 1) + " 项无效");
      }
      const item = objectOf(raw);
      const id = textOf(item.id || item.pairingId);
      if (!id) throw new Error("比分批次中的每场配对都必须有稳定 id");
      if (seenIds.has(id)) throw new Error("比分批次包含重复配对 id：" + id);
      seenIds.add(id);

      const table = Number(item.table);
      if (!Number.isInteger(table) || table < 1) {
        throw new Error("配对 " + id + " 的桌号无效");
      }
      if (seenTables.has(table)) throw new Error("比分批次包含重复桌号：" + table);
      seenTables.add(table);
      const blackScore = typeof item.blackScore === "number"
        ? item.blackScore
        : typeof item.blackScore === "string" && /^\d+$/.test(item.blackScore.trim())
          ? Number(item.blackScore.trim()) : NaN;
      const whiteScore = typeof item.whiteScore === "number"
        ? item.whiteScore
        : typeof item.whiteScore === "string" && /^\d+$/.test(item.whiteScore.trim())
          ? Number(item.whiteScore.trim()) : NaN;
      if (!Number.isInteger(blackScore) || blackScore < 0 || blackScore > MAX_SCORE ||
          !Number.isInteger(whiteScore) || whiteScore < 0 || whiteScore > MAX_SCORE ||
          blackScore + whiteScore !== MAX_SCORE) {
        throw new Error("配对 " + id + " 的比分必须是和为 64 的整数");
      }
      const black = textOf(item.black || item.blackName);
      const white = textOf(item.white || item.whiteName);
      if (!black || !white) throw new Error("配对 " + id + " 缺少黑白双方身份");

      const cPairing = cPairingInput(item, pool, value);
      if (!cPairing.blackId || !cPairing.whiteId || cPairing.blackId === cPairing.whiteId) {
        throw new Error("配对 " + id + " 无法唯一映射到 PAPP 黑白双方");
      }
      const blackPlayer = findCPlayer(pool, cPairing.blackId);
      const whitePlayer = findCPlayer(pool, cPairing.whiteId);
      return {
        id: id,
        table: table,
        blackId: cPairing.blackId,
        whiteId: cPairing.whiteId,
        black: black,
        white: white,
        blackAccount: sideAccount(item, "black", blackPlayer, value),
        whiteAccount: sideAccount(item, "white", whitePlayer, value),
        oqGameId: textOf(item.oqGameId),
        blackScore: blackScore,
        whiteScore: whiteScore,
        status: textOf(item.status || "ready"),
      };
    });

    if (requirePairingIds) {
      if (!Array.isArray(value.pairingIds)) {
        throw new Error("比分读回需要 pairingIds 数组");
      }
      const pairingIds = value.pairingIds.map(textOf);
      if (pairingIds.some(function (id) { return !id; }) ||
          new Set(pairingIds).size !== pairingIds.length ||
          pairingIds.length !== seenIds.size ||
          pairingIds.some(function (id) { return !seenIds.has(id); })) {
        throw new Error("pairingIds 与请求配对 id 不一致或包含重复项");
      }
    }

    return { stage: stage, round: round, batchId: batchId, pairings: pairings };
  }

  function tournamentScoreBatchOverrides(context, batch) {
    const value = objectOf(context);
    const state = objectOf(value.state);
    return {
      stage: batch.stage,
      round: batch.round,
      batchId: batch.batchId,
      tournamentName: textOf(value.tournamentName || state.competitionName),
      pairings: batch.pairings,
      pairingIds: Array.isArray(value.pairingIds) ? value.pairingIds.map(textOf) : undefined,
    };
  }

  function scoreBatchFailure(error, fallback) {
    return {
      ok: false,
      message: textOf(error && error.message) || fallback,
    };
  }

  async function writeScoreBatch(context) {
    try {
      const batch = scoreBatchForPapp(context, false);
      const result = await invokePappTournament(
        { ...objectOf(context), stage: batch.stage, round: batch.round },
        "write-score-batch",
        tournamentScoreBatchOverrides(context, batch),
      );
      if (!result || result.ok !== true || result.source !== "papp-c" ||
          result.accepted !== true || result.batchId !== batch.batchId) {
        return scoreBatchFailure(result, "PAPP 未确认已写入比分批次");
      }
      return {
        ok: true,
        batchId: batch.batchId,
        accepted: true,
        idempotent: result.idempotent === true,
        source: result.source || "papp-c",
      };
    } catch (error) {
      return scoreBatchFailure(error, "PAPP 批量比分写入失败");
    }
  }

  async function readScoreBatch(context) {
    try {
      const value = objectOf(context);
      const batch = scoreBatchForPapp(value, true);
      const result = await invokePappTournament(
        { ...value, stage: batch.stage, round: batch.round },
        "read-score-batch",
        tournamentScoreBatchOverrides(value, batch),
      );
      if (!result || result.ok !== true || result.source !== "papp-c" ||
          result.verified !== true || result.batchId !== batch.batchId ||
          !Array.isArray(result.pairings) || result.pairings.length !== batch.pairings.length) {
        return scoreBatchFailure(result, "PAPP 持久化比分读回不完整");
      }

      const returnedIds = new Set();
      const pairings = batch.pairings.map(function (expected) {
        const matches = result.pairings.filter(function (raw) {
          return textOf(objectOf(raw).id) === expected.id;
        });
        if (matches.length !== 1 || returnedIds.has(expected.id)) {
          throw new Error("PAPP 读回的配对缺失或重复：" + expected.id);
        }
        const stored = objectOf(matches[0]);
        const sameIdentity = Number(stored.table) === expected.table &&
          textOf(stored.blackId) === expected.blackId &&
          textOf(stored.whiteId) === expected.whiteId &&
          textOf(stored.black) === expected.black &&
          textOf(stored.white) === expected.white &&
          textOf(stored.blackAccount) === expected.blackAccount &&
          textOf(stored.whiteAccount) === expected.whiteAccount;
        const sameScore = Number.isInteger(Number(stored.blackScore)) &&
          Number.isInteger(Number(stored.whiteScore)) &&
          Number(stored.blackScore) === expected.blackScore &&
          Number(stored.whiteScore) === expected.whiteScore &&
          Number(stored.blackScore) + Number(stored.whiteScore) === MAX_SCORE;
        if (!sameIdentity || !sameScore || textOf(stored.status) !== "completed") {
          throw new Error("PAPP 持久化读回与请求身份或比分不一致：" + expected.id);
        }
        returnedIds.add(expected.id);
        return {
          id: expected.id,
          table: expected.table,
          black: expected.black,
          white: expected.white,
          blackAccount: expected.blackAccount,
          whiteAccount: expected.whiteAccount,
          status: "completed",
          blackScore: Number(stored.blackScore),
          whiteScore: Number(stored.whiteScore),
        };
      });
      if (returnedIds.size !== batch.pairings.length) {
        throw new Error("PAPP 读回包含缺失或额外配对");
      }
      return { ok: true, batchId: batch.batchId, pairings: pairings };
    } catch (error) {
      return scoreBatchFailure(error, "PAPP 比分读回失败");
    }
  }

  function samePairing(left, right) {
    const a = objectOf(left);
    const b = objectOf(right);
    const leftId = keyOf(a.id || a.pairingId);
    const rightId = keyOf(b.id || b.pairingId);
    if (leftId && rightId && leftId === rightId) return true;
    const leftGame = keyOf(a.oqGameId || a.gameId);
    const rightGame = keyOf(b.oqGameId || b.gameId);
    if (leftGame && rightGame && leftGame === rightGame) return true;
    const leftTable = keyOf(a.table || a.pendingTable);
    const rightTable = keyOf(b.table || b.pendingTable);
    if (leftTable && rightTable && leftTable === rightTable) return true;
    const lb = keyOf(a.black);
    const lw = keyOf(a.white);
    const rb = keyOf(b.black);
    const rw = keyOf(b.white);
    return Boolean(lb && rb && ((lb === rb && lw === rw) || (lb === rw && lw === rb)));
  }

  function pendingFrom(pairing, reason) {
    const value = objectOf(pairing);
    return {
      pairingId: textOf(value.id),
      pendingTable: textOf(value.table),
      table: textOf(value.table),
      black: textOf(value.black),
      white: textOf(value.white),
      blackAccount: textOf(value.blackAccount),
      whiteAccount: textOf(value.whiteAccount),
      oqGameId: textOf(value.oqGameId),
      pendingKind: "oq-auto",
      reason: textOf(reason) || "OQ 尚未返回稳定结果",
    };
  }

  function mergeOqPairing(current, incoming) {
    const previous = objectOf(current);
    const next = { ...objectOf(copy(current)), ...objectOf(copy(incoming)) };
    ["id", "table", "black", "white", "blackAccount", "whiteAccount", "oqGameId"].forEach(function (field) {
      if (!textOf(next[field])) next[field] = previous[field];
    });

    const previousMetadata = objectOf(previous.metadata);
    const nextMetadata = objectOf(next.metadata);
    const mergedMetadata = {
      ...copy(previousMetadata),
      ...copy(nextMetadata),
    };
    if (previousMetadata.papp || nextMetadata.papp) {
      mergedMetadata.papp = {
        ...objectOf(copy(previousMetadata.papp)),
        ...objectOf(copy(nextMetadata.papp)),
      };
    }
    next.metadata = mergedMetadata;
    return next;
  }

  function normalizeOqResult(raw, context) {
    const value = objectOf(raw && raw.oq && typeof raw.oq === "object" ? raw.oq : raw);
    if (value.ok === false) return value;
    const current = existingPairings(context) || [];
    if (!current.length) {
      return {
        ok: true,
        ready: [],
        pending: [],
        skipped: [],
        gameAvailable: Array.isArray(value.gameAvailable) ? copy(value.gameAvailable) : [],
        queryErrors: copy(objectOf(value.queryErrors || value.errors)),
        window: value.window && typeof value.window === "object" ? copy(value.window) : null,
      };
    }
    const ready = [];
    const pending = [];

    (Array.isArray(value.ready) ? value.ready : []).forEach(function (item) {
      const valueItem = objectOf(item);
      const blackScore = scoreOrNull(valueItem.blackScore);
      const whiteScore = scoreOrNull(valueItem.whiteScore);
      if (blackScore !== null && whiteScore !== null) {
        const matching = current.find(function (pairing) { return samePairing(pairing, valueItem); });
        ready.push({
          ...mergeOqPairing(matching, valueItem),
          status: "ready",
          blackScore: blackScore,
          whiteScore: whiteScore,
        });
      } else {
        pending.push({
          ...copy(valueItem),
          pendingKind: "oq-auto",
          pendingTable: valueItem.pendingTable || valueItem.table,
          reason: "OQ 未返回完整比分，等待稳定结果",
        });
      }
    });

    (Array.isArray(value.pending) ? value.pending : []).forEach(function (item) {
      const valueItem = objectOf(item);
      const matching = current.find(function (pairing) { return samePairing(pairing, valueItem); });
      pending.push({
        ...(matching ? pendingFrom(matching, valueItem.reason) : {}),
        ...copy(valueItem),
        pairingId: textOf(valueItem.pairingId || (matching && matching.id)),
        pendingTable: textOf(valueItem.pendingTable || valueItem.table || (matching && matching.table)),
        blackAccount: textOf(valueItem.blackAccount) || textOf(matching && matching.blackAccount),
        whiteAccount: textOf(valueItem.whiteAccount) || textOf(matching && matching.whiteAccount),
        pendingKind: textOf(valueItem.pendingKind) || "oq-auto",
        reason: textOf(valueItem.reason) || "OQ 尚未返回稳定结果",
      });
    });

    const skipped = Array.isArray(value.skipped) ? copy(value.skipped) : [];
    current.forEach(function (pairing) {
      const status = keyOf(pairing.status);
      if (status === "completed" || status === "bye") return;
      if (ready.some(function (item) { return samePairing(item, pairing); })) return;
      if (skipped.some(function (item) { return samePairing(item, pairing); })) return;
      if (!pending.some(function (item) { return samePairing(item, pairing); })) {
        pending.push(pendingFrom(pairing));
      }
    });
    return {
      ok: value.ok !== false,
      ready: ready,
      pending: pending,
      skipped: skipped,
      gameAvailable: Array.isArray(value.gameAvailable) ? copy(value.gameAvailable) : [],
      queryErrors: copy(objectOf(value.queryErrors || value.errors)),
      window: value.window && typeof value.window === "object" ? copy(value.window) : null,
    };
  }

  async function requestJson(url, payload) {
    const fetcher = window.fetch || (typeof fetch === "function" ? fetch : null);
    if (typeof fetcher !== "function") {
      return { ok: false, code: "fetch-unavailable", message: "当前页面无法连接 PAPP 本地服务" };
    }
    let response;
    try {
      response = await fetcher(url, {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify(payload || {}),
      });
    } catch (error) {
      return {
        ok: false,
        code: "local-service-unreachable",
        message: "PAPP 本地服务连接失败：" + (textOf(error && error.message) || "网络错误"),
      };
    }
    const responseText = await response.text();
    let value;
    try {
      value = responseText ? JSON.parse(responseText) : {};
    } catch (_) {
      value = { ok: false, code: "invalid-json", message: "PAPP 本地服务返回了无效 JSON" };
    }
    if (!response.ok && value.ok !== false) {
      value = { ok: false, code: "http-" + response.status, message: "PAPP 本地服务返回 HTTP " + response.status };
    }
    return value;
  }

  async function requestGetJson(url) {
    const fetcher = window.fetch || (typeof fetch === "function" ? fetch : null);
    if (typeof fetcher !== "function") {
      return { ok: false, code: "fetch-unavailable", message: "当前页面无法连接 PAPP 本地服务" };
    }
    let response;
    try {
      response = await fetcher(url, { method: "GET", cache: "no-store" });
    } catch (error) {
      return {
        ok: false,
        code: "local-service-unreachable",
        message: "PAPP 本地服务连接失败：" + (textOf(error && error.message) || "网络错误"),
      };
    }
    let value;
    try {
      value = await response.json();
    } catch (_) {
      value = null;
    }
    if (!response.ok || !value || value.ok !== true) {
      return {
        ok: false,
        code: "local-service-read-failed",
        message: "PAPP 本地服务没有返回有效数据",
      };
    }
    return value;
  }

  function requestPappC(payload) {
    return requestJson("/api/papp/tournament", payload);
  }

  function cPlayerPool(context) {
    const candidates = candidatePlayersFromContext(context);
    const players = Array.isArray(candidates) ? candidates : playersOf(context);
    return pappFieldsForPlayers(players, objectOf(context));
  }

  function findCPlayer(pool, id) {
    const key = textOf(id);
    return key ? pool.find(function (player) { return playerId(player) === key; }) || null : null;
  }

  function cPairingPlayerId(pairing, side, pool, context) {
    const value = objectOf(pairing);
    const metadata = objectOf(value.metadata);
    const papp = objectOf(metadata.papp);
    const playerIds = objectOf(metadata.playerIds);
    const direct = textOf(value[side + "PlayerId"] || value[side + "Id"] ||
      papp[side + "PlayerId"] || playerIds[side] || metadata[side + "PlayerId"]);
    if (findCPlayer(pool, direct)) return direct;
    const resolved = resolvePlayer(sideReference(value, side), pool, context);
    return playerId(resolved);
  }

  function cPairingInput(pairing, pool, context) {
    const value = objectOf(pairing);
    const status = textOf(value.status || "imported").toLowerCase();
    const id = textOf(value.id || value.pairingId || value.sourceLocalId);
    const row = {
      id: id,
      table: value.table,
      status: status,
      blackScore: value.blackScore === undefined ? null : value.blackScore,
      whiteScore: value.whiteScore === undefined ? null : value.whiteScore,
    };
    if (status === "bye") {
      row.playerId = textOf(value.playerId || value.byePlayerId) || cPairingPlayerId(value, "black", pool, context);
      return row;
    }
    row.blackId = cPairingPlayerId(value, "black", pool, context);
    row.whiteId = cPairingPlayerId(value, "white", pool, context);
    const metadata = objectOf(value.metadata);
    const phase = textOf(value.phase || objectOf(metadata.papp).phase || metadata.phase);
    if (phase) row.phase = phase;
    return row;
  }

  function cRoundPairings(roundData, pool, context) {
    const round = objectOf(roundData);
    const pairings = Array.isArray(round.pairings) ? round.pairings : [];
    if (pairings.some(isLegacyPairing)) return [];
    return pairings.map(function (pairing) { return cPairingInput(pairing, pool, context); });
  }

  function cPresentIds(roundData, pairings, pool, context) {
    const round = objectOf(roundData);
    const explicit = Array.isArray(round.presentPlayerIds) ? round.presentPlayerIds : null;
    const ids = new Set();
    if (explicit) {
      explicit.forEach(function (id) {
        const resolved = findCPlayer(pool, textOf(id)) || resolvePlayer(id, pool, context);
        const key = playerId(resolved);
        if (key) ids.add(key);
      });
      return Array.from(ids);
    }
    pairings.forEach(function (pairing) {
      if (pairing.status === "bye") {
        if (pairing.playerId) ids.add(pairing.playerId);
      } else {
        if (pairing.blackId) ids.add(pairing.blackId);
        if (pairing.whiteId) ids.add(pairing.whiteId);
      }
    });
    return Array.from(ids);
  }

  function cTournamentPlayers(context, pool) {
    const value = objectOf(context);
    const helper = objectOf(objectOf(value.state).scoreHelper);
    const rounds = Array.isArray(helper.rounds) ? helper.rounds : [];
    const playoff = objectOf(value.playoffRegistration || objectOf(value.state).playoffRegistration);
    const ids = new Set();
    const checked = Array.isArray(value.checkedInPlayers)
      ? value.checkedInPlayers
      : pool.filter(function (player) { return objectOf(player).checkedIn === true; });
    checked.forEach(function (player) {
      const id = playerId(player);
      if (id) ids.add(id);
    });
    rounds.slice(0, preliminaryRoundCountOf(context)).forEach(function (round) {
      const mapped = cRoundPairings(round, pool, context);
      mapped.forEach(function (pairing) {
        if (pairing.status === "bye") {
          if (pairing.playerId) ids.add(pairing.playerId);
        } else {
          if (pairing.blackId) ids.add(pairing.blackId);
          if (pairing.whiteId) ids.add(pairing.whiteId);
        }
      });
    });
    [
      Array.isArray(value.semifinalPairings) ? value.semifinalPairings : playoff.semifinalPairings,
      Array.isArray(value.placementPairings) ? value.placementPairings : playoff.placementPairings,
    ].forEach(function (pairings) {
      (Array.isArray(pairings) ? pairings : []).forEach(function (pairing) {
        const black = cPairingPlayerId(pairing, "black", pool, context);
        const white = cPairingPlayerId(pairing, "white", pool, context);
        if (black) ids.add(black);
        if (white) ids.add(white);
      });
    });
    if (!ids.size && !hasCheckinRoster(context)) {
      pool.forEach(function (player) {
        const id = playerId(player);
        if (id) ids.add(id);
      });
    }
    return pool.filter(function (player) { return ids.has(playerId(player)); });
  }

  function cTournamentPayload(context, operation) {
    const value = objectOf(context);
    const state = objectOf(value.state);
    const helper = objectOf(state.scoreHelper);
    const parameters = tournamentParametersOf(value);
    const pool = cPlayerPool(value);
    const players = cTournamentPlayers(value, pool);
    const sourceRounds = Array.isArray(helper.rounds) ? helper.rounds : [];
    const preliminaryRoundCount = preliminaryRoundCountOf(value);
    const rounds = [];
    let i;
    for (i = 0; i < preliminaryRoundCount; i++) {
      const roundData = objectOf(sourceRounds[i]);
      const pairings = cRoundPairings(roundData, pool, value);
      rounds.push({
        round: i + 1,
        pairings: pairings,
        presentPlayerIds: cPresentIds(roundData, pairings, pool, value),
      });
    }
    const checkedIn = Array.isArray(value.checkedInPlayers)
      ? value.checkedInPlayers.filter(function (player) { return objectOf(player).checkedIn === true; })
      : playersOf(value).filter(function (player) { return objectOf(player).checkedIn !== false; });
    const playoff = objectOf(value.playoffRegistration || state.playoffRegistration);
    const semifinalPairings = Array.isArray(value.semifinalPairings)
      ? value.semifinalPairings : Array.isArray(playoff.semifinalPairings) ? playoff.semifinalPairings : [];
    const placementPairings = Array.isArray(value.placementPairings)
      ? value.placementPairings : Array.isArray(playoff.placementPairings) ? playoff.placementPairings : [];
    return {
      operation: operation,
      round: roundOf(value),
      stage: value.stage,
      preliminaryRoundCount: preliminaryRoundCount,
      roundCount: preliminaryRoundCount,
      pappWorkfileId: textOf(helper.pappWorkfileId),
      players: players.map(function (player) {
        return {
          id: playerId(player),
          displayName: playerName(player),
          pappName: playerName(player),
          account: accountForPlayer(player, value) || playerAccount(player),
        };
      }),
      rounds: rounds,
      presentPlayerIds: checkedIn.map(playerId).filter(Boolean),
      hasSemifinalAndFinal: parameters.hasSemifinalAndFinal,
      skipSemifinal: parameters.skipSemifinal === true,
      tournamentParameters: parameters,
      brightwellConstant: parameters.brightwellConstant,
      semifinalPairings: semifinalPairings.filter(function (pairing) {
        return !isLegacyPairing(pairing);
      }).map(function (pairing) {
        return cPairingInput(pairing, pool, value);
      }),
      placementPairings: placementPairings.filter(function (pairing) {
        return !isLegacyPairing(pairing);
      }).map(function (pairing) {
        return cPairingInput(pairing, pool, value);
      }),
    };
  }

  async function invokePappTournament(context, operation, overrides) {
    const payload = { ...cTournamentPayload(context, operation), ...objectOf(overrides) };
    payload.operation = operation;
    return requestPappC(payload);
  }

  function requestedCStage(context) {
    const value = objectOf(context);
    const requested = keyOf(value.stage);
    const mode = keyOf(value.mode);
    if (requested === "placement" || mode === "advance-playoff-stage" ||
        roundOf(value) === preliminaryRoundCountOf(value) + 2) return "placement";
    if (requested === "semifinal" || mode === "playoff-registration" ||
        roundOf(value) === preliminaryRoundCountOf(value) + 1) return "semifinal";
    return "preliminary";
  }

  function mapCPairingToUi(pairing, players, context, sourceOverride) {
    const value = objectOf(pairing);
    const blackId = textOf(value.blackId || value.playerId);
    const whiteId = textOf(value.whiteId);
    const blackPlayer = findCPlayer(players, blackId);
    const whitePlayer = findCPlayer(players, whiteId);
    const black = playerName(blackPlayer) || textOf(value.black || value.blackName || value.playerName);
    const white = playerName(whitePlayer) || textOf(value.white || value.whiteName || "BYE");
    const metadata = objectOf(value.metadata);
    const metadataPapp = objectOf(metadata.papp);
    return {
      ...copy(value),
      black: black,
      blackName: black,
      white: white,
      whiteName: white,
      blackId: blackId,
      whiteId: whiteId,
      blackPlayerId: blackId,
      whitePlayerId: whiteId,
      blackAccount: sideAccount(value, "black", blackPlayer, context),
      whiteAccount: sideAccount(value, "white", whitePlayer, context),
      source: sourceOverride || value.source || "papp-c",
      metadata: {
        ...copy(metadata),
        papp: {
          ...copy(metadataPapp),
          source: value.source || "papp-c",
          blackPlayerId: blackId,
          whitePlayerId: whiteId,
          phase: value.phase || metadataPapp.phase || "",
        },
      },
    };
  }

  async function getRoundCount(context) {
    const value = objectOf(context);
    const helper = objectOf(objectOf(value.state).scoreHelper);
    const checkedInCount = Array.isArray(value.checkedInPlayers)
      ? value.checkedInPlayers.filter(function (player) { return objectOf(player).checkedIn === true; }).length
      : playersOf(value).filter(function (player) { return objectOf(player).checkedIn !== false; }).length;
    const playerCount = Number(value.playerCount ?? value.checkedInPlayerCount ?? checkedInCount);
    const manual = value.manualRoundCount !== undefined
      ? value.manualRoundCount
      : helper.roundCountSource === "manual" ? helper.preliminaryRoundCount : undefined;
    return requestPappC({
      operation: "round-count",
      playerCount: Number.isFinite(playerCount) ? playerCount : 0,
      ...(manual === undefined ? {} : { manualRoundCount: manual }),
    });
  }

  async function validateScore(context) {
    const value = objectOf(context);
    return requestPappC({
      operation: "validate-score",
      blackScore: value.blackScore,
      whiteScore: value.whiteScore,
    });
  }

  async function pollOqRound(context) {
    const roundData = objectOf(context && context.roundData);
    const currentPairings = (existingPairings(context) || []).map(function (pairing) {
      return mapPairingAccounts(pairing, context);
    });
    if (currentPairings.some(isLegacyPairing)) {
      return {
        ok: true,
        source: "legacy-history",
        readOnly: true,
        ready: [],
        pending: [],
        skipped: [],
        gameAvailable: [],
      };
    }
    const roundStartAt = textOf(context && context.roundStartAt || roundData.roundStartAt);
    if (!roundStartAt) {
      return { ok: false, code: "round-start-missing", message: "OQ 查询需要先设置本轮开始时间" };
    }
    const provider = window.PAPP_OQ_PROVIDER;
    let rawSnapshot = context && context.oqPollResult
      ? context.oqPollResult
      : objectOf(roundData.metadata).oqPollResult;
    if (provider && typeof provider.pollRound === "function") {
      rawSnapshot = await provider.pollRound({ ...context, roundStartAt: roundStartAt });
    }
    const currentStage = keyOf(context && context.stage || roundData.stage || "preliminary");
    const mappedRoundData = { ...copy(roundData), pairings: copy(currentPairings) };
    if (roundStartAt) mappedRoundData.roundStartAt = roundStartAt;
    const egRounds = oqRoundsForPolling({
      ...context,
      stage: currentStage,
      roundData: mappedRoundData,
    }, currentPairings);
    return requestJson("/api/papp/oq/poll", {
      round: roundOf(context),
      stage: currentStage,
      roundStartAt: roundStartAt,
      roundEndAt: textOf(context && context.roundEndAt || roundData.roundEndAt),
      windowMinutes: Number(context && context.windowMinutes || roundData.windowMinutes) || null,
      roundData: mappedRoundData,
      pairings: copy(currentPairings),
      egRounds: egRounds,
      oqPollResult: copy(rawSnapshot),
    });
  }

  function egPayload(context) {
    return {
      round: roundOf(context),
      stage: textOf(context && context.stage || objectOf(context && context.roundData).stage || "preliminary"),
      roundStartAt: textOf(context && context.roundStartAt),
      roundData: copy(context && context.roundData),
      pairings: copy(Array.isArray(context && context.pairings)
        ? context.pairings
        : existingPairings(context) || []),
      state: copy(context && context.state),
    };
  }

  async function getEgAnalysisStatus(context) {
    const provider = window.PAPP_EG_PROVIDER;
    if (provider && typeof provider.getStatus === "function") return provider.getStatus(context);
    return requestJson("/api/papp/eg/status", egPayload(context));
  }

  async function startEgAnalysis(context) {
    const provider = window.PAPP_EG_PROVIDER;
    if (provider && typeof provider.start === "function") return provider.start(context);
    return requestJson("/api/papp/eg/start", egPayload(context));
  }

  async function stopEgAnalysis(context) {
    const provider = window.PAPP_EG_PROVIDER;
    if (provider && typeof provider.stop === "function") return provider.stop(context);
    return requestJson("/api/papp/eg/stop", egPayload(context));
  }

  function notReady(method) {
    return Promise.resolve({
      ok: false,
      code: "adapter-not-ready",
      method: method,
      message: "PAPP 编排适配器尚未实现：" + method,
    });
  }

  window.PAPP_TOURNAMENT_ADAPTER = {
    ...previous,
    version: VERSION,
    getCandidates: getCandidates,
    syncCandidates: syncCandidates,
    importPairings: importPairings,
    refreshRound: refreshRound,
    registerScore: registerScore,
    writeScoreBatch: writeScoreBatch,
    readScoreBatch: readScoreBatch,
    getRoundCount: getRoundCount,
    validateScore: validateScore,
    getPreliminaryStandings: getPreliminaryStandings,
    getRoundStandings: getRoundStandings,
    getStageStatus: getStageStatus,
    getOverallStandings: getOverallStandings,
    pollOqRound: pollOqRound,
    getEgAnalysisStatus: getEgAnalysisStatus,
    startEgAnalysis: startEgAnalysis,
    stopEgAnalysis: stopEgAnalysis,
    importPairingsFromText: function (source, context) {
      return importPairings({ ...(context || {}), pairingSource: source });
    },
    getEgAnalysisReport: typeof previous.getEgAnalysisReport === "function"
      ? previous.getEgAnalysisReport
      : function () { return notReady("getEgAnalysisReport"); },
  };
})();
