import os

from ..utils.env import load_local_env

load_local_env()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
SUPER_ADMIN_ID = int(os.environ.get("ADMIN_USER_ID", "6186327330").strip() or "6186327330")

DEFAULT_GROUP_CONFIG = {
    "service_owner_user_id": 0,
    "verify_enabled": True,
    "verify_mode": "join",  # join/calc/image_calc/captcha
    "verify_timeout_sec": 60,
    "verify_max_attempts": 3,
    "verify_fail_action": "mute",  # none/mute/ban/kick
    "verify_private": False,
    "verify_messages": {},
    "verify_fail_text": "{userName} \u9a8c\u8bc1\u5931\u8d25\uff0c\u8bf7\u91cd\u65b0\u52a0\u5165\u540e\u518d\u8bd5\u3002",
    "welcome_enabled": True,
    "welcome_text": "欢迎 {userName} 加入 {group}",
    "welcome_photo_file_id": "",
    "welcome_buttons": [],
    "welcome_ttl_sec": 0,
    "welcome_delete_prev": False,
    "ad_filter": {
        "nickname_enabled": False,
        "sticker_enabled": False,
        "message_enabled": False,
        "block_channel_mask": False,
    },
    "command_gate": {
        "sign": False,
        "profile": False,
        "warn": False,
        "help": False,
        "config": False,
        "ban": False,
        "kick": False,
        "mute": False,
    },
    "crypto": {
        "price_query_enabled": True,
        "push_enabled": False,
        "default_symbol": "BTC",
        "query_alias": "查",
        "wallet_query_enabled": True,
    },
    "member_watch": {
        "nickname_change_detect": False,
        "nickname_change_notice": False,
    },
    "schedule": {
        "enabled": True,
    },
    "points": {
        "enabled": False,
        "chat_points_enabled": False,
        "sign_command": "签到",
        "query_command": "查询积分",
        "rank_command": "积分排行",
        "sign_points": 5,
        "chat_points_per_message": 1,
        "min_text_length": 5,
        "admin_adjust_enabled": False,
    },
    "activity": {
        "enabled": True,
        "today_command": "今日活跃",
        "month_command": "本月活跃",
        "total_command": "总活跃",
    },
    "entertainment": {
        "dice_enabled": True,
        "dice_cost": 10,
        "dice_command": "/dice",
        "gomoku_enabled": False,
        "gomoku_command": "/gomoku",
    },
    "usdt_price": {
        "enabled": False,
        "tier": "best",
        "show_query_message": True,
        "show_calc_message": True,
        "alias_z": "z",
        "alias_w": "w",
        "alias_k": "k",
        "exchanges": ["binance", "okx", "htx"],
    },
    "related_channel": {
        "cancel_top_pin": False,
        "occupy_comment": False,
        "occupy_comment_text": "抢占评论区",
    },
    "admin_access": {
        "mode": "all_admins",
    },
    "nsfw": {
        "enabled": False,
        "sensitivity": "medium",
        "allow_miss": False,
        "notice_enabled": True,
        "delay_delete_sec": 0,
    },
    "language_whitelist": {
        "enabled": False,
        "allowed": ["en", "zh"],
    },
    "invite_links": {
        "enabled": False,
        "notify_enabled": False,
        "join_review": False,
        "notify_text": "{userName} 通过邀请链接加入群组",
        "reward_points": 0,
        "query_command": "/link",
        "today_rank_command": "本日邀请排行",
        "month_rank_command": "本月邀请排行",
        "total_rank_command": "总邀请排行",
        "result_format": "text",
        "only_admin_can_query_rank": False,
        "auto_delete_sec": 0,
    },
    "lottery": {
        "enabled": False,
        "query_command": "抽奖查询",
        "auto_delete_sec": 30,
        "pin_post": True,
        "pin_result": True,
    },
    "verified_user": {
        "enabled": False,
        "members": [],
        "reply_text": "{verifiedUser} 是本群认证会员，请认准官方账号。",
        "reply_photo_file_id": "",
        "reply_buttons": [],
    },
}

