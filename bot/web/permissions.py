from types import SimpleNamespace

from ..handlers.admin import _can_manage_group, _manageable_groups


async def get_manageable_groups(bot, user_id: int) -> list[dict]:
    return await _manageable_groups(SimpleNamespace(bot=bot), int(user_id))


async def ensure_group_access(bot, user_id: int, group_id: int) -> bool:
    return await _can_manage_group(SimpleNamespace(bot=bot), int(user_id), int(group_id))
