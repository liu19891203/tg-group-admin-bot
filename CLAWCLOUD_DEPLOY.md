# ClawCloud 部署

推荐继续用两个服务部署这个项目：

- `bot-worker`：运行 Telegram 机器人和定时任务
- `admin-web`：运行网页管理后台

这套方案直接复用当前仓库的 `polling` 实现，不需要先把定时任务改写成 `webhook + cron`。

如果你使用的是 ClawCloud `Free` 计划，还要注意一个现实限制：免费额度通常只允许 `1 nodeport`。这意味着你往往不能同时跑一个公网 `admin-web` 和一个单独的 `bot-worker`。

所以免费版推荐优先使用单应用方案：

- `all-in-one`：同一个容器里同时运行 `web_server` 和 `local_polling`

仓库里已经提供了入口：

```bash
python combined_service.py
```

## 必备环境

部署前先准备：

1. `BOT_TOKEN`
2. `ADMIN_USER_ID`
3. `KV_REST_API_URL`
4. `KV_REST_API_TOKEN`
5. `WEB_SESSION_SECRET`

线上不要依赖本地 `.local_kv.json`。`admin-web` 和 `bot-worker` 必须使用同一组外部 KV 配置，否则后台保存的群配置不会被 worker 看到。

## 镜像来源

推荐在 ClawCloud 里直接使用 Docker Hub 镜像：

```text
liuliul/tg-group-admin:latest
```

这个仓库已经附带 GitHub Actions。每次向 `main` 分支 push 后，GitHub 会自动构建并推送以下标签：

- `latest`
- `main`
- `sha-<commit>`
- `v*` tag 对应的版本标签

## GitHub Actions 需要的仓库 Secrets

在 GitHub 仓库 `Settings -> Secrets and variables -> Actions` 里添加：

```text
DOCKERHUB_USERNAME=liuliul
DOCKERHUB_TOKEN=你的DockerHubAccessToken
```

`DOCKERHUB_TOKEN` 建议使用 Docker Hub 的 Access Token，不要直接用密码。

## 免费版单应用方案

用途：

- 提供 `/web` 后台页面
- 同时运行 Telegram polling 和定时任务

ClawCloud 配置建议：

- Image: `liuliul/tg-group-admin:<你的镜像tag>`
- Command: `python`
- Arguments: `combined_service.py`
- Public Access: 开启
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

说明：

- 这是免费版最稳的部署方式，只占一个应用和一个公网入口
- `WEB_APP_URL` 可以在第一次部署成功、拿到公网地址后，再回填并更新一次
- 如果你已经升级到 `Hobby` 或更高计划，再优先用下面的双服务方案

## 服务一：admin-web

用途：

- 提供 `/web` 后台页面
- 提供后台接口

ClawCloud 配置建议：

- Image: `liuliul/tg-group-admin:latest`
- Command: `python`
- Arguments: `web_server.py`
- Public Access: 开启
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
```

部署成功后，记录后台地址：

```text
https://你的admin-web域名/web
```

## 服务二：bot-worker

用途：

- 拉取 Telegram 更新
- 处理入群验证、欢迎语、群消息逻辑
- 运行 `scheduled_message_worker`

ClawCloud 配置建议：

- Image: `liuliul/tg-group-admin:latest`
- Command: `python`
- Arguments: `local_polling.py`
- Public Access: 关闭
- Replicas: `1`
- Autoscaling: 关闭

环境变量：

```env
BOT_TOKEN=你的TelegramBotToken
ADMIN_USER_ID=你的Telegram用户ID
KV_REST_API_URL=你的UpstashRedisREST地址
KV_REST_API_TOKEN=你的UpstashRedisREST令牌
WEB_APP_URL=https://你的admin-web域名/web
```

`bot-worker` 必须保持单副本，否则会重复拉取 Telegram 更新，定时任务也可能重复执行。

## 上线后检查

1. 打开 `https://你的admin-web域名/web`
2. 用 Telegram 登录后台
3. 私聊 bot 发送 `/start`
4. 在后台给目标群配置验证目标
5. 把 bot 拉进测试群并确认它有禁言权限
6. 用新账号进群，检查禁言、验证提示、验证放行是否正常

## 自动更新能做到什么

这套仓库现在已经支持：

- `git push origin main`
- GitHub Actions 自动构建镜像
- GitHub Actions 自动推送 Docker Hub

但截至目前 ClawCloud 官方文档没有明确提供“GitHub push 后自动重部署 App Launchpad 应用”的标准入口。也就是说：

- 镜像会自动更新
- ClawCloud 应用通常仍需要你手动点一次 `Update`、`Redeploy` 或重启应用，才能拉取新镜像

如果你在 ClawCloud 后台看到支持“重新拉取最新镜像”的更新按钮，优先使用那个按钮即可。

## 推荐发布流程

1. 本地改代码
2. `git add .`
3. `git commit -m "..."` 
4. `git push origin main`
5. 等 GitHub Actions 推送新镜像完成
6. 到 ClawCloud 对应用点一次更新或重启

## 常见问题

### 为什么不继续用 webhook？

当前仓库的定时任务依赖常驻 worker，直接跑 `local_polling.py` 改动最小，也更贴合 ClawCloud 这种长期运行容器平台。

### 为什么新成员进群后不触发验证？

最常见原因是 `admin-web` 和 `bot-worker` 没有共用同一组 `KV_REST_API_URL` / `KV_REST_API_TOKEN`。这种情况下后台配置不会同步到 worker，机器人就看不到验证目标。
