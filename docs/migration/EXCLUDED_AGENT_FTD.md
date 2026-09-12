# Agent / FTD 排除清单

本次严格不迁移 Agent/FTD 编排工作流。用户另行授权后，独立的 Egaroucid 分析/审计链和 toolkit 确定性结果处理链已迁入 PAPP；其中的 Agent/FTD 现场编排脚本仍继续排除。

## 微信工具源目录排除项

原迁移基线统计：源目录文件总数 104；当时已复制 72；当时排除 32。本次授权新增的两个 standalone Egaroucid runner 不计入该历史基线。

| 源相对路径 | 字节数 | SHA-256 | 原因 |
| --- | ---: | --- | --- |
| `__pycache__\agent_checkin_bridge.cpython-311.pyc` | 35529 | `5dce42246a54f05e0920291c60a98ab15d8ce0db919183043ab6e62e0492524e` | 缓存/编译产物，不迁移 |
| `__pycache__\agent_checkin_bridge.cpython-314.pyc` | 35994 | `fd2e7f3c9744dc180b397dd5b17c495d88a23821e741e5c8797f56ac37583dcd` | 缓存/编译产物，不迁移 |
| `__pycache__\agent_egaroucid_analysis.cpython-311.pyc` | 116113 | `9b5ffe9e1d4cacedf3587a52157132539d4e3112cbd9d336166a7cdd534f767c` | 缓存/编译产物，不迁移 |
| `__pycache__\agent_egaroucid_analysis.cpython-314.pyc` | 113493 | `0d88be3bc15ede0c8e190beeb0a0eabc9840482bee7624f167b8c86b56c57898` | 缓存/编译产物，不迁移 |
| `__pycache__\agent_match_image_helper.cpython-311.pyc` | 479691 | `3fcb7aac823ce6be28847375ec345b47e991e575854b7b6ef60a4617b9b1e7fa` | 缓存/编译产物，不迁移 |
| `__pycache__\agent_match_image_helper.cpython-314.pyc` | 479038 | `016e4d048b0f23f94e0438283320e208a7634f7450474f4a1900b1a4a8e03dbd` | 缓存/编译产物，不迁移 |
| `__pycache__\config.cpython-311.pyc` | 19502 | `a8eca46781c85139c07b1a96bf64eef5c3b6230c4156f56c66c4214f8cba5d1b` | 缓存/编译产物，不迁移 |
| `__pycache__\decode_image.cpython-311.pyc` | 31468 | `1bf1a3ba4ee2a9a0423937fd66d8a140d15761e0ac747185eccbd7f9b81cb6da` | 缓存/编译产物，不迁移 |
| `__pycache__\key_utils.cpython-311.pyc` | 2289 | `65be2af9b7b8759717b3ccb091ae59a392f8b05932bc2f3c5e6f64559757bad5` | 缓存/编译产物，不迁移 |
| `__pycache__\local_state_commands.cpython-311.pyc` | 12741 | `46ec72df63d15f5ce2eb1d4059d0c6dce23aa6f8b19dfc277a15b7090aafe38a` | 缓存/编译产物，不迁移 |
| `__pycache__\mcp_server.cpython-311.pyc` | 195850 | `809e32a07f1ea2a160d8fd49eb79769ca4110f361094e322e139a378c7a82218` | 缓存/编译产物，不迁移 |
| `__pycache__\mcp_server.cpython-314.pyc` | 184827 | `297ccac8623707d8accdac5f97cee083949804fbcb2cdf6f9fa02a1c4a87b703` | 缓存/编译产物，不迁移 |
| `agent_apply_checkin_window.js` | 8606 | `fe6b5eaaf25397746ee0a5302b9345267de23a0cd2b99f10d86849dd04b17cbe` | Agent 工作流脚本或说明，按边界排除 |
| `agent_checkin_bridge.cmd` | 461 | `f36239c3e15c7e6433dfc98f1e44587b468cb66f55c7f9ca3bb50717516402d6` | Agent 工作流脚本或说明，按边界排除 |
| `AGENT_CHECKIN_BRIDGE.md` | 5783 | `cf1c4809859dde72a1c4595fb4d5249c29d857c6aab41db244c37724d92e1253` | Agent 工作流脚本或说明，按边界排除 |
| `agent_checkin_bridge.py` | 25543 | `274eb946a87f201adc47f2d39427f22589272bb2086e71f2e82f1442bf931a34` | Agent 工作流脚本或说明，按边界排除 |
| `agent_match_image_helper.cmd` | 480 | `647c4d3a01278fcfe65fe8485acc20b259e301c9138b52c840d74fd2f3d15537` | Agent 工作流脚本或说明，按边界排除 |
| `AGENT_MATCH_IMAGE_HELPER.md` | 25764 | `9d8b075d3cc153f8a95646888fa3153d802ac3b80fc8530a03c70bbbede179c8` | Agent 工作流脚本或说明，按边界排除 |
| `agent_match_image_helper.py` | 358960 | `5ad7d00aef95a42566d6955fdd0a920404f54abfacde6c2736935bdc6b0acd4f` | Agent 工作流脚本或说明，按边界排除 |
| `agent_roster_matcher.js` | 5969 | `23d04651da4ead0264b79497807918e48d6934a14e6394874996992dfafb673a` | Agent 工作流脚本或说明，按边界排除 |
| `agent_tournament_helper.cmd` | 477 | `dcf5220db599bcd1eeee7481375d9354cf39efe6d087b32b03b8a99bec3d7094` | Agent 工作流脚本或说明，按边界排除 |
| `agent_tournament_helper.py` | 10983 | `3c939c2ae6c1c44a0f8611627d7dd7e278bacb2be389d83be29a13e5c4e38485` | Agent 工作流脚本或说明，按边界排除 |
| `ftd_player_name_overrides.example.json` | 40 | `3f7173b722e6960659222fc8636f9cebd93a4e27f5d37170b8501a36d5ad00b4` | FTD/Agent 状态、解析或映射文件，按边界排除 |
| `local_state_commands.py` | 7751 | `ef741da87fe598448d5a7874c1a2cca271f1b2f889bf61bae8f70637f3d6b521` | FTD/Agent 状态、解析或映射文件，按边界排除 |
| `resolve_ftd_players.js` | 30391 | `0ea3a547eb61d650a586760aafdab4edcd0c373c457a3db1b0b596f615bcb2f7` | FTD/Agent 状态、解析或映射文件，按边界排除 |
| `tests\test_agent_match_ready_write.py` | 47823 | `67409fce553b43229d3c7866e159f5c28d91a4b461ffcf21cbb2aacb47eb4854` | Agent/FTD 集成测试，按边界排除 |
| `tests\test_agent_match_score_inference.py` | 1005 | `b1ccee386d93218f264b08afb94bb027938ac3fb6c9859d6387e27e51caedd30` | Agent/FTD 集成测试，按边界排除 |
| `tests\test_local_state_commands.py` | 2822 | `9fbda0caf6a3af42648b1f3847727a7cd2c4119ab598fe2769fa3f04ef824e27` | Agent/FTD 集成测试，按边界排除 |
| `tests\test_oq_auto_score_update.py` | 38492 | `0557bfb1ec4738ce19584170bdc63734733f34529004c75abd4ec7444ec4f277` | Agent/FTD 集成测试，按边界排除 |
| `tests\test_resolve_ftd_players.js` | 5596 | `df5b038273724a76fe5c5885207165e25cd1cae1337fa756b4797f7e404bcb4a` | Agent/FTD 集成测试，按边界排除 |

