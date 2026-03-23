# ClawCloud 部署

本文按 `2026-03-23` 的 ClawCloud 官方页面整理：

- Free 计划页面显示 `1 workspace / 1 seat / 1 NodePort / 4 Pods`
- App Launchpad 文档支持直接用公共 Docker 镜像部署，并在控制台填写端口、环境变量、启动命令和参数

对这个仓库来说，最稳的线上运行方式仍然是 `polling`，因为：

- 机器人主逻辑依赖常驻进程拉取 Telegram 更新
- 定时消息也依赖常驻 worker
- 用 ClawCloud 跑 `polling_worker.py` 或 `combined_service.py`，改动最小

## 这个仓库现在提供的部署入口

- `python web_server.py`
  用于独立的后台 Web 管理服务
- `python polling_worker.py`
  用于独立的常驻 polling worker
- `python combined_service.py`
  用于单容器同时运行 Web + polling

`polling_worker.py` 和 `combined_service.py` 都不会在退出时恢复旧 webhook，更适合长期运行在 ClawCloud / Render 这类容器平台。

如果你想直接照着填：

- 后台 Web 环境变量模板见 [.env.clawcloud.admin-web.example](/F:/脚本/纵横公开群机器人/群管机器人全功能完整版/.env.clawcloud.admin-web.example)
- Worker 环境变量模板见 [.env.clawcloud.bot-worker.example](/F:/脚本/纵横公开群机器人/群管机器人全功能完整版/.env.clawcloud.bot-worker.example)
- 上线核对清单见 [CLAWCLOUD_CHECKLIST.md](/F:/脚本/纵横公开群机器人/群管机器人全功能完整版/CLAWCLOUD_CHECKLIST.md)

## 两账号免费版推荐方案

你现在准备用两个 ClawCloud 账号部署，这个项目推荐这样拆：

- 账号 A：`admin-web`
  开公网，提供 `/web` 后台页面和后台接口
- 账号 B：`bot-worker`
  不开公网，常驻运行 Telegram polling 和定时任务

这样拆的好处：

- 后台页面和 worker 相互独立，重启互不影响
- 免费版的公网入口限制不会卡住两个服务
- worker 不需要暴露公网端口

## 必备前提

部署前先准备好：

1. `BOT_TOKEN`
2. `ADMIN_USER_ID`
3. 一套共享的 Upstash Redis REST KV
4. 一个足够长的 `WEB_SESSION_SECRET`
5. 一个可拉取的 Docker 镜像

线上不要依赖本地 `.local_kv.json`。`admin-web` 和 `bot-worker` 必须共用同一组：

- `KV_REST_API_URL`
- `KV_REST_API_TOKEN`

否则后台保存的群配置不会同步到 worker。

## 镜像来源

### 方案 A：直接用现成镜像

如果你已经有可用镜像，可以直接填：

```text
liuliul/tg-group-admin:latest
```

### 方案 B：用你自己的 Docker Hub 镜像

仓库里已经有 GitHub Actions：

- 文件：[docker-publish.yml](/F:/脚本/纵横公开群机器人/群管机器人全功能完整版/.github/workflows/docker-publish.yml)
- 触发条件：push 到 `main`，或手动触发

需要在 GitHub 仓库里配置：

```text
DOCKERHUB_USERNAME=你的DockerHub用户名
DOCKERHUB_TOKEN=你的DockerHubAccessToken
```

然后执行：

```bash
git add .
git commit -m "Prepare ClawCloud deployment"
git push origin main
```

等待 GitHub Actions 推送镜像完成，再去 ClawCloud 部署。

## 账号 A：部署 admin-web

在账号 A 的 App Launchpad 新建应用，建议这样填：

- Image Type: `Public`
- Image Name: `你的镜像名:tag`
- Usage Type: `Fixed`
- Replicas: `1`
- Public Access: `On`
- Container Port: `8000`
- Command: `python`
- Arguments: `web_server.py`

