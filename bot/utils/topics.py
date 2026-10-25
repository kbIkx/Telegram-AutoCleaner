from aiogram.types import Message


def topic_id(message: Message) -> int | None:
    """0 represents General internally; never trust a reply-thread ID outside a topic."""
    if not message.chat.is_forum:
        return None
    if not message.is_topic_message:
        return 0
    value = message.message_thread_id
    return value if value is not None and value > 0 else None


def scope_label(is_forum: bool, selected_topic: int | None) -> str:
    if not is_forum:
        return "Вся группа (тем нет)"
    if selected_topic is None:
        return "Тема не выбрана — удаление заблокировано"
    if selected_topic == 0:
        return "Только общая тема (General)"
    return f"Только тема #{selected_topic}"
