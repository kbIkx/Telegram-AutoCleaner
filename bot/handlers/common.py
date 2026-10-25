import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from bot.services.cleaner import GROUP_TYPES, Cleaner
from bot.utils.time_parser import format_delay
from bot.utils.topics import scope_label

logger = logging.getLogger(__name__)
HELP_TEXT = (
    "AutoCleaner сохраняет сообщения администраторов и удаляет новые сообщения участников.\n\n"
    "/settings — панель управления\n"
    "/settime 30s | 5m | 1h — задержка от 10 секунд до 47 часов\n"
    "/enable — включить очистку (в форуме — только в текущей теме)\n"
    "/disable — выключить и отменить очередь\n"
    "/status — очередь, статистика и права\n"
    "/help — справка\n\n"
    "Управление доступно администраторам в самой группе. Новая задержка действует только "
    "на новые сообщения. После включения старые сообщения не добавляются.\n\n"
    "В супергруппе с темами откройте нужную тему, например «Информация», и выполните "
    "/enable именно в ней. Остальные темы сохраняются. Повторный /enable в другой теме "
    "перенесёт очистку туда и отменит старую очередь.\n\n"
    "Закрепления защищаются по доступным событиям Telegram. Полная проверка всех "
    "закреплений через Bot API невозможна; подробности — в README."
)
RIGHTS_WARNING = (
    "⚠️ Нужны права администратора и разрешение «Удаление сообщений». "
    "Выдайте их боту и выполните /enable."
)


async def authorize(message: Message, cleaner: Cleaner) -> bool:
    if message.chat.type not in GROUP_TYPES:
        await message.answer("Эта команда доступна только в группе. Откройте нужную группу.")
        return False
    if message.sender_chat is not None or message.from_user is None:
        await message.answer(
            "Для управления отправьте команду от личного аккаунта администратора."
        )
        return False
    try:
        allowed = await cleaner.permissions.is_admin(message.chat.id, message.from_user.id)
    except (TelegramAPIError, ValueError):
        await message.answer("Не удалось проверить права. Попробуйте позже.")
        return False
    if not allowed:
        await message.answer("Управление доступно только администраторам группы.")
    return allowed


async def panel(cleaner: Cleaner, chat_id: int, statistics: bool = False) -> tuple[str, bool]:
    group = await cleaner.repository.group(chat_id)
    if group is None:
        raise ValueError("Group not registered")
    stats = await cleaner.repository.stats(chat_id)
    try:
        rights = (
            "✅ Достаточно для удаления"
            if await cleaner.permissions.can_delete(chat_id)
            else RIGHTS_WARNING
        )
    except TelegramAPIError:
        rights = "⚠️ Не удалось проверить; удаление требует проверки прав"
    text = (
        f"AutoCleaner — {'Статистика' if statistics else 'Настройки'}\n\n"
        f"Группа: {group.title}\n"
        f"Статус: {'🟢 Включён' if group.enabled else '⚪ Выключен'}\n"
        f"Область: {scope_label(group.is_forum, group.cleanup_thread_id)}\n"
        f"Удаление через: {format_delay(group.delete_delay)}\n"
        f"Сообщений в очереди: {stats['pending_count']}\n"
        f"Права бота: {rights}"
    )
    if group.is_forum:
        text += "\nДля выбора/смены темы: выполните /enable внутри нужной темы."
    if statistics:
        text += (
            f"\nУспешно удалено: {stats['deleted_count']}"
            f"\nОшибок удаления: {stats['failed_count']}"
            "\nОшибки — окончательно пропущенные задачи после исчерпания попыток."
        )
    return text, bool(group.enabled)
