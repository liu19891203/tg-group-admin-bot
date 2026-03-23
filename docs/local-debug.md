# 本地联调说明

## 环境准备
1. 复制项目根目录下的 `.env.example` 为 `.env`
2. 至少配置以下变量：
   - `BOT_TOKEN`
   - `ADMIN_USER_ID`
3. `KV_REST_API_URL` 和 `KV_REST_API_TOKEN` 在本地可留空，程序会退回进程内内存存储

## 启动
```bash
python local_server.py
```

默认监听 `http://127.0.0.1:8000`。

## 说明
- 本地会自动读取项目根目录下的 `.env.local` 或 `.env`
- 启动时会直接校验 `BOT_TOKEN`
- 如果需要接 Telegram 真 webhook，可把 `/api/telegram` 转发到本地服务
