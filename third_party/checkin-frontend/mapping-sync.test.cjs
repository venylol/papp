"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  accountTokenFromGroupNick,
  buildMappingPlayersForPappSync,
  matchGroupNicksToRosterPlayers,
  mappingOqRatingLabel,
  mappingGroupCacheQuery,
  mappingGroupInputValue,
  mappingGroupNickHasIdentityMismatch,
  mappingGroupRefreshQuery,
  mappingGroupTargetKey,
  mappingRowsForRoster,
  reconcileGroupNicksWithCandidates,
  reconcileHistoricalRelayGroupNicks,
  reconcileMappingRowsWithCandidates,
  sanitizeMapping,
  sanitizeMappingCheck,
  synchronizeMappingGroupToSelectedChat,
  syncMappingFieldToCheckinPlayer,
  transferSelectedMappingText,
  wechatRelayMonthUnixRange,
} = require("./app.js");

test("adds every new check-in candidate to mappings, including unchecked players", () => {
  const mapping = {
    rows: [
      {
        id: "mapping-alice",
        registrationNick: "Alice",
        oqAccount: "alice-oq",
        checkinPlayerId: "1",
      },
    ],
    excludedCheckinPlayerIds: [],
  };
  const candidates = [
    { id: 1, displayName: "Alice", account: "alice-oq", platform: "oq", checkedIn: true },
    { id: 2, displayName: "Bob", account: "bob-oq", platform: "oq", checkedIn: false },
  ];

  const result = reconcileMappingRowsWithCandidates(mapping, candidates);

  assert.equal(result.addedCount, 1);
  assert.equal(mapping.rows.length, 2);
  assert.deepEqual(
    {
      registrationNick: mapping.rows[1].registrationNick,
      oqAccount: mapping.rows[1].oqAccount,
      checkinPlayerId: mapping.rows[1].checkinPlayerId,
    },
    { registrationNick: "Bob", oqAccount: "bob-oq", checkinPlayerId: "2" },
  );
  assert.deepEqual(buildMappingPlayersForPappSync(mapping, candidates), [
    {
      mappingRowId: "mapping-alice",
      candidatePlayerId: "1",
      name: "Alice",
      country: "alice-oq",
    },
    {
      mappingRowId: "checkin-2",
      candidatePlayerId: "2",
      name: "Bob",
      country: "bob-oq",
    },
  ]);
});

test("mapping rows follow the roster and ignore group-only or stale rows", () => {
  const mapping = {
    rows: [
      { id: "nick-only", wechatNick: "unregistered member" },
      { id: "checkin-b", checkinPlayerId: "2", registrationNick: "B" },
      { id: "stale", checkinPlayerId: "9", registrationNick: "Old" },
      { id: "checkin-a", checkinPlayerId: "1", registrationNick: "A" },
    ],
  };
  const roster = [
    { id: 1, displayName: "A" },
    { id: 2, displayName: "B" },
  ];

  assert.deepEqual(
    mappingRowsForRoster(mapping, roster).map((row) => row.id),
    ["checkin-a", "checkin-b"],
  );
});

test("mapping rows sort by missing-field count while keeping roster order within each count", () => {
  const mapping = {
    rows: [
      { id: "complete", checkinPlayerId: "1", wechatNick: "Group", registrationNick: "A", oqAccount: "a" },
      { id: "one-missing", checkinPlayerId: "2", wechatNick: "Group", registrationNick: "B", oqAccount: "", scriptLocked: true },
      { id: "two-missing-first", checkinPlayerId: "3", wechatNick: "", registrationNick: "C", oqAccount: "", scriptLocked: true },
      { id: "three-missing", checkinPlayerId: "4", wechatNick: "", registrationNick: "", oqAccount: "" },
      { id: "two-missing-second", checkinPlayerId: "5", wechatNick: "", registrationNick: "E", oqAccount: "" },
    ],
  };
  const roster = [1, 2, 3, 4, 5].map((id) => ({ id, displayName: `Player ${id}` }));

  assert.deepEqual(
    mappingRowsForRoster(mapping, roster).map((row) => row.id),
    ["three-missing", "two-missing-first", "two-missing-second", "one-missing", "complete"],
  );
});

