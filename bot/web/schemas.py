from copy import deepcopy

MODULES = [
    {"key": "verify", "label": "进群验证", "icon": "🆕", "editor": "rich"},
    {"key": "welcome", "label": "欢迎消息", "icon": "👋", "editor": "rich"},
    {"key": "autoreply", "label": "自动回复", "icon": "💬", "editor": "rich"},
    {"key": "autodelete", "label": "自动删除", "icon": "🧹", "editor": "autodelete"},
    {"key": "autoban", "label": "自动封禁", "icon": "🛑", "editor": "autoban"},
    {"key": "autowarn", "label": "自动警告", "icon": "⚠️", "editor": "autowarn"},
    {"key": "automute", "label": "自动禁言", "icon": "🤐", "editor": "automute"},
    {"key": "antispam", "label": "刷屏处理", "icon": "✋", "editor": "antispam"},
    {"key": "ad", "label": "广告封杀", "icon": "🗑", "editor": "ad"},
    {"key": "cmd", "label": "命令关闭", "icon": "🚫", "editor": "cmd"},
    {"key": "crypto", "label": "加密货币", "icon": "💎", "editor": "crypto"},
    {"key": "member", "label": "群组成员", "icon": "👥", "editor": "member"},
    {"key": "schedule", "label": "定时消息", "icon": "⏰", "editor": "schedule"},
    {"key": "points", "label": "积分相关", "icon": "🧾", "editor": "points"},
    {"key": "activity", "label": "活跃度统计", "icon": "📊", "editor": "activity"},
    {"key": "fun", "label": "娱乐功能", "icon": "🎮", "editor": "fun"},
    {"key": "usdt", "label": "实时查U价", "icon": "💹", "editor": "usdt"},
    {"key": "related", "label": "关联频道", "icon": "🔗", "editor": "related"},
    {"key": "admin_access", "label": "管理权限", "icon": "🛡️", "editor": "admin_access"},
    {"key": "nsfw", "label": "色情处理", "icon": "🔞", "editor": "nsfw"},
    {"key": "lang", "label": "语言白名单", "icon": "📃", "editor": "lang"},
    {"key": "invite", "label": "邀请链接", "icon": "🖇", "editor": "invite"},
    {"key": "lottery", "label": "抽奖", "icon": "🎁", "editor": "lottery"},
    {"key": "verified", "label": "认证用户", "icon": "✅", "editor": "verified"},
]
MODULE_INDEX = {item["key"]: item for item in MODULES}


def list_modules() -> list[dict]:
    return deepcopy(MODULES)


def get_module(key: str) -> dict | None:
    item = MODULE_INDEX.get(key)
    return deepcopy(item) if item else None
