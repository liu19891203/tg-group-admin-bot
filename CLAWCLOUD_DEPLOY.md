# ClawCloud 部署

推荐用两个服务部署这个项目：

- `bot-worker`: 运行 Telegram 机器人和定时任务
- `admin-web`: 运行网页管理后台

这样可以直接复用当前仓库的 polling 方案，不需要先把定时任务重写成 webhook + cron。

## 前提

1. 准备 Telegram Bot Token
2. 准备外部 KV/Redis
3. 代码仓库已推到 Git 平台

线上不要依赖本地 `.local_kv.json`。部署前至少要配置：

- `BOT_TOKEN`
- `ADMIN_USER_ID`
- `KV_REST_API_URL`
- `KV_REST_API_TOKEN`

网页后台额外建议配置：

- `WEB_SESSION_SECRET`
- `WEB_COOKIE_SECURE=1`

## 服务一：bot-worker

用途：

- 接收真实 Telegram 更新
- 运行 `scheduled_message_worker`
- 处理欢迎语、验证、群消息逻辑

启动命令：

```bash
python local_polling.py
```

建议配置：

- Public Access: 关闭
- Replicas: `1`
- Autoscaling: 关闭

说明：

- `local_polling.py` 会直接从 Telegram 拉取更新，因此不要再同时给同一个 bot 长期开 webhook。
- 这个服务必须保持单副本，否则会重复消费更新，定时任务也可能重复触发。

## 服务二：admin-web

用途：

- 提供 `/web` 后台页面
- 提供 `/api/web/*` 后台接口

启动命令：

```bash
python web_server.py
```

建议配置：

- Public Access: 开启
- Port: `8000`
- Replicas: `1`

说明：

- `web_server.py` 会把 `api/web.py` 绑定到 `0.0.0.0:$PORT`
- 后台登录依赖 Telegram Login Widget，因此需要公网域名

## 环境变量建议

两个服务都配置：

```env
BOT_TOKEN=...
ADMIN_USER_ID=...
KV_REST_API_URL=...
KV_REST_API_TOKEN=...
```

`admin-web` 额外配置：

```env
WEB_SESSION_SECRET=replace-with-a-random-secret
WEB_COOKIE_SECURE=1
```

不建议在线上继续保留本地调试变量：

```env
WEB_LOCAL_DEBUG_LOGIN_ENABLED=1
WEB_LOCAL_DEBUG_LOGIN_SECRET=...
```

这组变量主要适合本机 `localhost` 调试，不适合公网后台。

## 部署后检查

1. 打开后台域名 `/web`
2. 用 Telegram 账号完成网页登录
3. 私聊 bot 发送 `/start`
4. 把 bot 拉进测试群，确认欢迎语和验证流程正常
5. 等待一条定时消息，确认 worker 正在运行

## 常见问题

### 为什么不用 webhook？

当前仓库的定时任务实现已经依赖常驻 worker。先用 `local_polling.py` 直接跑，改动最小，也更符合 ClawCloud 这种长期运行容器平台。

### 为什么拆成两个服务？

当前仓库现成的 HTTP 入口是分开的：

- Telegram webhook 入口在 `api/tg_bot.py`
- 网页后台入口在 `api/web.py`

而 polling 模式本身不需要暴露机器人 HTTP 端口，所以拆成 `bot-worker` + `admin-web` 最省事。
