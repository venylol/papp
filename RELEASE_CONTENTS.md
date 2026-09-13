# 2026-09-13 公开离线版文件范围

本次版本以用户提供的 PAPP-Offline 工作目录为依据，保留原有 Git 历史，以新提交替换 main 分支中的文件。

## Git 仓库与完整离线包

- Git 仓库：程序、前端、本地 EXE、引擎及其资源、脚本、较小模型和依赖文件。
- [完整 Release ZIP](https://github.com/venylol/papp/releases/download/v2026.09.13-offline/PAPP-Offline-20260913-public.zip)：上述文件，加上 `runtime/python/`、`data/reference/` 和以下大型模型数据文件：
  `third_party/player-analysis-toolkit/research/tcn_loss_model/outputs/oq_tcn_model_ready_11200_oq_profile_wld_ply39_20260808/model_ready_11200_oq_profile_wld_ply39.npz`
- 将完整 ZIP 解压到新目录即可得到公开版本的完整目录结构。无需拼接文件，也无需 Git LFS。
- 发布过程未修改比赛算法或本地调用接口，沿用提供目录中的 Windows EXE。

## 不公开的本机资料

- `data/` 中除 `reference/` 外的比赛状态、工作文件、调查结果及工作缓存。
- `third_party/wechat-decrypt/config.json`、`all_keys.json`、`wxwork_keys.json`。
- 微信解密数据库、企业微信导出数据和 `agent_cache/`。
- Python 字节码、缓存和本机 `.env` 配置。

程序自带的参考数据、训练数据和模型资源予以保留。原始工作目录不作修改。
