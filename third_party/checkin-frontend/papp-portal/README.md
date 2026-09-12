# PAPP 入口边界

本目录只负责 PAPP 的工作区入口和独立的“选手调查”前端。

- `index.html`：首页工作区选择。
- `player-investigation/`：选手调查的独立前端边界，包含往期比赛调查入口和指定 ID 的 5 分钟 Player 画像确认。
- “比赛编排”按钮通过相对路径进入当前 PAPP 本地服务的根页面，不再依赖已停止的 `4174` 旧入口，也不复制、不修改 `tournament_arrangement/recovered`。

父目录 `third_party/checkin-frontend/` 根页面仍保留原有签到前端；PAPP 启动器现在打开本目录的入口页。
