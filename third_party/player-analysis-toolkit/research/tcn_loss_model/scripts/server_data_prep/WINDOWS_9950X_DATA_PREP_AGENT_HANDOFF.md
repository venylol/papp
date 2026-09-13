# Windows 9950X 服务器：hint6 续算到 TCN model-ready 数据交接

这是一个无 CUDA 的数据准备任务。服务器 Agent 应从 ZIP 内的已审计
hint6 部分进度继续，完成全量引擎结果、分阶段审计、精确合并和特征
装配。当数据已可直接供 TCN 模型使用时停止，不训练模型。

## 当前冻结状态

- 固定样本：10,000 局；609,124 原始行；599,112 个实着节点；10,012 个 pass。
- hint1：599,112/599,112 已完成，已通过 board/legality/key 全量审计。
- hint6：本机停止时已原子提交 14,080 行。部分审计为：
  `uniqueKeys=14080`、`boardMismatches=0`、`legalityOrCompletenessErrors=0`。
- 原始 hint6 证据在 `evidence\hint6_partial_original`，首次启动会生成路径
  重定位后的 `work\hint6`；原证据不修改。

## 启动

1. 解压 ZIP，保留目录结构。不要从 ZIP 内直接运行。
2. 确认 Python 能导入 `numpy pandas torch scipy sklearn`。若缺少，安装
   `requirements-data-prep.txt`中的数据准备依赖；可使用 CPU 版 PyTorch。
3. 只运行：

```powershell
.\run_windows_data_prep.cmd
```

如 Python 不在 PATH：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_windows_data_prep.ps1 `
  -PythonExe "C:\path\to\python.exe"
```

## 运行顺序

`package hash 校验 -> hint1 全审计 -> hint6 部分工作副本审计 -> hint6 --resume
-> hint6 599112 行全审计 -> (game_id, move_index) 精确合并 -> 362+23 特征
装配 -> checkpoint/preprocessing/model-ready 最终合同校验 -> 停止`

hint6 参数是用户锁定值：12 workers，每 worker 独占一个 Console，每 Console
16 threads，level18 + book，hash25。不得改参或自动调参。

## 成功标准与回传

仅当以下全部成立时才是完成：

- `DELIVERY_STATUS.json`: `status="complete"`, `stage="data-ready"`;
- `work\hint1\audit.json`: 599,112 行且 `ok=true`;
- `work\hint6\audit.json`: 599,112 行且 `ok=true`;
- `results\assembled\assembly_manifest.json`: 10,000 局/609,124 行/599,112 实着/
  10,012 pass，且 `status="complete"`;
- `results\model_ready\materialization_manifest.json`: 装配与内嵌 validation 均通过；
- `results\model_ready\server_final_validation.json`: `ok=true`, `inputFeatures=362`,
  `boardChannels=23`, `trainingStarted=false`;
- `model_ready_10000.npz` 和 `position_context_metadata.csv` 均存在且哈希已写入最终报告。

回传时保留整个解压目录，至少带回 `DELIVERY_STATUS.json`、`work`、
`results`下的全部产物。不要删除中间 JSONL 和 Console 原始响应，它们是 board
provenance 审计证据。

## 错误处理

任何 board 不一致、非法/缺失/重复 hint6、key 重复、文件哈希变化或数据形状
不同，都必须立即失败，不得填 0、猜测、跳过或改变样本。保留现场，读取
`DELIVERY_STATUS.json`、相应 progress/audit/manifest 和完整错误。如仅是 hint6
进程中断，确认无遗留同任务进程后重跑同一入口，它会跳过已提交 key。

严禁任何正式训练。包中没有训练阶段入口，最终验证只在 CPU 上读取 checkpoint
的特征/通道合同。
