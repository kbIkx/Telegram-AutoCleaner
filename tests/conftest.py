import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, ChatFullInfo, Message, User

from bot.database.connection import Database
from bot.database.repositories import Repository
from bot.services.cleaner import Cleaner

TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
BOT_ID = 123456789
CHAT_ID = -100123456
USER_ID = 101
ADMIN_ID = 201


def message(
    message_id: int = 1,
    sender: int = USER_ID,
    chat_id: int = CHAT_ID,
    text: str | None = "Сообщение",
    **kwargs: Any,
) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type="supergroup", title="Учебная группа"),
        from_user=User(id=sender, is_bot=sender == BOT_ID, first_name="Test"),
        text=text,
        **kwargs,
    )


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[Any] = []
        self.roles: dict[tuple[int, int], str] = {}
        self.bot_can_delete = True
        self.forum_chats: set[int] = set()
        self.pins: dict[int, Message] = {}
        self.errors: defaultdict[tuple, list[Exception]] = defaultdict(list)

    async def close(self):
        pass

    async def stream_content(
        self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True
    ):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = method.__api_method__
        key = (
            name,
            getattr(method, "chat_id", None),
            getattr(method, "message_id", getattr(method, "user_id", None)),
        )
        if self.errors[key]:
            raise self.errors[key].pop(0)
        if name == "getMe":
            return User(id=BOT_ID, is_bot=True, first_name="AutoCleaner", username="test_bot")
        if name == "getUpdates":
            return []
        if name == "getChatMember":
            role = self.roles.get((method.chat_id, method.user_id), "member")
            if method.user_id == BOT_ID:
                role = "administrator" if self.bot_can_delete else "member"
            from aiogram.types import ChatMemberAdministrator, ChatMemberMember, ChatMemberOwner

            user = User(id=method.user_id, is_bot=method.user_id == BOT_ID, first_name="Test")
            if role == "creator":
                return ChatMemberOwner(user=user, is_anonymous=False)
            if role == "administrator":
                fields = {
                    name: False
                    for name, field in ChatMemberAdministrator.model_fields.items()
                    if field.is_required() and name not in {"status", "user"}
                }
                fields["can_delete_messages"] = True
                return ChatMemberAdministrator(user=user, **fields)
            return ChatMemberMember(user=user)
        if name == "getChat":
            return ChatFullInfo(
                id=method.chat_id,
                type="supergroup",
                title="Учебная группа",
                is_forum=True if method.chat_id in self.forum_chats else None,
                accent_color_id=0,
                max_reaction_count=11,
                accepted_gift_types={
                    "unlimited_gifts": False,
                    "limited_gifts": False,
                    "unique_gifts": False,
                    "premium_subscription": False,
                    "gifts_from_channels": False,
                },
                pinned_message=self.pins.get(method.chat_id),
            )
        if name in {"sendMessage", "editMessageText"}:
            return message(
                getattr(method, "message_id", None) or 900,
                sender=BOT_ID,
                chat_id=method.chat_id,
                text=method.text,
            )
        return True

    def called(self, name):
        return [method for method in self.calls if method.__api_method__ == name]


@pytest.fixture
async def app(tmp_path):
    database = await Database.open(tmp_path / "test.db")
    repo = Repository(database, default_delay=10)
    await repo.ensure_group(CHAT_ID, "Учебная группа")
    await repo.set_enabled(CHAT_ID, True)
    # Tests use messages in the past without wall-clock sleeps.
    async with database.transaction() as db:
        await db.execute("UPDATE groups SET enabled_at=?", (time.time() - 3600,))
    session = FakeSession()
    bot = Bot(TOKEN, session=session)
    cleaner = Cleaner(bot, repo)
    session.roles[(CHAT_ID, ADMIN_ID)] = "administrator"
    yield cleaner, repo, session, database
    await bot.session.close()
    await database.close()


async def make_due(repo, message_id=1, chat_id=CHAT_ID):
    async with repo.database.transaction() as db:
        await db.execute(
            "UPDATE pending_deletions SET delete_at=? WHERE chat_id=? AND message_id=?",
            (time.time() - 1, chat_id, message_id),
        )
