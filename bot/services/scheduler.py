import asyncio
import logging
import time

from bot.services.cleaner import Cleaner

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self, cleaner: Cleaner, interval: float = 1, batch_size: int = 30, concurrency: int = 3
    ):
        self.cleaner = cleaner
        self.interval = interval
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(concurrency)
        self.tick_lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.last_prune = 0.0

    async def _process(self, task_id: int, chat_id: int) -> None:
        async with self.semaphore:
            try:
                await self.cleaner.process(task_id, chat_id)
            except Exception as error:
                # No exception text/traceback: upstream exceptions may contain secrets or content.
                logger.error("worker_error chat=%s kind=%s", chat_id, type(error).__name__)
                # A programming/storage failure stops this batch item, not the other groups.
                task = await self.cleaner.repository.pending(task_id)
                if task:
                    await self.cleaner._retry(task, "internal_error")

    async def tick(self) -> None:
        async with self.tick_lock:
            now = time.time()
            tasks = await self.cleaner.repository.due(now, self.batch_size)
            results = await asyncio.gather(
                *(self._process(task.id, task.chat_id) for task in tasks),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.error("batch_item_error kind=%s", type(result).__name__)
            if now - self.last_prune > 3600:
                await self.cleaner.repository.prune_protected(now)
                self.last_prune = now

    async def run(self) -> None:
        logger.info(
            "scheduler_started restored_queue=%s", await self.cleaner.repository.queue_size()
        )
        while not self.stop_event.is_set():
            try:
                await self.tick()
            except Exception as error:
                logger.error("scheduler_error kind=%s", type(error).__name__)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval)
            except TimeoutError:
                pass
        logger.info("scheduler_stopped")

    def stop(self) -> None:
        self.stop_event.set()
