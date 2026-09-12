# PAPP 依赖清单

本次为已迁移的独立微信工具和已授权的 `player_analysis_toolkit` 模型分析准备本地依赖。PAPP 主编排运行时不依赖 Agent 工作流或 MCP 调用。

## 清单文件

| 项目 | 路径/来源 | 字节数 | SHA-256 | 说明 |
| --- | --- | ---: | --- | --- |
| 源微信依赖清单 | `C:\Users\MeroAF\Desktop\比赛编排\othello-tournament-autopilot\wechat-decrypt\requirements.txt` | 273 | `2fe7ddc0bb5718e36565ea94335935b66038cb5aea48a60ee7fb7c372d647bad` | 上游原始清单 |
| 本地化依赖清单 | `vendor\python\requirements-wechat.txt` | 216 | `c9f6c8f1fa6a702ddf4b9a1fffb13ae2d8040c7528cb573027491ac1d5f93845` | UTF-8；加入了用途说明，保留运行/构建边界 |
| 模型分析依赖清单 | `vendor\python\requirements-model.txt` | 223 | `5eb3622b5b652f57e4933d9aae620c78cb848f6fe64452a60e08409878ca7d4b` | UTF-8；numpy/pandas/scipy/scikit-learn 与 CPU torch 的顶层约束 |

微信工具的直接依赖为 `pycryptodome`、`zstandard`、`mcp`、`pilk`；`pyinstaller` 仅作为后续打包依赖。模型分析的直接依赖为 `numpy`、`pandas`、`scipy`、`scikit-learn` 和 CPU 版 `torch`，并已下载其运行所需的传递依赖。wheelhouse 同时包含 MCP 和 PyInstaller 的传递依赖。

## 模型分析依赖

- 清单：`vendor\python\requirements-model.txt`。
- 目标平台：Windows x64、CPython 3.11；torch 使用 `2.14.0+cpu` wheel，不下载 CUDA 组件。
- 已解析顶层版本：`numpy 2.4.6`、`pandas 2.3.3`、`scipy 1.17.1`、`scikit-learn 1.9.1`、`torch 2.14.0+cpu`。
- 同批 wheelhouse 中还包含 pandas/scikit-learn 与 torch 的传递依赖；完整文件级 SHA-256 见下表和 [MIGRATION_INVENTORY.md](MIGRATION_INVENTORY.md)。

## Wheelhouse 平台

- 解析目标：Windows x64、CPython 3.11（`win_amd64`；`cp311`/`abi3`/纯 Python wheel）。
- 下载命令使用了 `--only-binary=:all:`；由于 `pilk==0.2.4` 没有匹配的 Windows wheel，另行保留其 PyPI source distribution。
- 不包含 `ffmpeg`、Whisper/whisper.cpp 模型或其他源仓库外部可选资源；这些仍需用户后续按需安装。

