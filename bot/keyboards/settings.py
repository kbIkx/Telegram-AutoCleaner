from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.time_parser import format_delay


def button(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"ac:{action}")


def settings_keyboard(enabled: bool, is_forum: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("⏱ Изменить время", "time")],
            [
                button(
                    "❌ Выключить"
                    if enabled
                    else ("✅ Включить в этой теме" if is_forum else "✅ Включить"),
                    "disable" if enabled else "enable",
                )
            ],
            [button("📊 Статистика", "stats"), button("🔄 Обновить", "home")],
            [button("❓ Помощь", "help")],
        ]
    )


def time_keyboard() -> InlineKeyboardMarkup:
    values = [30, 60, 300, 900, 1800, 3600, 21600, 43200, 86400]
    buttons = [button(format_delay(value), f"delay:{value}") for value in values]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *[buttons[index : index + 3] for index in range(0, len(buttons), 3)],
            [button("← Назад", "home")],
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button("← Назад", "home")]])
