import logging

from aiogram import Router
from aiogram.types import ChatMemberUpdated, Message

from bot.handlers.common import RIGHTS_WARNING
from bot.services.cleaner import GROUP_TYPES, Cleaner
from bot.services.permissions import ADMIN_STATUSES

logger = logging.getLogger(__name__)


def create_router(cleaner: Cleaner) -> Router:
    router = Router(name="messages")

    @router.message()
    async def message_handler(message: Message) -> None:
        if message.chat.type not in GROUP_TYPES:
            return
        await cleaner.register(message)
        if message.migrate_to_chat_id:
            await cleaner.migrate(
                message.chat.id, message.migrate_to_chat_id, message.chat.title or "Группа"
            )
        elif message.migrate_from_chat_id:
            await cleaner.migrate(
                message.migrate_from_chat_id, message.chat.id, message.chat.title or "Группа"
            )

    @router.my_chat_member()
    async def membership_handler(event: ChatMemberUpdated) -> None:
        if event.chat.type not in GROUP_TYPES:
            return
        member = event.new_chat_member
        async with cleaner.locks[event.chat.id]:
            await cleaner.repository.ensure_group(
                event.chat.id,
                event.chat.title or "Группа",
                True if event.chat.is_forum else None,
            )
            if member.status not in ADMIN_STATUSES or not getattr(
                member, "can_delete_messages", False
            ):
                await cleaner.repository.set_enabled(event.chat.id, False)
                logger.warning("membership_permissions_lost chat=%s", event.chat.id)
                if member.status not in {"left", "kicked"}:
                    await cleaner.bot.send_message(event.chat.id, RIGHTS_WARNING)
            else:
                logger.info("bot_admin_added chat=%s", event.chat.id)
                await cleaner.bot.send_message(
                    event.chat.id,
                    "AutoCleaner готов. Администратор: /settings → "
                    "выберите время и включите очистку. Если в группе есть темы, "
                    "выполните /enable внутри нужной темы — будет очищаться только она.",
                )

    return router
