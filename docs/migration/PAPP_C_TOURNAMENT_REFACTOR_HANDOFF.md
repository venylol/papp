# PAPP C 比赛编排重构：实施交接

更新日期：2026-09-12

## 当前状态

重构主路径和收尾已完成。比赛结果通过本地服务调用 PAPP C 的结构化 JSON 接口；Windows EXE 已重新编译验证。C 原生预赛配对核心及其随机行为保持不变。前端不再包含活动的 JS 配对、积分排名或淘汰赛结果算法；OQ 回放与裁定也由 C 执行。

本次没有修改 data/checkin-state.json。用户确认，已有 source 为 papp-adapter 的计分轮次继续作为旧记录只读展示，不重算、不改来源、不转换、不清除。

## 运行接口

- POST /api/papp/tournament 将结构化请求交给编译产物 bin/Windows/papp_GB.exe --tournament-json。
- POST /api/papp/oq/poll 将 OQ 原始快照、轮次时间、配对与人工状态交给 C。
- GET /api/state 读取共享状态；用户和脚本状态写入继续经 POST /api/state。C 不直接写共享 JSON。
- C 操作包括 round-count、pairings、validate-pairings、validate-score、write-score-batch、read-score-batch、preliminary-standings、overall-standings、stage-status 和 oq-poll。C 调用失败时明确返回错误，不使用 JS fallback。

自动预赛轮数为 max(4, ceil(log2(n)))，n 是实际已签到人数；无人签到不能开始，手动轮数由用户控制并由 C 校验 1–128 上限。积分同时返回 pointsHalfUnits 与 displayPoints：胜局为 2 ticks / 1 显示分，和棋为 1 tick / 0.5 显示分。淘汰赛种子、平局晋级、名次赛和无淘汰赛流程都由 C 返回。

## 覆盖矩阵

| 重构前逻辑 | 接管位置 | 编译后 EXE / 回归证据 | 结果 |
|---|---|---|---|
| 自动/手动轮数、比分补算与校验 | C round-count、validate-score；app.js 只调用适配器 | 17 人自动 5 轮、最少 4 轮、手动值、128 上限和比分样例；C 集成套件 | 通过 |
| 预赛配对、Bye、重赛和颜色历史 | C pairings 调用原生 compute_pairings | 奇数参赛、40:24 Bye、第二轮避免重赛与颜色历史；C 集成套件 | 通过 |
| 预赛积分、Brightwell、排名、最终排名 | C preliminary-standings、overall-standings | half-point/display-point、Brightwell 0 与小数、无淘汰赛排名；C 集成套件 | 通过 |
| 半决赛、决赛、三四名赛、平局裁定和第 5 名以后 | C pairings、validate-pairings、overall-standings、stage-status | 四强种子、半决赛平局、决赛/名次赛、第五名以后顺序、错误种子和未完成阶段阻断；C 集成套件 | 通过 |
| 比分批次写入和持久化读回核对 | C write-score-batch / read-score-batch；适配器从 /api/state 读取存储值再交 C 核验 | 一致读回确认、不一致读回拒绝；C 集成与前端回归 | 通过 |
| OQ 棋谱计分和候选裁定 | C oq-poll；local-server.js 调用编译 EXE | 双方账号映射、时间窗、去重、多局候选详情、缺棋谱、raw_metadata_json、非法 Pass、终局、人工比分保护、用户 pending followup、裁判解决项；C 集成与前端回归 | 通过 |
| 本地 HTTP 结果路径 | local-server.js 的比赛和 OQ 路由 | 对本地服务发请求并验证响应 source 为 papp-c；C 集成套件 | 通过 |
| 候选名单、选手映射和旧历史 | 适配器纯映射；状态经 /api/state | 候选/映射回归、papp-adapter 旧轮次只读展示与 OQ 不覆盖；前端回归 | 通过 |
| 旧 JS 结果算法和独立 OQ JS 模块 | tournament-adapter.js 清退算法；OQ 运行时统一使用 C | 旧算法调用引用扫描无匹配；C 覆盖通过后，将 oq-score-logic.js 与 oq-score-logic.test.cjs 移入回收站 | 已清退 |

## 验证记录

- .\build-windows.cmd：成功生成 bin/Windows/papp_GB.exe。构建中只有生成词法/语法文件的既有编译告警，没有编译失败。
- 在 third_party/checkin-frontend 运行 node --test tournament-c.integration.test.cjs：12 项通过。
- 运行 node --test tournament-adapter.test.cjs score-helper-sanitize.test.cjs tournament-parameters.test.cjs mapping-sync.test.cjs local-server-candidates.test.cjs：60 项通过。
- node --check app.js、node --check tournament-adapter.js、node --check local-server.js：通过。
- 旧 OQ JS 模块与专属测试已移入 Windows 回收站；它们没有运行时调用者。收尾测试在其退出默认路径后运行，覆盖由 C/EXE 和保留的适配器测试承担。
- 没有运行浏览器自动化；依 AGENTS.md，浏览器页面测试由用户执行。
- 未计算文件哈希。

## 用户数据与运行边界

旧 source papp-adapter 轮次保持原样只读。新 C 配对使用 source papp-c。所有共享状态写入经 POST /api/state，并遵守 source 和脚本延迟补写规则。比赛运行不依赖 FTD、Agent 或 Agent 工作流。