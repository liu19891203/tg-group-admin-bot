# Telegram 群管机器人（入群验证 + 欢迎语）

## 功能
- 入群即刻验证：要求用户加入所有预设频道/群组，否则禁言
- 验证按钮：加入链接 + “已加入，点验证”
- 欢迎语：支持图片、按钮与变量 `{user}` `{group}` `{date}`
- 管理员私聊按钮菜单配置（目标、欢迎语、按钮）

## 部署前准备
1. 创建 Telegram Bot，拿到 `BOT_TOKEN`
2. 准备 Upstash/Vercel KV，拿到 `KV_REST_API_URL` 与 `KV_REST_API_TOKEN`
3. 将机器人加入目标群，并授予禁言/读取成员权限
4. 目标频道/群组需允许 bot `getChatMember`（通常需要 bot 为管理员）

## 环境变量
复制 `.env.example`，在 Vercel 中配置：
- `BOT_TOKEN`
- `WEBHOOK_SECRET`（可选，用于验证 Telegram 请求）
- `KV_REST_API_URL`
- `KV_REST_API_TOKEN`

## Webhook 设置
部署后，设置 webhook（示例）：
```
https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook?url=https://<your-vercel-domain>/api/telegram&secret_token=<WEBHOOK_SECRET>
```

## 管理员使用
1. 私聊机器人发送 `/start`
2. 绑定/选择群组
3. 配置验证目标（@username 或 t.me/xxx）
4. 配置欢迎语、图片、按钮

## 备注
- 若未配置验证目标，则不会禁言
- 回调按钮默认弹出提示，不会在群里刷屏
