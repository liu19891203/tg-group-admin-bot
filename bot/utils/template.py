
import html

from .time import now_date_str


def render_template(text: str, user, chat, extra: dict | None = None):
    safe = html.escape(text or "")
    if user:
        safe = safe.replace("{user}", user.mention_html())
        safe = safe.replace("{userName}", html.escape(user.full_name or ""))
        safe = safe.replace("{userNameLink}", user.mention_html())
    if chat:
        safe = safe.replace("{group}", html.escape(chat.title or ""))
    safe = safe.replace("{date}", now_date_str())
    if extra:
        if "question" in extra and extra["question"] is not None:
            safe = safe.replace("{question}", html.escape(str(extra["question"])))
        for key, value in extra.items():
            if key == "question":
                continue
            if value is None:
                continue
            safe = safe.replace("{" + str(key) + "}", html.escape(str(value)))
    return safe
