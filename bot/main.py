import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, ErrorEvent

from bot.config import Config
from bot.database.connection import Database
from bot.database.repositories import Repository
from bot.handlers import callbacks, commands, messages
from bot.services.api_limiter import ApiLimiter
from bot.services.cleaner import Cleaner
from bot.services.scheduler import Scheduler
from bot.utils.logging_setup import setup_logging
from bot.utils.process_lock import ProcessLock

logger = logging.getLogger(__name__)


def create_dispatcher(cleaner: Cleaner) -> Dispatcher:
    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_routers(
        commands.create_router(cleaner),
        callbacks.create_router(cleaner),
        messages.create_router(cleaner),
    )

    @dispatcher.errors()
    async def handle_error(event: ErrorEvent) -> bool:
        logger.error(
            "update_error kind=%s update=%s",
            type(event.exception).__name__,
            event.update.update_id,
        )
        return True

    return dispatcher


async def main() -> None:
    config = Config.load()
    setup_logging(config.log_path, config.log_level, config.token)
    # Acquire before opening SQLite. OS releases the lock even after a process crash.
    with ProcessLock(config.database_path.with_suffix(".lock")):
        database = await Database.open(config.database_path)
        bot = Bot(config.token)
        bot.session.middleware(ApiLimiter(config.requests_per_second))
        scheduler_task = None
        scheduler = None
        try:
            repository = Repository(database, config.default_delete_delay)
            cleaner = Cleaner(bot, repository, config.max_attempts)
            dispatcher = create_dispatcher(cleaner)
            scheduler = Scheduler(
                cleaner, config.poll_interval, config.batch_size, config.concurrency
            )
            await bot.get_me()
            await bot.delete_webhook(drop_pending_updates=False)
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Начало работы"),
                    BotCommand(command="settings", description="Настройки группы"),
                    BotCommand(command="settime", description="Задержка: 30s, 5m, 1h"),
                    BotCommand(command="enable", description="Включить очистку"),
                    BotCommand(command="disable", description="Выключить и отменить очередь"),
                    BotCommand(command="status", description="Статистика и права"),
                    BotCommand(command="help", description="Справка"),
                ]
            )
            logger.info("bot_started")
            # Drain pending updates (notably pin events) before enabling queue processing.
            offset = None
            while True:
                updates = await bot.get_updates(
                    offset=offset,
                    timeout=0,
                    limit=100,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                )
                if not updates:
                    break
                for update in updates:
                    await dispatcher.feed_update(bot, update)
                    offset = update.update_id + 1
            scheduler_task = asyncio.create_task(scheduler.run(), name="deletion-scheduler")
            await dispatcher.start_polling(
                bot,
                handle_as_tasks=False,
                close_bot_session=False,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
        finally:
            if scheduler and scheduler_task:
                scheduler.stop()
                try:
                    await asyncio.wait_for(scheduler_task, timeout=15)
                except TimeoutError:
                    scheduler_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await scheduler_task
            await bot.session.close()
            await database.close()
            logger.info("bot_stopped")