test("historical relay attribution uses the earliest message containing the full identity", () => {
  const mapping = {
    rows: [
      {
        id: "player-1",
        checkinPlayerId: "1",
        registrationNick: "王万里",
        oqAccount: "wangwanli88",
        wechatNick: "",
        wechatNickSource: "",
      },
    ],
  };
  const roster = [{ id: 1, displayName: "王万里", account: "wangwanli88" }];
  const messages = [
    {
      messageId: "later",
      createTime: 30,
      senderGroupNick: "后来的群昵称",
      content: "# 接龙\n1. 王万里\n2. OQ: wangwanli88",
    },
    {
      messageId: "name-only",
      createTime: 10,
      senderGroupNick: "只有姓名的发送者",
      content: "# 接龙\n1. 王万里\n2. Other Player",
    },
    {
      messageId: "first-full-identity",
      createTime: 20,
      senderGroupNick: "首次出现完整身份的群昵称",
      content: "# 接龙\n1. 王万里\n2. OQ: wangwanli88",
    },
  ];

  const result = reconcileHistoricalRelayGroupNicks(mapping, roster, messages);

  assert.equal(result.matchedCount, 1);
  assert.equal(mapping.rows[0].wechatNick, "首次出现完整身份的群昵称");
  assert.equal(mapping.rows[0].wechatNickSource, "history");
});

test("current group nicknames replace stale automatic and historical mappings", () => {
  const roster = [{ id: 1, displayName: "王万里", account: "wangwanli88" }];
  for (const source of ["auto", "history"]) {
    const mapping = {
      groupNicks: ["王万里 wangwanli88"],
      excludedCheckinPlayerIds: [],
      rows: [
        {
          id: "player-1",
          checkinPlayerId: "1",
          registrationNick: "王万里",
          oqAccount: "wangwanli88",
          wechatNick: "旧群昵称",
          wechatNickSource: source,
          scriptLocked: false,
        },
      ],
    };

    const result = reconcileGroupNicksWithCandidates(mapping, roster);

    assert.equal(result.autoMatchedCount, 1, source);
    assert.equal(mapping.rows[0].wechatNick, "王万里 wangwanli88", source);
    assert.equal(mapping.rows[0].wechatNickSource, "auto", source);
  }
});

test("current group nickname wins after historical relay attribution", () => {
  const mapping = {
    groupNicks: ["王万里 wangwanli88"],
    excludedCheckinPlayerIds: [],
    rows: [
      {
        id: "player-1",
        checkinPlayerId: "1",
        registrationNick: "王万里",
        oqAccount: "wangwanli88",
        wechatNick: "旧自动昵称",
        wechatNickSource: "auto",
        scriptLocked: false,
      },
    ],
  };
  const roster = [{ id: 1, displayName: "王万里", account: "wangwanli88" }];

  reconcileHistoricalRelayGroupNicks(mapping, roster, [
    {
      messageId: "old-relay",
      createTime: 1,
      senderGroupNick: "历史接龙昵称",
      content: "# 接龙\n1. 王万里\n2. OQ: wangwanli88",
    },
  ]);
  assert.equal(mapping.rows[0].wechatNick, "历史接龙昵称");
  reconcileGroupNicksWithCandidates(mapping, roster);

  assert.equal(mapping.rows[0].wechatNick, "王万里 wangwanli88");
  assert.equal(mapping.rows[0].wechatNickSource, "auto");
});

