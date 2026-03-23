# ClawCloud 双账号部署检查清单

适用场景：

- 账号 A 部署 `admin-web`
- 账号 B 部署 `bot-worker`

对应入口：

- `python web_server.py`
- `python polling_worker.py`

## 部署前

- 已准备 `BOT_TOKEN`
- 已准备 `ADMIN_USER_ID`
- 已准备共享的 `KV_REST_API_URL`
- 已准备共享的 `KV_REST_API_TOKEN`
- 已准备 `WEB_SESSION_SECRET`
- 已确认 Docker 镜像可拉取
- 已确认机器人已经是目标群管理员，且具备禁言/删消息等必要权限

## 镜像发布

- GitHub 仓库已配置 `DOCKERHUB_USERNAME`
- GitHub 仓库已配置 `DOCKERHUB_TOKEN`
- 已执行 `git push origin main`
- GitHub Actions 已成功推送最新镜像

## 账号 A: admin-web

- Image Type 选择 `Public`
- Image Name 填写正确 tag
- Usage Type 选择 `Fixed`
- Replicas 设置为 `1`
- Public Access 已开启
- Container Port 已设置为 `8000`
- Command 为 `python`
- Arguments 为 `web_server.py`
- 已填入 [.env.clawcloud.admin-web.example](/F:/脚本/纵横公开群机器人/群管机器人全功能完整版/.env.clawcloud.admin-web.example) 对应变量
- 部署后 `https://你的域名/healthz` 返回 200
- 部署后 `https://你的域名/web` 可以打开

## 账号 B: bot-worker

- Image Type 选择 `Public`
- Image Name 填写正确 tag
- Usage Type 选择 `Fixed`
- Replicas 设置为 `1`
- Public Access 已关闭
- Command 为 `python`
- Arguments 为 `polling_worker.py`
- 已填入 [.env.clawcloud.bot-worker.example](/F:/脚本/纵横公开群机器人/群管机器人全功能完整版/.env.clawcloud.bot-worker.example) 对应变量
- 未配置 `WEBHOOK_SECRET`
- 未开启多副本或自动扩容

## 上线后验证

- 私聊 bot 发送 `/start`
- 能正常打开 Web 后台并完成 Telegram 登录
- 后台里能看到目标群
- 保存群配置后，worker 日志没有明显报错
- 新用户进群后会被正确触发验证
- 欢迎语正常发送
- 定时消息正常触发

## 更新版本

- 本地改动后已执行 `git push origin main`
- GitHub Actions 已推送新镜像
- 两个 ClawCloud 应用都点过 `Update` 或 `Redeploy`
- 更新后再次检查 `/healthz`
- 更新后抽样验证 `/start`、入群验证、欢迎语、定时消息
