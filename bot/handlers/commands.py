from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.handlers.common import HELP_TEXT, RIGHTS_WARNING, authorize, panel
from bot.keyboards.settings import settings_keyboard
from bot.services.cleaner import Cleaner
from bot.utils.time_parser import format_delay, parse_delay
from bot.utils.topics import scope_label, topic_id


def create_router(cleaner: Cleaner) -> Router:
    router = Router(name="commands")

    @router.message(Command("start", "help", "settings", "settime", "enable", "disable", "status"))
    async def command_handler(message: Message, command: CommandObject) -> None:
        await cleaner.register(message)
        name = command.command.lower()
        if name in {"start", "help"}:
            if name == "start" and message.chat.type == "private":
                me = await cleaner.bot.get_me()
                await message.answer(
                    "Здравствуйте! Я AutoCleaner. Удаляю сообщения участников по таймеру, "
                    "сохраняя сообщения администраторов.\n\n"
                    f"Добавить в группу: https://t.me/{me.username}?startgroup=true\n\n"
                    "Назначьте меня администратором с правом удаления сообщений. "
                    "В группе выполните /settime 30m, затем /enable.\n\n" + HELP_TEXT,
                )
            else:
                await message.answer(HELP_TEXT)
            return
        if not await authorize(message, cleaner):
            return
        await cleaner.repository.ensure_group(message.chat.id, message.chat.title or "Группа")
        if name == "settime":
            try:
                delay = parse_delay(command.args or "")
            except ValueError as error:
                await message.answer(str(error))
                return
            await cleaner.set_delay(message.chat.id, delay)
            await message.answer(f"Новые сообщения будут удаляться через {format_delay(delay)}.")
        elif name in {"enable", "disable"}:
            try:
                allowed = await cleaner.set_enabled(
                    message.chat.id,
                    name == "enable",
                    topic_id(message),
                )
            except ValueError as error:
                await message.answer(str(error))
                return
            if not allowed:
                await message.answer(RIGHTS_WARNING)
                return
            group = await cleaner.repository.group(message.chat.id)
            await message.answer(
                "✅ Очистка включена для новых сообщений.\nОбласть: "
                + scope_label(group.is_forum, group.cleanup_thread_id)
                if name == "enable"
                else "Очистка выключена. Ожидающие удаления отменены.",
            )
        else:
            text, enabled = await panel(cleaner, message.chat.id, statistics=name == "status")
            group = await cleaner.repository.group(message.chat.id)
            await message.answer(text, reply_markup=settings_keyboard(enabled, group.is_forum))

    return router
