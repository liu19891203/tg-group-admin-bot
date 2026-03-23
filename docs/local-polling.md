# Local Polling Debug

真实 Telegram 端到端联调请优先使用 `local_polling.py`。

## Why

- 当前线上 bot 使用 webhook。
- 本机如果想直接从 Telegram 拉取真实更新，需要临时停用 webhook，切到 polling。
- `local_polling.py` 会在退出时自动把原 webhook 恢复回去。

## Run

```bash
python local_polling.py
```

如果只想短时间验证链路：

```bash
POLL_TIMEOUT_SEC=30 python local_polling.py
```

PowerShell:

```powershell
$env:POLL_TIMEOUT_SEC='30'
python local_polling.py
```

## Notes

- 运行期间请用真实 Telegram 账号给 bot 发消息，或把 bot 拉进测试群后再发一条群消息。
- 如果没有真实入站消息，脚本只能验证 Telegram API 联机、webhook 切换和 polling 启动是否正常，不能覆盖完整入站处理链路。
