# Windows 9950X：1200 局独立增量数据准备合同

## 目标与边界

本包只处理新拉取的 1200 局。必须先让原 10000 局任务完整结束，并先把其成品回传给本地；之后才能启动本包。

本包完成后，服务器只回传单独的 1200 局结果包。不要在服务器上把 10000 与 1200 合并，不要启动 TCN 训练，不要使用 CUDA。

本包生成的是官方 362 维基础特征和 23 个棋盘通道。不要在服务器上单独写入 31 维 OQ Player profile 特征：该分支的均值和标准差必须在 10000+1200 合并后的统一 train split 上拟合。分别归一化后再拼接会破坏训练合同。两批基础 NPZ 回到本地并合并为 11200 局后，才统一写入 Player profile 数组并再次做训练通路验收。

## 固定数据合同

- 1200 个唯一 `game_id`，与原 10000 局零重合。
- 72,940 条原始 OQ ply：71,954 个实际落子，986 个明确 pass。
- 合并键只能是精确 `(game_id, move_index)`。
- `move_index` 是 OQ 原始索引；`-` pass 明确占一个索引。
- `source_ply_including_pass = move_index + 1`，可能超过 60；`global_placement_ply` 排除 pass，必须不超过 60。
- 禁止按棋盘、相邻 ply 或压缩 pass 后的索引重配。

## 启动前检查

1. 找到原始 v3 包解压目录，即同时含有 `assets\engine`、`app\scripts`、`data\source_snapshot` 的目录。
2. 确认原 10000 局任务状态为 `complete`，并且其回传包已经交给用户。
3. 确认系统中没有 `Egaroucid_for_Console_7_8_1_AVX512_AMD.exe` 进程。
4. 解压本包到独立目录，不要覆盖 v3/v5 包或其结果。

可先做不启动 Console 的预检：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_extension_1200_data_prep.ps1 -ServerRoot 'D:\path\to\v3' -PreflightOnly
```

正式运行：

```powershell
.\run_extension_1200_data_prep.cmd D:\path\to\v3
```

## 固定计算配置

- hint1：level 2、no book、每 Console 1 thread、hash 25、12 workers。
- hint6：level 18 + book、12 个 Console、每 Console 16 threads、hash 25、batch 128、timeout 900、max attempts 2。
- 不得加入 `-q` 或 `-noboard`，不得更改 Console 协议。

脚本会依次执行：源数据审计 → 完整 hint1 → hint1 审计 → 完整 hint6 → hint6 审计 → 精确索引装配 → 362 特征/23 棋盘通道物化 → CPU 基础数据合同校验 → 单独的 1200 局回传 ZIP。

## 完成条件与回传

只有 `EXTENSION_1200_DELIVERY_STATUS.json` 同时满足以下条件才算完成：

- `status` 为 `complete`；
- `stage` 为 `extension-return-ready`；
- `trainingStarted` 为 `false`；
- `cudaUsed` 为 `false`。

回传以下两个文件：

- `returns\oq_bilateral_extension_1200_model_ready_20260804_v2.zip`
- 同目录下的 `.zip.sha256`

回传包内保留 hint1/hint6 原始批次与审计、精确索引装配数据、`model_ready_1200.npz`、上下文数据及全部清单。出现任何审计失败、行数不符、索引不连续或已有目录不完整时，停止并报告，不要猜测修复或删除旧结果。

`model_ready_1200.npz` 是 Profile 物化之前的基础 NPZ。服务器 Agent 不得把它宣称为最终的 profile-conditioned 训练输入；最终训练输入必须是本地统一合并和统一归一化后生成的 `model_ready_11200_oq_profile_retrospective.npz`。