test("historical relay attribution accepts nickname-only identities and requires one message", () => {
  const mapping = {
    rows: [
      {
        id: "player-1",
        checkinPlayerId: "1",
        registrationNick: "张三",
        oqAccount: "",
        wechatNick: "",
      },
      {
        id: "player-2",
        checkinPlayerId: "2",
        registrationNick: "李四",
        oqAccount: "li-si-oq",
        wechatNick: "",
      },
    ],
  };
  const roster = [
    { id: 1, displayName: "张三", account: "" },
    { id: 2, displayName: "李四", account: "li-si-oq" },
  ];
  const messages = [
    {
      messageId: "name-only",
      createTime: 1,
      senderGroupNick: "张三本人",
      content: "# 接龙\n1. 张三\n2. 其他选手",
    },
    {
      messageId: "name-without-account",
      createTime: 2,
      senderGroupNick: "仅出现姓名的李四发送者",
      content: "# 接龙\n1. 李四\n2. 其他选手",
    },
    {
      messageId: "account-without-name",
      createTime: 3,
      senderGroupNick: "仅出现账号的发送者",
      content: "# 接龙\n1. li-si-oq\n2. 其他选手",
    },
  ];

  const result = reconcileHistoricalRelayGroupNicks(mapping, roster, messages);

  assert.equal(result.matchedCount, 1);
  assert.equal(mapping.rows[0].wechatNick, "张三本人");
  assert.equal(mapping.rows[0].wechatNickSource, "history");
  assert.equal(mapping.rows[1].wechatNick, "");
});

test("historical and group-nickname scripts leave locked rows untouched", () => {
  const mapping = {
    groupNicks: ["Alice alice-oq"],
    rows: [
      {
        id: "player-1",
        checkinPlayerId: "1",
        registrationNick: "",
        oqAccount: "",
        wechatNick: "",
        scriptLocked: true,
      },
    ],
  };
  const roster = [{ id: 1, displayName: "Alice", account: "alice-oq" }];

  const historical = reconcileHistoricalRelayGroupNicks(mapping, roster, [
    {
      messageId: "history",
      createTime: 1,
      senderGroupNick: "历史群昵称",
      content: "# 接龙\n1. Alice\n2. alice-oq",
    },
  ]);
  const automatic = reconcileGroupNicksWithCandidates(mapping, roster);

  assert.equal(historical.matchedCount, 0);
  assert.equal(automatic.autoMatchedCount, 0);
  assert.deepEqual(
    {
      wechatNick: mapping.rows[0].wechatNick,
      registrationNick: mapping.rows[0].registrationNick,
      oqAccount: mapping.rows[0].oqAccount,
      scriptLocked: mapping.rows[0].scriptLocked,
    },
    { wechatNick: "", registrationNick: "", oqAccount: "", scriptLocked: true },
  );
});

test("mapping sanitization preserves historical nickname source and row lock", () => {
  const mapping = sanitizeMapping({
    rows: [
      {
        id: "locked-history",
        registrationNick: "A",
        wechatNick: "Group A",
        wechatNickSource: "history",
        scriptLocked: true,
      },
    ],
  });

  assert.equal(mapping.rows[0].wechatNickSource, "history");
  assert.equal(mapping.rows[0].scriptLocked, true);
});

test("warns when the current group nickname shares no name or account terms", () => {
  assert.equal(
    mappingGroupNickHasIdentityMismatch({
      wechatNick: "阿梅 sunshine",
      wechatNickSource: "auto",
      registrationNick: "李华",
      oqAccount: "lihua88",
    }),
    true,
  );
  assert.equal(
    mappingGroupNickHasIdentityMismatch({
      wechatNick: "WangWanli 王万里",
      wechatNickSource: "history",
      registrationNick: "王万里",
      oqAccount: "other-account",
    }),
    false,
  );
  assert.equal(
    mappingGroupNickHasIdentityMismatch({
      wechatNick: "sunshine_99",
      wechatNickSource: "auto",
      registrationNick: "李华",
      oqAccount: "sunshine88",
    }),
    false,
  );
  assert.equal(
    mappingGroupNickHasIdentityMismatch({
      wechatNick: "阿梅 sunshine",
      wechatNickSource: "manual",
      registrationNick: "李华",
      oqAccount: "lihua88",
    }),
    true,
  );
  assert.equal(
    mappingGroupNickHasIdentityMismatch({
      wechatNick: "lihua88",
      wechatNickSource: "manual",
      registrationNick: "李华",
      oqAccount: "lihua88",
    }),
    false,
  );
  assert.equal(
    mappingGroupNickHasIdentityMismatch({
      wechatNick: "阿梅 sunshine",
      wechatNickSource: "auto",
      registrationNick: "",
      oqAccount: "",
    }),
    false,
  );
});

