import asyncio
import logging
import time
from collections import defaultdict

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import Message

from bot.database.repositories import Pending, Repository
from bot.services.permissions import Permissions
from bot.utils.topics import topic_id

logger = logging.getLogger(__name__)
GROUP_TYPES = {"group", "supergroup"}
SUPPORTED_CONTENT = {
    "text",
    "photo",
    "video",
    "voice",
    "video_note",
    "document",
    "sticker",
    "animation",
    "audio",
    "poll",
    "contact",
    "location",
    "venue",
    "dice",
}
MAX_MESSAGE_AGE = 48 * 3600 - 60


class Cleaner:
    def __init__(self, bot: Bot, repository: Repository, max_attempts: int = 5):
        self.bot = bot
        self.repository = repository
        self.permissions = Permissions(bot)
        self.max_attempts = max_attempts
        self.locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def register(self, message: Message) -> None:
        if message.chat.type not in GROUP_TYPES:
            return
        async with self.locks[message.chat.id]:
            if await self.repository.is_migrated(message.chat.id):
                return
            await self.repository.ensure_group(
                message.chat.id,
                message.chat.title or "Группа",
                True if message.chat.is_forum else None,
            )
            if message.pinned_message:
                await self.repository.protect(message.chat.id, message.pinned_message.message_id)
                return
            group = await self.repository.group(message.chat.id)
            if (
                not group
                or not group.enabled
                or (
                    group.is_forum
                    and (
                        group.cleanup_thread_id is None
                        or topic_id(message) != group.cleanup_thread_id
                    )
                )
                or message.content_type not in SUPPORTED_CONTENT
                or message.sender_chat is not None
                or message.from_user is None
                or message.from_user.id == self.bot.id
                or message.date.timestamp() < group.enabled_at
                or time.time() - message.date.timestamp() >= MAX_MESSAGE_AGE
            ):
                return
            try:
                if await self.permissions.is_admin(message.chat.id, message.from_user.id):
                    return
            except (TelegramAPIError, ValueError):
                # No reliable identity check: preserve rather than guess.
                logger.warning("author_check_unavailable chat=%s", message.chat.id)
                return
            if await self.repository.enqueue(
                message.chat.id,
                message.message_id,
                message.from_user.id,
                message.date.timestamp(),
                topic_id(message),
            ):
                logger.info("queued chat=%s message=%s", message.chat.id, message.message_id)

    async def set_enabled(
        self,
        chat_id: int,
        enabled: bool,
        thread_id: int | None = None,
    ) -> bool:
        async with self.locks[chat_id]:
            if enabled and not await self.permissions.can_delete(chat_id):
                return False
            if enabled:
                chat = await self.bot.get_chat(chat_id)
                await self.repository.ensure_group(
                    chat_id, chat.title or "Группа", bool(chat.is_forum)
                )
            await self.repository.set_enabled(chat_id, enabled, thread_id)
            logger.info("settings chat=%s enabled=%s thread=%s", chat_id, enabled, thread_id)
            return True

    async def set_delay(self, chat_id: int, delay: int) -> None:
        async with self.locks[chat_id]:
            await self.repository.set_delay(chat_id, delay)
            logger.info("settings chat=%s delay=%s", chat_id, delay)

    async def migrate(self, old: int, new: int, title: str) -> None:
        if old == new:
            return
        # Stable lock order prevents deadlocks with two migration notifications.
        async with self.locks[min(old, new)], self.locks[max(old, new)]:
            await self.repository.migrate(old, new, title)
            logger.info("migration old=%s new=%s old_queue_cancelled", old, new)

    async def _permissions_lost(self, chat_id: int) -> None:
        # Caller holds the group lock. Disable and cancel atomically before notification.
        await self.repository.set_enabled(chat_id, False)
        logger.warning("permissions_lost chat=%s cleanup_disabled", chat_id)
        try:
            await self.bot.send_message(
                chat_id,
                "⚠️ Очистка остановлена, очередь отменена. Администратор: проверьте права "
                "бота на удаление сообщений и выполните /enable.",
            )
        except TelegramAPIError:
            logger.warning("permission_notice_unavailable chat=%s", chat_id)

    async def _retry(self, task: Pending, code: str, delay: float | None = None) -> None:
        retry_delay = delay if delay is not None else min(300, 2 ** (task.attempts + 1))
        retry_at = time.time() + retry_delay
        if task.attempts + 1 >= self.max_attempts or retry_at >= task.sent_at + MAX_MESSAGE_AGE:
            await self.repository.finish(task, "failed")
            logger.warning(
                "abandoned chat=%s message=%s code=%s", task.chat_id, task.message_id, code
            )
        else:
            await self.repository.retry(task, retry_at, code)
            logger.warning("retry chat=%s message=%s code=%s", task.chat_id, task.message_id, code)

    async def process(self, task_id: int, chat_id: int) -> None:
        async with self.locks[chat_id]:
            task = await self.repository.pending(task_id)
            group = await self.repository.group(chat_id)
            if not task:
                return
            if not group or not group.enabled:
                await self.repository.finish(task)
                return
            if group.is_forum and (
                group.cleanup_thread_id is None
                or task.message_thread_id != group.cleanup_thread_id
            ):
                await self.repository.finish(task)
                return
            if task.delete_at > time.time():
                return
            if task.sender_user_id == self.bot.id:
                await self.repository.finish(task)
                return
            if time.time() >= task.sent_at + MAX_MESSAGE_AGE:
                await self.repository.finish(task, "failed")
                logger.warning("expired chat=%s message=%s", chat_id, task.message_id)
                return
            new_chat_id = None
            try:
                if not await self.permissions.can_delete(chat_id):
                    await self._permissions_lost(chat_id)
                    return
                if await self.permissions.is_admin(chat_id, task.sender_user_id):
                    await self.repository.finish(task)
                    return
                # getChat exposes only the most recent pin. Also persist every observed pin event.
                chat = await self.bot.get_chat(chat_id)
                if bool(chat.is_forum) != bool(group.is_forum):
                    await self.repository.ensure_group(
                        chat_id, chat.title or group.title, bool(chat.is_forum)
                    )
                    return
                if chat.pinned_message:
                    await self.repository.protect(chat_id, chat.pinned_message.message_id)
                if await self.repository.is_protected(chat_id, task.message_id):
                    await self.repository.finish(task)
                    return
                await self.bot.delete_message(chat_id, task.message_id)
            except TelegramRetryAfter as error:
                await self._retry(task, "rate_limit", error.retry_after + 1)
                return
            except (TelegramNetworkError, TelegramServerError):
                await self._retry(task, "network_or_server")
                return
            except TelegramMigrateToChat as error:
                new_chat_id = error.migrate_to_chat_id
            except TelegramForbiddenError:
                await self._permissions_lost(chat_id)
                return
            except TelegramBadRequest as error:
                # Inspect API descriptions locally, never persist or log their raw text.
                description = error.message.lower()
                if "message to delete not found" in description:
                    await self.repository.finish(task)
                elif any(
                    code in description
                    for code in (
                        "not enough rights",
                        "chat_admin_required",
                        "need administrator rights",
                    )
                ):
                    await self._permissions_lost(chat_id)
                elif (
                    "message can't be deleted" in description
                    or "message_id_invalid" in description
                ):
                    await self.repository.finish(task, "failed")
                else:
                    await self._retry(task, "bad_request_or_author_unverifiable")
                return
            except (TelegramAPIError, ValueError):
                await self._retry(task, "api_or_author_unverifiable")
                return
            else:
                await self.repository.finish(task, "deleted")
                logger.info("deleted chat=%s message=%s", chat_id, task.message_id)
                return
        if new_chat_id is not None:
            await self.migrate(chat_id, new_chat_id, group.title)
