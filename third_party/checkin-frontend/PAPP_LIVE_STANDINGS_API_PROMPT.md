# 原生 PAPP C 分轮实时排名接口核验与构建 Prompt

请核验并交付比赛签到助手使用的原生 PAPP C 分轮排名接口。前端已接入调用端：它通过本项目本地服务现有的 `POST /api/papp/tournament` 路由，以 `--tournament-json` 调用 `bin/Windows/papp_GB.exe`。本仓库的 `src/tournament_json.c` 已有 `round-standings` 实现和 dispatcher 分支；相关原生集成测试位于 `third_party/checkin-frontend/tournament-c.integration.test.cjs`。但当前 `bin/Windows/papp_GB.exe` 对 `round-standings` 返回 `unknown-operation`，对 `write-score-batch` 也返回 `unknown-operation`，因此现有源码能力尚未体现在正在运行的 Windows 执行文件中。

请先审阅现有 C 实现，按下方契约修正任何缺口，再把当前源码编译成 Windows `.exe` 并用该执行文件完成回归和冒烟验证。不能仅回复“源码已有实现”，也不能只做静态检查。接口总约定见 `third_party/checkin-frontend/PAPP_TOURNAMENT_ADAPTER.md`。

不要重写、替换或调整 PAPP 原生预赛配对核心算法。排名、积分、Brightwell、总棋子数、最终排名及淘汰赛胜负仍由 PAPP C 计算。不要增加 JavaScript 排名算法、其他运行时依赖或 Agent 工作流内容。

## 接口契约

确保 `--tournament-json` dispatcher 支持 `operation: "round-standings"`。请求继续使用现有 tournament JSON 输入结构，至少包含：

```json
{
  "operation": "round-standings",
  "round": 2,
  "preliminaryRoundCount": 4,
  "pappWorkfileId": "papp-example",
  "players": [],
  "rounds": [],
  "hasSemifinalAndFinal": true,
  "tournamentParameters": {
    "brightwellConstant": 6
  }
}
```

前端会传入本赛事已签到选手、比赛 workfile ID 和完整的已保存预赛历史。`round` 是要查看的目标预赛轮次，范围为 `1..preliminaryRoundCount`。排名必须只计入第 1 轮到目标轮次（含目标轮）的 PAPP C 历史，后续轮次不得影响该快照。分数必须依照现有 workfile/比分读回语义确认；页面输入值或 JS 缓存不能被视为已登记比分。

## 返回格式

成功且能核算时，返回单行 JSON：

```json
{
  "ok": true,
  "source": "papp-c",
  "operation": "round-standings",
  "stage": "preliminary",
  "round": 2,
  "throughRound": 2,
  "participantCount": 8,
  "progress": {
    "complete": true,
    "expectedRounds": 2,
    "roundsWithPairings": 2,
    "missingRounds": [],
    "unresolvedPairings": 0
  },
  "standings": [
    {
      "rank": 1,
      "preliminaryRank": 1,
      "playerId": "stable-player-id",
      "displayName": "选手姓名",
      "account": "oq-account",
      "pointsHalfUnits": 4,
      "displayPoints": 2,
      "totalPoints": 2,
      "brightwell": 120,
      "totalDiscs": 104
    }
  ]
}
```

`standings` 应包含所有当前参赛者，并按 PAPP C 的原生排名顺序返回。行字段与现有 `preliminary-standings` / `overall-standings` 一致，`rank` 和 `preliminaryRank` 都由 C 生成。不要让前端按数组顺序补名次。

若目标轮次尚未全部登记，可以只用 PAPP C 已确认的比分计算暂算排名，并返回 `progress.complete: false`、`missingRounds` 和/或 `unresolvedPairings`。如果缺失状态使 PAPP 原生排名不能安全计算，则返回 `ok: true`、相应 `progress`、空 `standings`，并给出可读的 `nextStage` 或 message；不要在 C 端之外推测缺失比分，也不要返回伪造排名。非法轮次、重复/无效选手和不一致 workfile 应沿用现有 JSON 错误格式明确失败。

## 验收与验证

1. 增加 C 集成回归：同一赛事分别查询第 1、2 轮，验证第 2 轮包含前两轮结果；查询第 1 轮不得被第 2 轮结果改变。
2. 覆盖缺配对、未确认比分、空 BYE、轮次越界、错误 workfile 等情况；未完成时只能返回 C 核算的暂算排名或空结果及明确进度。
3. 验证所有排名字段均由 PAPP C 原生计算，原生配对算法与既有 `pairings`、`preliminary-standings`、`overall-standings`、`stage-status` 和比分读回测试保持通过。
4. 用当前 C 源码构建 Windows `.exe`，避免覆盖或清理用户现有文件；若仓库默认构建脚本会执行 `clean`、删除对象文件或覆盖既有 `.exe`，请改为安全的隔离构建或输出到单独候选路径。
5. 用新构建的执行文件实际通过 `--tournament-json` 对 `round-standings` 做成功、部分未完成和非法轮次冒烟验证，并运行对应 C 集成测试及比分批次测试。当前 Node 全量测试的失败集中在 3 个 `round-standings` 用例和 1 个 workfile 批次用例，报错为执行文件返回 `unknown-operation`；请确认构建后的 `.exe` 消除这些失败，并报告构建命令、执行文件路径和测试结果。
6. 不改本项目的共享 `data/checkin-state.json`，不计算或核对任何文件哈希，不永久删除文件。