test("manual group nick stays fixed while linked identity fields can be filled", () => {
  const mapping = sanitizeMapping({
    groupNicks: ["张三 zhangsan"],
    excludedCheckinPlayerIds: [],
    rows: [
      {
        id: "manual-player",
        checkinPlayerId: "1",
        wechatNick: "用户手动昵称",
        wechatNickSource: "manual",
        registrationNick: "",
        oqAccount: "",
        oqCheck: sanitizeMappingCheck(null),
        scriptLocked: false,
      },
    ],
  });
  const roster = [{ id: 1, displayName: "张三", account: "zhangsan" }];

  const rowResult = reconcileMappingRowsWithCandidates(mapping, roster);
  const historicalResult = reconcileHistoricalRelayGroupNicks(mapping, roster, [
    {
      messageId: "history",
      createTime: 1,
      senderGroupNick: "历史自动昵称",
      content: "# 接龙\n1. 张三\n2. zhangsan",
    },
  ]);
  const candidateResult = reconcileGroupNicksWithCandidates(mapping, roster);

  assert.equal(rowResult.changed, true);
  assert.equal(historicalResult.matchedCount, 0);
  assert.equal(candidateResult.autoMatchedCount, 0);
  assert.deepEqual(
    {
      wechatNick: mapping.rows[0].wechatNick,
      wechatNickSource: mapping.rows[0].wechatNickSource,
      registrationNick: mapping.rows[0].registrationNick,
      oqAccount: mapping.rows[0].oqAccount,
      scriptLocked: mapping.rows[0].scriptLocked,
    },
    {
      wechatNick: "用户手动昵称",
      wechatNickSource: "manual",
      registrationNick: "张三",
      oqAccount: "zhangsan",
      scriptLocked: false,
    },
  );
});

test("mapping nickname suggestions follow the selected event chat", () => {
  const mapping = sanitizeMapping({
    groupName: "旧新人赛群",
    groupUsername: "@old-chatroom",
    groupNicks: ["旧群成员"],
    memberCount: 1,
    mappedCount: 1,
    refreshedAt: "2026-08-10T10:00:00",
    rows: [{ id: "p1", wechatNick: "已录入昵称", registrationNick: "选手甲" }],
  });
  const selectedGroup = {
    username: "@new-chatroom",
    queryIndex: "@new-chatroom",
    displayName: "新人赛群",
  };

  assert.equal(synchronizeMappingGroupToSelectedChat(mapping, selectedGroup), true);
  assert.equal(mapping.groupName, "新人赛群");
  assert.equal(mapping.groupUsername, "@new-chatroom");
  assert.deepEqual(mapping.groupNicks, []);
  assert.equal(mapping.memberCount, 0);
  assert.equal(mapping.mappedCount, 0);
  assert.equal(mapping.refreshedAt, "");
  assert.equal(mapping.rows[0].wechatNick, "已录入昵称");
  assert.equal(mappingGroupRefreshQuery(mapping, selectedGroup), "@new-chatroom");
  assert.equal(mappingGroupCacheQuery(mapping, selectedGroup), "新人赛群");
  assert.equal(mappingGroupInputValue(mapping, selectedGroup), "新人赛群");
  assert.equal(mappingGroupTargetKey(mapping, selectedGroup), "chat:@new-chatroom");
});

