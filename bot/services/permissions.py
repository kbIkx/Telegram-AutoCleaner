from aiogram import Bot
from aiogram.enums import ChatMemberStatus

ADMIN_STATUSES = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}
KNOWN_STATUSES = ADMIN_STATUSES | {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,
    ChatMemberStatus.LEFT,
    ChatMemberStatus.KICKED,
}


class Permissions:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def is_admin(self, chat_id: int, user_id: int) -> bool:
        member = await self.bot.get_chat_member(chat_id, user_id)
        if member.status not in KNOWN_STATUSES:
            raise ValueError("Unknown membership status")
        return member.status in ADMIN_STATUSES

    async def can_delete(self, chat_id: int) -> bool:
        member = await self.bot.get_chat_member(chat_id, self.bot.id)
        return member.status == ChatMemberStatus.CREATOR or (
            member.status == ChatMemberStatus.ADMINISTRATOR
            and bool(getattr(member, "can_delete_messages", False))
        )
