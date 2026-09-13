# PAPP 离线编排与选手调查

- 项目作者：瑞瑞
- 开发说明：使用 Agent 完成
- 项目协议：GNU GPL 3.0，详见本目录的 `LICENSE` 与 `COPYING`
- 项目地址：<https://github.com/venylol/papp>

请先运行(papp-install-deps.cmd)安装必要依赖

之后按照这个流程运行papp-wechat-decrypt.cmd：

启动微信电脑版并登录。将 PAPP ZIP 解压到任意位置，并在文件资源管理器中打开解压后的 PAPP 文件夹。

找到 papp-wechat-decrypt.cmd，右键选择“以管理员身份运行”；Windows 11 如果没显示该选项，先点击“显示更多选项”。

在用户帐户控制窗口点击“是”。启动器会自动定位解密程序、安装依赖并开始本机解密；请保持命令窗口打开直到完成。

## 下载完整 Windows 离线包

请从 [Release](https://github.com/venylol/papp/releases/tag/v2026.09.13-offline) 下载 [PAPP-Offline-20260913-public.zip](https://github.com/venylol/papp/releases/download/v2026.09.13-offline/PAPP-Offline-20260913-public.zip)，完整解压后按上方说明运行。

Git 仓库中保留本次发布的程序文件；Python 运行库、参考数据和大型模型数据随完整 Release ZIP 提供。GitHub 自动生成的 Source code ZIP 不包含这些大文件，不能替代完整离线包。

本次公开版本已排除本机微信配置与密钥、解密聊天数据库、签到及比赛状态、调查结果和缓存。使用者需在本机重新配置微信并导入自己的比赛资料。原有 Git 提交历史保留。详细范围见 [RELEASE_CONTENTS.md](RELEASE_CONTENTS.md)。