test("legacy nickname pools without a source group are cleared on first selected-chat sync", () => {
  const mapping = sanitizeMapping({
    groupName: "旧群",
    groupNicks: ["来源不明的旧昵称"],
    refreshedAt: "2026-08-10T10:00:00",
  });

  assert.equal(
    synchronizeMappingGroupToSelectedChat(mapping, {
      username: "@rookie-chatroom",
      queryIndex: "@rookie-chatroom",
      displayName: "新人赛群",
    }),
    true,
  );
  assert.deepEqual(mapping.groupNicks, []);
  assert.equal(mapping.groupUsername, "@rookie-chatroom");
});

test("same-chat nickname pools and explicit manual group overrides are preserved", () => {
  const selectedGroup = {
    username: "@rookie-chatroom",
    queryIndex: "@rookie-chatroom",
    displayName: "新人赛群",
  };
  const sameChat = sanitizeMapping({
    groupName: "新人赛群",
    groupUsername: "@rookie-chatroom",
    groupNicks: ["当前群成员"],
    memberCount: 1,
    mappedCount: 1,
    refreshedAt: "2026-09-12T12:00:00",
  });
  assert.equal(synchronizeMappingGroupToSelectedChat(sameChat, selectedGroup), false);
  assert.deepEqual(sameChat.groupNicks, ["当前群成员"]);

  const manual = sanitizeMapping({
    groupName: "备用群",
    groupUsername: "@backup-chatroom",
    groupOverride: "@backup-chatroom",
    groupNicks: ["备用群成员"],
  });
  assert.equal(synchronizeMappingGroupToSelectedChat(manual, selectedGroup), false);
  assert.equal(mappingGroupRefreshQuery(manual, selectedGroup), "@backup-chatroom");
  assert.equal(mappingGroupCacheQuery(manual, selectedGroup), "备用群");
  assert.equal(mappingGroupInputValue(manual, selectedGroup), "@backup-chatroom");
  assert.deepEqual(manual.groupNicks, ["备用群成员"]);
});

test("relay history month range covers the whole local calendar month", () => {
  const range = wechatRelayMonthUnixRange("2025-02-14T18:30");
  assert.equal(range.startTime, Math.floor(new Date(2025, 1, 1, 0, 0, 0, 0).getTime() / 1000));
  assert.equal(range.endTime, Math.floor(new Date(2025, 2, 1, 0, 0, 0, 0).getTime() / 1000) - 1);
});

test("mapping name and OQ account edits update only the linked check-in player", () => {
  const row = {
    checkinPlayerId: "player-1",
    registrationNick: "报名新姓名",
    oqAccount: "oq-new",
  };
  const roster = [
    { id: "player-1", displayName: "报名旧姓名", account: "oq-old", platform: "" },
    { id: "player-2", displayName: "报名旧姓名", account: "other-oq", platform: "vint" },
  ];

  assert.equal(syncMappingFieldToCheckinPlayer(row, "registrationNick", roster), true);
  assert.equal(roster[0].displayName, "报名新姓名");
  assert.equal(roster[1].displayName, "报名旧姓名");

  assert.equal(syncMappingFieldToCheckinPlayer(row, "oqAccount", roster), true);
  assert.equal(roster[0].account, "oq-new");
  assert.equal(roster[0].platform, "oq");
  assert.equal(roster[1].account, "other-oq");
  assert.equal(roster[1].platform, "vint");
});

test("mapping edits do not change check-in players without a linked ID", () => {
  const row = { registrationNick: "Changed", oqAccount: "changed-oq" };
  const roster = [{ id: "player-1", displayName: "Unchanged", account: "old-oq" }];

  assert.equal(syncMappingFieldToCheckinPlayer(row, "registrationNick", roster), false);
  assert.equal(syncMappingFieldToCheckinPlayer(row, "oqAccount", roster), false);
  assert.deepEqual(roster, [
    { id: "player-1", displayName: "Unchanged", account: "old-oq" },
  ]);
});

