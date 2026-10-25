from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from bot.handlers.common import HELP_TEXT, RIGHTS_WARNING, panel
from bot.keyboards.settings import back_keyboard, settings_keyboard, time_keyboard
from bot.services.cleaner import GROUP_TYPES, Cleaner
from bot.utils.time_parser import validate_delay
from bot.utils.topics import topic_id


def create_router(cleaner: Cleaner) -> Router:
    router = Router(name="callbacks")

    @router.callback_query(F.data.startswith("ac:"))
    async def callback_handler(query: CallbackQuery) -> None:
        message = query.message
        # Never accept a chat_id supplied in callback_data or an inline/private message.
        if (
            not isinstance(message, Message)
            or message.chat.type not in GROUP_TYPES
            or message.from_user is None
            or message.from_user.id != cleaner.bot.id
        ):
            await query.answer("Откройте /settings в нужной группе.", show_alert=True)
            return
        try:
            allowed = await cleaner.permissions.is_admin(message.chat.id, query.from_user.id)
        except (TelegramAPIError, ValueError):
            await query.answer("Не удалось проверить права. Попробуйте позже.", show_alert=True)
            return
        if not allowed:
            await query.answer("Только для администраторов группы.", show_alert=True)
            return
        action = (query.data or "")[3:]
        if action not in {
            "home",
            "stats",
            "time",
            "help",
            "enable",
            "disable",
        } and not action.startswith("delay:"):
            await query.answer("Неизвестная кнопка. Откройте /settings.", show_alert=True)
            return
        if action.startswith("delay:"):
            try:
                delay = validate_delay(int(action.removeprefix("delay:")))
            except ValueError:
                await query.answer("Некорректное время.", show_alert=True)
                return
            await cleaner.set_delay(message.chat.id, delay)
        elif action in {"enable", "disable"}:
            try:
                allowed = await cleaner.set_enabled(
                    message.chat.id,
                    action == "enable",
                    topic_id(message),
                )
            except ValueError as error:
                await query.answer(str(error), show_alert=True)
                return
            if not allowed:
                await query.answer(RIGHTS_WARNING, show_alert=True)
                return
        # Answer before the additional getChatMember call needed to render the panel.
        await query.answer()
        if action == "time":
            text = "⏱ Выберите задержку. Свой вариант: /settime 45m.\nСроки в очереди сохранятся."
            keyboard = time_keyboard()
        elif action == "help":
            text, keyboard = HELP_TEXT, back_keyboard()
        else:
            text, enabled = await panel(cleaner, message.chat.id, statistics=action == "stats")
            group = await cleaner.repository.group(message.chat.id)
            keyboard = settings_keyboard(enabled, group.is_forum)
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message.lower():
                raise

    return router