环境变量：

```env
BOT_TOKEN=你的TelegramBotToken
ADMIN_USER_ID=你的Telegram用户ID
KV_REST_API_URL=你的UpstashRedisREST地址
KV_REST_API_TOKEN=你的UpstashRedisREST令牌
WEB_SESSION_SECRET=一段随机长字符串
WEB_COOKIE_SECURE=1
```

部署完成后，先确认这两个地址可用：

```text
https://你的admin-web域名/web
https://你的admin-web域名/healthz
```

然后记下后台地址：

```text
https://你的admin-web域名/web
```

## 账号 B：部署 bot-worker

在账号 B 的 App Launchpad 再新建一个应用：

- Image Type: `Public`
- Image Name: `你的镜像名:tag`
- Usage Type: `Fixed`
- Replicas: `1`
- Public Access: `Off`
- Command: `python`
- Arguments: `polling_worker.py`

环境变量：

```env
BOT_TOKEN=你的TelegramBotToken
ADMIN_USER_ID=你的Telegram用户ID
KV_REST_API_URL=你的UpstashRedisREST地址
KV_REST_API_TOKEN=你的UpstashRedisREST令牌
WEB_APP_URL=https://你的admin-web域名/web
```

注意：

- `bot-worker` 必须保持单副本，不能多开
- 不要给 `bot-worker` 配 `WEBHOOK_SECRET`
- 如果你之前在 Vercel 或别的平台配过 webhook，`polling_worker.py` 启动时会自动把它关掉

## 上线顺序

建议按这个顺序走：

1. 先部署账号 A 的 `admin-web`
2. 打开 `/healthz` 和 `/web`，确认后台能访问
3. 再部署账号 B 的 `bot-worker`
4. 私聊机器人发送 `/start`
5. 通过后台给目标群配置验证规则
6. 把机器人加进测试群，确认它有禁言、删消息等需要的管理员权限
7. 用测试号进群，验证入群验证、欢迎语、定时消息是否正常

## 单账号备用方案

如果你后面发现两账号维护太麻烦，也可以退回单账号单应用：

- Command: `python`
- Arguments: `combined_service.py`
- Public Access: `On`
- Port: `8000`
- Replicas: `1`

环境变量：

```env
BOT_TOKEN=你的TelegramBotToken
ADMIN_USER_ID=你的Telegram用户ID
KV_REST_API_URL=你的UpstashRedisREST地址
KV_REST_API_TOKEN=你的UpstashRedisREST令牌
WEB_SESSION_SECRET=一段随机长字符串
WEB_COOKIE_SECURE=1
WEB_APP_URL=https://你的公网域名/web
```

这个方案最省事，但 Web 和 worker 会绑在一个容器里。

## 更新发布流程

推荐流程：

1. 本地改代码
2. `git add .`
3. `git commit -m "..."` 
4. `git push origin main`
5. 等 GitHub Actions 推送新镜像完成
6. 回到 ClawCloud，对 `admin-web` 和 `bot-worker` 分别点一次 `Update` 或 `Redeploy`

截至 `2026-03-23`，我没有在官方文档里看到“GitHub push 后自动重部署 App Launchpad 应用”的明确标准入口，所以先按“镜像自动更新，应用手动重部署”来操作更稳。

## 常见问题

### 为什么不用 webhook？

当前仓库有定时消息 worker，直接跑 `polling_worker.py` 或 `combined_service.py` 的改动最小，也最贴合 ClawCloud 这种长期运行容器平台。

### 为什么后台改了配置，worker 没反应？

最常见原因是 `admin-web` 和 `bot-worker` 没有共用同一组 `KV_REST_API_URL` / `KV_REST_API_TOKEN`。

### 为什么 bot-worker 不能多开？

因为它会同时拉取 Telegram 更新，还会跑定时任务。多副本会导致重复消费更新、重复执行定时消息和维护任务。