test("a blank mapping name does not erase the required check-in player name", () => {
  const row = { checkinPlayerId: "player-1", registrationNick: "" };
  const roster = [{ id: "player-1", displayName: "Existing Name" }];

  assert.equal(syncMappingFieldToCheckinPlayer(row, "registrationNick", roster), false);
  assert.equal(roster[0].displayName, "Existing Name");
});

test("legacy nickname rows stay visible after their roster name is edited", () => {
  const mapping = {
    rows: [
      { id: "legacy-row", registrationNick: "Roster Name", wechatNick: "Group Nick" },
    ],
    excludedCheckinPlayerIds: [],
  };
  const roster = [{ id: "player-1", displayName: "Roster Name" }];

  reconcileMappingRowsWithCandidates(mapping, roster);
  assert.equal(mapping.rows[0].checkinPlayerId, "player-1");
  assert.deepEqual(mappingRowsForRoster(mapping, roster).map((row) => row.id), ["legacy-row"]);

  mapping.rows[0].registrationNick = "Edited Name";
  assert.deepEqual(mappingRowsForRoster(mapping, roster).map((row) => row.id), ["legacy-row"]);
});

test("transfers selected name text into an empty OQ field and preserves the remaining name", () => {
  assert.deepEqual(
    transferSelectedMappingText("WangWanli OQ_Account", "", 10, 20),
    {
      ok: true,
      registrationNick: "WangWanli",
      oqAccount: "OQ_Account",
      selectedText: "OQ_Account",
    },
  );
  assert.deepEqual(
    transferSelectedMappingText("WangWanli handle", "existing", 10, 16),
    { ok: false, reason: "account-occupied" },
  );
});

test("questionable OQ rating uses a trailing question mark without parentheses", () => {
  const rating = mappingOqRatingLabel({
    status: "ok",
    rating: 196,
    n: 12,
  });

  assert.equal(rating.text, "196？");
  assert.equal(rating.className, "mapping-oq-rating--suspect");
});

test("keeps mapping rows when candidates leave and excludes them from PAPP updates", () => {
  const mapping = {
    rows: [
      { id: "mapping-old", registrationNick: "Old", oqAccount: "old-oq", checkinPlayerId: "1" },
      { id: "mapping-current", registrationNick: "Current", oqAccount: "current-oq", checkinPlayerId: "2" },
    ],
    excludedCheckinPlayerIds: [],
  };
  const remainingCandidates = [
    { id: 2, displayName: "Current", account: "current-oq", platform: "oq", checkedIn: false },
  ];

  const result = reconcileMappingRowsWithCandidates(mapping, remainingCandidates);

  assert.equal(result.addedCount, 0);
  assert.equal(mapping.rows.length, 2);
  assert.deepEqual(buildMappingPlayersForPappSync(mapping, remainingCandidates), [
    {
      mappingRowId: "mapping-current",
      candidatePlayerId: "2",
      name: "Current",
      country: "current-oq",
    },
  ]);
});

test("honors deleted-row exclusions until a mapping is explicitly restored", () => {
  const mapping = { rows: [], excludedCheckinPlayerIds: ["1"] };
  const candidates = [
    { id: 1, displayName: "Removed mapping", account: "removed-oq" },
    { id: 2, displayName: "New candidate", account: "new-oq" },
  ];

  reconcileMappingRowsWithCandidates(mapping, candidates);

  assert.deepEqual(mapping.rows.map((row) => row.checkinPlayerId), ["2"]);
  assert.deepEqual(mapping.excludedCheckinPlayerIds, ["1"]);
});

test("persists hidden candidate links and exclusions through mapping sanitization", () => {
  const mapping = sanitizeMapping({
    rows: [
      {
        id: "deleted-visible-fields",
        registrationNick: "",
        oqAccount: "",
        wechatNickSource: "manual",
        checkinPlayerId: "7",
      },
    ],
    excludedCheckinPlayerIds: [7, "7", "8"],
  });

  assert.deepEqual(
    mapping.rows.map((row) => ({
      id: row.id,
      registrationNick: row.registrationNick,
      wechatNickSource: row.wechatNickSource,
      checkinPlayerId: row.checkinPlayerId,
    })),
    [{ id: "deleted-visible-fields", registrationNick: "", wechatNickSource: "manual", checkinPlayerId: "7" }],
  );
  assert.deepEqual(mapping.excludedCheckinPlayerIds, ["7", "8"]);
});