## `player_analysis_toolkit` 中仍排除的内容

用户已于 2026-09-11 授权复制原计划中的 toolkit 白名单及独立 Egaroucid 分析链。以下明确列出的 Agent/FTD 编排内容仍不复制：

| 源相对路径 | 原因 |
| --- | --- |
| `scripts\review\materialize_reference_marks.py` | Agent 标注/Reference 标记物化脚本，不直接迁移 |
| 所有 `__pycache__\`、未列入白名单的 toolkit 脚本/数据 | 缓存或超出本次精选迁移范围 |

toolkit 已复制的确定性代码、模型、特征和最终 Reference 资产见 [MIGRATION_INVENTORY.md](MIGRATION_INVENTORY.md)。

## 本次授权迁入的 Egaroucid / 结果处理链

以下脚本属于独立的分析、复核或结果处理工具，不接入 PAPP 的 C 编排核心：

| 目标路径 | 主要用途 |
| --- | --- |
| `third_party\wechat-decrypt\agent_egaroucid_analysis.py/.cmd` | 使用本地 Egaroucid Console 分析 transcript 或 account bundle，生成可审计的逐手结果 |
| `third_party\player-analysis-toolkit\scripts\data\run_egaroucid_bundle.py` | 为 bundle 分析建立隔离输出目录、调用分析器并保留运行审计 |
| `third_party\player-analysis-toolkit\scripts\analysis\run_player_investigation.py` | 串联选手调查、Level22 分析、off-book/Sentinel/Elo 结果处理 |
| `third_party\player-analysis-toolkit\research\tcn_loss_model\scripts\pipeline\safe_recompute_egaroucid_hints.py` | 对冻结输入执行安全 hint1/hint6 重算、审计、压力测试和结果比较 |
| `third_party\player-analysis-toolkit\scripts\data\run_safe_hint_stage.py` | 对安全 hint stage 提供统一命令包装和审计门禁 |
| `third_party\player-analysis-toolkit\research\tcn_loss_model\scripts\data\materialize_personal_oq_tcn_model_ready.py` | 旧版个人模型路径的 Egaroucid hint 特征物化；已限制其总引擎 worker 数 |
| `third_party\player-analysis-toolkit\src\player_analysis_toolkit\egaroucid_worker_budget.py` | 所有上述启动入口共享的内存预算和跨进程 slot 锁 |

Egaroucid 入口按场景分开校验：比赛运行最多 2 个 worker；后台选手调查按“单 worker 1400 MiB、物理内存 50% 向下取整”计算上限。独立进程之间还通过 `data\egaroucid-worker-slots\` 的 OS 锁共享同一物理内存池，超限会直接失败，不会静默降级。`analyze-bundle` 与 `analyze-transcript` 可脱离现场 Agent 运行；`once/status/watch` 若需现场 Agent/FTD helper，仍因本边界而不在 PAPP 中提供。

## 运行时边界

- PAPP 的比赛编排主流程没有接入 Agent、MCP 或 FTD 运行时；上述 Egaroucid/调查链是独立可选工具。
- `mcp_server.py` 仅作为独立微信工具源码保留，不能据此推断 PAPP 主流程依赖 MCP。
- 前端按原计划完整保留，因此其原有 UI 文案中可能出现“agent 识别”等历史提示词；这不是迁移 Agent 脚本，也没有迁移其对应执行器。
- 未迁移个人微信数据库、密钥、缓存、聊天导出、调查结果、历史真实选手样本和约 33GB 研究数据；也未迁移原始 Level22 数据、Anscombe 中间 shards/base 缓存或校准中间分片。