| 文件 | 类型 | 字节数 | SHA-256 |
| --- | --- | ---: | --- |
| `vendor\python\wheelhouse\altgraph-0.17.5-py2.py3-none-any.whl` | Windows/Python wheel | 21228 | `f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597` |
| `vendor\python\wheelhouse\annotated_types-0.8.0-py3-none-any.whl` | Windows/Python wheel | 13427 | `f072f4d804ea359e4eaf198b1af7a8b0943881a87f31bb764f8bf219bb9419e0` |
| `vendor\python\wheelhouse\anyio-4.15.1-py3-none-any.whl` | Windows/Python wheel | 132079 | `6152fdbbf9a77fdec97731721bebf7c4c44f7c29b424b0065826173efc7ed101` |
| `vendor\python\wheelhouse\attrs-26.1.0-py3-none-any.whl` | Windows/Python wheel | 67548 | `c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309` |
| `vendor\python\wheelhouse\certifi-2026.7.22-py3-none-any.whl` | Windows/Python wheel | 136983 | `62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775` |
| `vendor\python\wheelhouse\cffi-2.1.1-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 185096 | `42f6930c31dc7f50732c9ae793c2786c7b6b044195967bbdde40bb9be81c4cc0` |
| `vendor\python\wheelhouse\click-8.5.0-py3-none-any.whl` | Windows/Python wheel | 125251 | `255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360` |
| `vendor\python\wheelhouse\cryptography-50.0.1-cp311-abi3-win_amd64.whl` | Windows/Python wheel | 3842826 | `aed8db4f6d71c51efb89530e12d9464e7bf2923d46c3205dc794a2a93f8c0648` |
| `vendor\python\wheelhouse\h11-0.16.0-py3-none-any.whl` | Windows/Python wheel | 37515 | `63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86` |
| `vendor\python\wheelhouse\httpcore-1.0.9-py3-none-any.whl` | Windows/Python wheel | 78784 | `2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55` |
| `vendor\python\wheelhouse\httpx_sse-0.4.3-py3-none-any.whl` | Windows/Python wheel | 8960 | `0ac1c9fe3c0afad2e0ebb25a934a59f4c7823b60792691f779fad2c5568830fc` |
| `vendor\python\wheelhouse\httpx-0.28.1-py3-none-any.whl` | Windows/Python wheel | 73517 | `d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad` |
| `vendor\python\wheelhouse\idna-3.19-py3-none-any.whl` | Windows/Python wheel | 68550 | `815e7be7a7806d54abb586dc943addc79e8b2ee16915059658cbeff4b1b43bf4` |
| `vendor\python\wheelhouse\jsonschema_specifications-2025.9.1-py3-none-any.whl` | Windows/Python wheel | 18437 | `98802fee3a11ee76ecaca44429fda8a41bff98b00a0f2838151b113f210cc6fe` |
| `vendor\python\wheelhouse\jsonschema-4.26.0-py3-none-any.whl` | Windows/Python wheel | 90630 | `d489f15263b8d200f8387e64b4c3a75f06629559fb73deb8fdfb525f2dab50ce` |
| `vendor\python\wheelhouse\mcp-1.30.0-py3-none-any.whl` | Windows/Python wheel | 234581 | `666edb5009503e1047c9d60346a756f94b261f05cc2625f23d41c728ffc484d0` |
| `vendor\python\wheelhouse\packaging-26.3-py3-none-any.whl` | Windows/Python wheel | 129956 | `d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c` |
| `vendor\python\wheelhouse\pefile-2024.8.26-py3-none-any.whl` | Windows/Python wheel | 74766 | `76f8b485dcd3b1bb8166f1128d395fa3d87af26360c2358fb75b80019b957c6f` |
| `vendor\python\wheelhouse\pilk-0.2.4.tar.gz` | source distribution（pilk） | 226451 | `d4a1bcf93dc6ef5e95e0cfd728ed4ef4d49f9c0476d70816fecbe456cc762e7f` |
| `vendor\python\wheelhouse\pycparser-3.0-py3-none-any.whl` | Windows/Python wheel | 48172 | `b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992` |
| `vendor\python\wheelhouse\pycryptodome-3.23.0-cp37-abi3-win_amd64.whl` | Windows/Python wheel | 1799636 | `c75b52aacc6c0c260f204cbdd834f76edc9fb0d8e0da9fbf8352ef58202564e2` |
| `vendor\python\wheelhouse\pydantic_core-2.46.5-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 2041030 | `40375c2d05acec10323e45dfe2077ac44bc74659008614af5069034e2cfc781c` |
| `vendor\python\wheelhouse\pydantic_settings-2.15.0-py3-none-any.whl` | Windows/Python wheel | 69413 | `0ba092c291c94baceb5eff768aa0d56400a457585bc0175925a5a5510303da42` |
| `vendor\python\wheelhouse\pydantic-2.13.5-py3-none-any.whl` | Windows/Python wheel | 472589 | `346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73` |
| `vendor\python\wheelhouse\pyinstaller_hooks_contrib-2026.7-py3-none-any.whl` | Windows/Python wheel | 459445 | `24257a04c7a5a7a034cf28e39dcee20fbeeb9f043076729480f2e1b69904408a` |
| `vendor\python\wheelhouse\pyinstaller-6.22.2-py3-none-win_amd64.whl` | Windows/Python wheel | 1405725 | `9b990fa6bbe143572f06644a984ad0d7aa2e2ccc6929d4916031343a5888e9a7` |
| `vendor\python\wheelhouse\pyjwt-2.13.0-py3-none-any.whl` | Windows/Python wheel | 31274 | `66adcc2aff09b3f1bbd95fc1e1577df8ac8723c978552fd43304c8a290ac5728` |
| `vendor\python\wheelhouse\python_dotenv-1.2.3-py3-none-any.whl` | Windows/Python wheel | 22780 | `904552145e8bfed22162c09dab1c2b9b54fefa7b23ba780f4f26ca0316b0f0d9` |
| `vendor\python\wheelhouse\python_multipart-0.0.32-py3-none-any.whl` | Windows/Python wheel | 30042 | `ff6d3f776f16878c894e52e107296ffc890e913c611b1a4ec6c44e2821fe2e23` |
| `vendor\python\wheelhouse\pywin32_ctypes-0.2.3-py3-none-any.whl` | Windows/Python wheel | 30756 | `8a1513379d709975552d202d942d9837758905c8d01eb82b8bcc30918929e7b8` |
| `vendor\python\wheelhouse\pywin32-312-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 6928825 | `d11417d84412f859b722fad0841b3614459ed0047f7542d8362e77884f6b6e8a` |
| `vendor\python\wheelhouse\referencing-0.37.0-py3-none-any.whl` | Windows/Python wheel | 26766 | `381329a9f99628c9069361716891d34ad94af76e461dcb0335825aecc7692231` |
| `vendor\python\wheelhouse\rpds_py-2026.6.3-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 223219 | `2c54a076ca4d370980ab57bc0e31df57bbe8d41340436a90ef8b1219a3cbb127` |
| `vendor\python\wheelhouse\setuptools-84.0.0-py3-none-any.whl` | Windows/Python wheel | 818216 | `51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670` |
| `vendor\python\wheelhouse\sse_starlette-3.4.11-py3-none-any.whl` | Windows/Python wheel | 17122 | `c7b2244bdff016fe7f64e10075e89a3e6bbf899649cc89b0fe884b5545042453` |
| `vendor\python\wheelhouse\starlette-1.6.0-py3-none-any.whl` | Windows/Python wheel | 75969 | `a86dd39d14bb45f85a3d18525215a9ef0cfd1f192ac793220e72598c90335f0c` |
| `vendor\python\wheelhouse\typing_extensions-4.16.0-py3-none-any.whl` | Windows/Python wheel | 45571 | `481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8` |
| `vendor\python\wheelhouse\typing_inspection-0.4.4-py3-none-any.whl` | Windows/Python wheel | 14750 | `65b8397ba37ccbce054456aaccddfc91e6e3083c92824df348d96ca832f3f147` |
| `vendor\python\wheelhouse\uvicorn-0.52.4-py3-none-any.whl` | Windows/Python wheel | 79871 | `f86e41a149d7d05a9969337e3946a9c171c06a5d42680896daaba624aeac8da1` |
| `vendor\python\wheelhouse\zstandard-0.25.0-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 506183 | `daab68faadb847063d0c56f361a289c4f268706b598afbf9ad113cbe5c38b6b2` |

| `vendor\python\wheelhouse\cloudpickle-3.1.2-py3-none-any.whl` | Windows/Python wheel | 22228 | `9acb47f6afd73f60dc1df93bb801b472f05ff42fa6c84167d25cb206be1fbf4a` |
| `vendor\python\wheelhouse\filelock-3.32.6-py3-none-any.whl` | Windows/Python wheel | 100189 | `3f16ecd0117feae0dfc147e8c62eb5daeccd8bd800378c3ddf416de9b4feb6b1` |
| `vendor\python\wheelhouse\fsspec-2026.7.0-py3-none-any.whl` | Windows/Python wheel | 206583 | `b57ddbafedfaef7018c1ecab32aa200a9d7ca26b77965f64e48b70061249d279` |
| `vendor\python\wheelhouse\jinja2-3.1.6-py3-none-any.whl` | Windows/Python wheel | 134899 | `85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67` |
| `vendor\python\wheelhouse\joblib-1.6.0-py3-none-any.whl` | Windows/Python wheel | 306115 | `3dbbf9f6e4b592a2357b854608e980fe6390d131d7a82f011a377ef2ebef7aba` |
| `vendor\python\wheelhouse\markupsafe-3.0.3-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 15077 | `de8a88e63464af587c950061a5e6a67d3632e36df62b986892331d4620a35c01` |
| `vendor\python\wheelhouse\mpmath-1.3.0-py3-none-any.whl` | Windows/Python wheel | 536198 | `a0b2b9fe80bbcd81a6647ff13108738cfb482d481d826cc0e02f5b35e5c88d2c` |
| `vendor\python\wheelhouse\narwhals-2.26.0-py3-none-any.whl` | Windows/Python wheel | 474034 | `29326d74f107c347fd1009bd58e38d9f7c7c5b51e6de97bc93dbc325d9038b54` |
| `vendor\python\wheelhouse\networkx-3.6.1-py3-none-any.whl` | Windows/Python wheel | 2068504 | `d47fbf302e7d9cbbb9e2555a0d267983d2aa476bac30e90dfbe5669bd57f3762` |
| `vendor\python\wheelhouse\numpy-2.4.6-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 12608406 | `1e254a00cdf42b1e4d5b3d68d33af63268d41340d8885df2ab6470f2e1500147` |
| `vendor\python\wheelhouse\pandas-2.3.3-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 11348702 | `f086f6fe114e19d92014a1966f43a3e62285109afe874f067f5abbdcbb10e59c` |
| `vendor\python\wheelhouse\python_dateutil-2.9.0.post0-py2.py3-none-any.whl` | Windows/Python wheel | 229892 | `a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427` |
| `vendor\python\wheelhouse\pytz-2026.3.post1-py2.py3-none-any.whl` | Windows/Python wheel | 508283 | `dd95840dd199baea12d9cc096a1d452caa6596a1c1e4b5f3dbd1541855d5e815` |
| `vendor\python\wheelhouse\scikit_learn-1.9.1-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 8329877 | `220fa18152852a5ce29c49e1eaba9d44ec44631cd2e5cf65f5a40eafa5ab3412` |
| `vendor\python\wheelhouse\scipy-1.17.1-cp311-cp311-win_amd64.whl` | Windows/Python wheel | 36607512 | `d30e57c72013c2a4fe441c2fcb8e77b14e152ad48b5464858e07e2ad9fbfceff` |
| `vendor\python\wheelhouse\six-1.17.0-py2.py3-none-any.whl` | Windows/Python wheel | 11050 | `4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274` |
| `vendor\python\wheelhouse\sympy-1.14.0-py3-none-any.whl` | Windows/Python wheel | 6299353 | `e091cc3e99d2141a0ba2847328f5479b05d94a6635cb96148ccb3f34671bd8f5` |
| `vendor\python\wheelhouse\threadpoolctl-3.6.0-py3-none-any.whl` | Windows/Python wheel | 18638 | `43a0b8fd5a2928500110039e43a5eed8480b918967083ea48dc3ab9f13c4a7fb` |
| `vendor\python\wheelhouse\torch-2.14.0+cpu-cp311-cp311-win_amd64.whl` | Windows/Python CPU wheel | 123978438 | `8e2c47c6556c7d5a85848634372bb2252907d411e9cad669c99406856d536eb5` |
| `vendor\python\wheelhouse\tzdata-2026.3-py2.py3-none-any.whl` | Windows/Python wheel | 348168 | `dc096730c87af6cab1b171c9d532be840741ff5d459015e7f6947bd7d7e54931` |
合计：60 个本地包文件，224866085 字节；其中 wheel 59 个，source distribution 1 个。上面新增的 20 行覆盖模型顶层包及其 pandas/scikit-learn/torch 传递依赖。

## 离线安装提示

在目标 Python 版本与平台匹配时，可使用：

```powershell
python -m pip install --no-index --find-links vendor/python/wheelhouse -r vendor/python/requirements-wechat.txt
```

模型侧在 CPython 3.11 Windows x64 环境可使用：

```powershell
python -m pip install --no-index --find-links vendor/python/wheelhouse -r vendor/python/requirements-model.txt
```

`pilk-0.2.4.tar.gz` 不是预编译 Windows wheel，离线安装它可能需要本机 C 编译工具；本次不执行该构建。微信 `mcp_server.py` 的 SILK 转录路径还会按其源码说明尝试 `silk-python`/`pysilk`，该可选路径没有在本次计划外新增包。

未执行 PyInstaller，也未生成任何微信工具 `.exe`。