test("parses OQ handles from group nicks and uniquely matches normalized names", () => {
  assert.equal(accountTokenFromGroupNick("王万里 / WangWanli", "王万里"), "WangWanli");
  assert.equal(accountTokenFromGroupNick("Wang Xiaoming__xiaoming", "Wang Xiaoming"), "xiaoming");
  assert.equal(
    accountTokenFromGroupNick("Wang Xiaoming cool-account", "Xiaoming", "Wang Xiaoming"),
    "cool-account",
  );

  const result = matchGroupNicksToRosterPlayers(
    ["王万里 / WangWanli", "Wang Xiaoming__xiaoming", "someone else"],
    [
      { id: 1, displayName: "王万里", account: "wangwanli" },
      { id: 2, displayName: "Wang-Xiaoming", account: "xiaoming" },
    ],
  );

  assert.deepEqual(
    result.matches.map(({ wechatNick, candidatePlayerId, account }) => ({
      wechatNick,
      candidatePlayerId,
      account,
    })),
    [
      { wechatNick: "王万里 / WangWanli", candidatePlayerId: "1", account: "wangwanli" },
      { wechatNick: "Wang Xiaoming__xiaoming", candidatePlayerId: "2", account: "xiaoming" },
    ],
  );
  assert.deepEqual(result.unmatched, ["someone else"]);
});

test("uses a matching nickname account to resolve duplicate names and flags conflicts", () => {
  const disambiguated = matchGroupNicksToRosterPlayers(
    ["Li Hua lihua2"],
    [
      { id: 1, displayName: "Li Hua", account: "lihua1" },
      { id: 2, displayName: "Li Hua", account: "lihua2" },
    ],
  );
  assert.equal(disambiguated.matches.length, 1);
  assert.equal(disambiguated.matches[0].candidatePlayerId, "2");

  const ambiguous = matchGroupNicksToRosterPlayers(
    ["Li Hua unknownhandle"],
    [
      { id: 1, displayName: "Li Hua", account: "lihua1" },
      { id: 2, displayName: "Li Hua", account: "lihua2" },
    ],
  );
  assert.equal(ambiguous.ambiguous.length, 1);
  assert.equal(ambiguous.matches.length, 0);

  const conflict = matchGroupNicksToRosterPlayers(
    ["张三 wronghandle"],
    [{ id: 3, displayName: "张三", account: "zhangsan" }],
  );
  assert.equal(conflict.accountConflicts.length, 1);
  assert.equal(conflict.matches.length, 0);
});

test("keeps group-nick matching one-to-one when a player has multiple matching nicks", () => {
  const result = matchGroupNicksToRosterPlayers(
    ["张三 zhangsan", "张三 zhangsan2"],
    [{ id: 3, displayName: "张三", account: "" }],
  );

  assert.equal(result.matches.length, 0);
  assert.equal(result.ambiguous.length, 2);
});

