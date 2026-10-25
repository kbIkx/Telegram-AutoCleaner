import asyncio
from unittest.mock import AsyncMock

from aiogram import Bot

from bot.config import Config
from bot.database.connection import Database
from bot.database.repositories import Repository
from bot.main import create_dispatcher
from bot.services.cleaner import Cleaner
from tests.conftest import TOKEN, FakeSession


async def test_startup_and_graceful_shutdown_with_mocked_telegram(tmp_path, monkeypatch):
    import bot.main as entry

    config = Config(
        token=TOKEN,
        database_path=tmp_path / "data/bot.db",
        log_path=tmp_path / "bot.log",
        requests_per_second=25,
    )
    api = FakeSession()
    bot = Bot(TOKEN, session=api)
    database = await Database.open(tmp_path / "dispatcher.db")
    dispatcher = create_dispatcher(Cleaner(bot, Repository(database)))

    async def poll(*args, **kwargs):
        await asyncio.sleep(0.01)

    dispatcher.start_polling = AsyncMock(side_effect=poll)
    monkeypatch.setattr(entry.Config, "load", lambda: config)
    monkeypatch.setattr(entry, "Bot", lambda token: bot)
    monkeypatch.setattr(entry, "create_dispatcher", lambda cleaner: dispatcher)
    monkeypatch.setattr(entry, "setup_logging", lambda *args: None)
    try:
        await entry.main()
        dispatcher.start_polling.assert_awaited_once()
        assert config.database_path.exists()
        assert api.called("deleteWebhook")[0].drop_pending_updates is False
        assert api.called("setMyCommands")
        # Reopening verifies cleanup released the SQLite connection.
        reopened = await Database.open(config.database_path)
        await reopened.close()
    finally:
        await database.close()