DEFAULT_AUTO_REPLIES = []

DEFAULT_AUTO_DELETE = {
    "delete_system": True,
    "delete_channel_mask": False,
    "delete_links": True,
    "delete_long": True,
    "long_length": 500,
    "delete_videos": True,
    "delete_stickers": False,
    "delete_forwarded": False,
    "delete_ad_stickers": False,
    "delete_archives": False,
    "delete_executables": False,
    "delete_notice_text": True,
    "delete_documents": True,
    "delete_mentions": True,
    "delete_other_commands": False,
    "delete_qr": True,
    "delete_edited": False,
    "delete_member_emoji": False,
    "delete_member_emoji_only": False,
    "delete_external_reply": True,
    "delete_shared_contact": True,
    "exclude_admins": True,
    "custom_rules": [],
    "ad_sticker_ids": [],
}

DEFAULT_AUTO_BAN = {
    "enabled": True,
    "default_duration_sec": 86400,
    "rules": [],
}

DEFAULT_AUTO_MUTE = {
    "default_duration_sec": 60,
    "rules": [],
}

DEFAULT_AUTO_WARN = {
    "enabled": True,
    "warn_limit": 3,
    "reset_mode": "daily",
    "mute_seconds": 86400,
    "action": "mute",  # mute/kick
    "cmd_mute_enabled": False,
    "warn_text": "⚠️ {userNameLink} 触发警告 {count}/{limit}",
    "rules": [],
}

DEFAULT_ANTI_SPAM = {
    "enabled": False,
    "action": "mute",  # mute/ban
    "mute_seconds": 300,
    "window_sec": 10,
    "threshold": 3,
    "types": ["text", "photo", "video", "document", "voice", "sticker", "link"],
}

# Conversation states
STATE_NONE = None
STATE_TARGET_INPUT = "target_input"
STATE_WELCOME_TEXT = "welcome_text"
STATE_WELCOME_PHOTO = "welcome_photo"
STATE_WELCOME_TTL = "welcome_ttl"
STATE_WELCOME_DELETE_PREV = "welcome_delete_prev"
STATE_VERIFY_TEXT = "verify_text"
STATE_VERIFY_PHOTO = "verify_photo"
STATE_VERIFY_TIMEOUT = "verify_timeout"
STATE_VERIFY_FAIL_TEXT = "verify_fail_text"
STATE_VERIFY_MODE = "verify_mode"
STATE_VERIFY_TARGET_CONFIRM = "verify_target_confirm"
STATE_VERIFY_BTN_TEXT = "verify_btn_text"
STATE_VERIFY_BTN_VALUE = "verify_btn_value"
STATE_VERIFY_BTN_ROW = "verify_btn_row"

STATE_BTN_TEXT = "btn_text"
STATE_BTN_VALUE = "btn_value"
STATE_BTN_ROW = "btn_row"

STATE_AR_KEYWORD = "ar_keyword"
STATE_AR_MODE = "ar_mode"
STATE_AR_TEXT = "ar_text"
STATE_AR_PHOTO = "ar_photo"
STATE_AR_BTN_TEXT = "ar_btn_text"
STATE_AR_BTN_VALUE = "ar_btn_value"
STATE_AR_BTN_ROW = "ar_btn_row"

STATE_AD_RULE_KEYWORD = "ad_rule_keyword"

STATE_AB_KEYWORD = "ab_keyword"
STATE_AB_DURATION = "ab_duration"
STATE_AB_TEXT = "ab_text"

STATE_AM_KEYWORD = "am_keyword"
STATE_AM_DURATION = "am_duration"

STATE_AW_RULE_KEYWORD = "aw_rule_keyword"
STATE_AW_LIMIT = "aw_limit"
STATE_AW_MUTE = "aw_mute"
STATE_AW_TEXT = "aw_text"

STATE_SPAM_WINDOW = "spam_window"
STATE_SPAM_THRESHOLD = "spam_threshold"
STATE_SPAM_MUTE = "spam_mute"