test("attaches an unambiguous group nick to its check-in row and removes only the empty nick row", () => {
  const mapping = {
    groupNicks: ["王万里 wangwanli"],
    excludedCheckinPlayerIds: [],
    rows: [
      {
        id: "checkin-1",
        wechatNick: "",
        wechatNickSource: "",
        registrationNick: "王万里",
        oqAccount: "",
        oqCheck: sanitizeMappingCheck(null),
        checkinPlayerId: "1",
      },
      {
        id: "nick-only",
        wechatNick: "王万里 wangwanli",
        wechatNickSource: "",
        registrationNick: "",
        oqAccount: "",
        oqCheck: sanitizeMappingCheck(null),
        checkinPlayerId: "",
      },
    ],
  };

  const result = reconcileGroupNicksWithCandidates(mapping, [
    { id: 1, displayName: "王万里", account: "" },
  ]);

  assert.equal(result.autoMatchedCount, 1);
  assert.deepEqual(
    mapping.rows.map(({ id, wechatNick, oqAccount, wechatNickSource }) => ({
      id,
      wechatNick,
      oqAccount,
      wechatNickSource,
    })),
    [
      {
        id: "checkin-1",
        wechatNick: "王万里 wangwanli",
        oqAccount: "wangwanli",
        wechatNickSource: "auto",
      },
    ],
  );
});

test("unregistered group nicks stay in the nickname pool without creating table rows", () => {
  const mapping = {
    groupNicks: ["王万里 wangwanli", "未报名群成员"],
    excludedCheckinPlayerIds: [],
    rows: [
      {
        id: "checkin-1",
        wechatNick: "",
        wechatNickSource: "",
        registrationNick: "王万里",
        oqAccount: "",
        oqCheck: sanitizeMappingCheck(null),
        checkinPlayerId: "1",
      },
    ],
  };

  const result = reconcileGroupNicksWithCandidates(mapping, [
    { id: 1, displayName: "王万里", account: "" },
  ]);

  assert.equal(result.autoMatchedCount, 1);
  assert.equal(result.unmatchedCount, 1);
  assert.equal(mapping.rows.length, 1);
  assert.equal(mapping.rows[0].wechatNick, "王万里 wangwanli");
});

test("does not overwrite manual group nick assignments or accounts", () => {
  const mapping = {
    groupNicks: ["张三 zhangsan"],
    excludedCheckinPlayerIds: [],
    rows: [
      {
        id: "checkin-1",
        wechatNick: "手工保留昵称",
        wechatNickSource: "manual",
        registrationNick: "张三",
        oqAccount: "manual-account",
        oqCheck: sanitizeMappingCheck(null),
        checkinPlayerId: "1",
      },
      {
        id: "nick-only",
        wechatNick: "张三 zhangsan",
        wechatNickSource: "",
        registrationNick: "",
        oqAccount: "",
        oqCheck: sanitizeMappingCheck(null),
        checkinPlayerId: "",
      },
    ],
  };

  const result = reconcileGroupNicksWithCandidates(mapping, [
    { id: 1, displayName: "张三", account: "zhangsan" },
  ]);

  assert.equal(result.autoMatchedCount, 0);
  assert.deepEqual(
    mapping.rows.map(({ id, wechatNick, oqAccount }) => ({ id, wechatNick, oqAccount })),
    [
      { id: "checkin-1", wechatNick: "手工保留昵称", oqAccount: "manual-account" },
      { id: "nick-only", wechatNick: "张三 zhangsan", oqAccount: "" },
    ],
  );
});

test("does not send ambiguous legacy name matches or duplicate mappings to PAPP", () => {
  const candidates = [
    { id: 1, displayName: "Same", account: "oq-one", platform: "oq" },
    { id: 2, displayName: "Same", account: "oq-two", platform: "oq" },
  ];
  const mapping = {
    rows: [
      { id: "legacy-same", registrationNick: "Same", oqAccount: "", checkinPlayerId: "" },
      { id: "same-one", registrationNick: "Same", oqAccount: "oq-one", checkinPlayerId: "1" },
      { id: "same-one-duplicate", registrationNick: "Same", oqAccount: "oq-one", checkinPlayerId: "1" },
      { id: "same-two", registrationNick: "Same", oqAccount: "oq-two", checkinPlayerId: "2" },
    ],
    excludedCheckinPlayerIds: [],
  };

  assert.deepEqual(buildMappingPlayersForPappSync(mapping, candidates), [
    {
      mappingRowId: "same-two",
      candidatePlayerId: "2",
      name: "Same",
      country: "oq-two",
    },
  ]);
});
