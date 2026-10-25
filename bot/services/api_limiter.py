import asyncio
import time
from typing import Any

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramRetryAfter


class ApiLimiter(BaseRequestMiddleware):
    """Global pacing and flood cooldown apply to all outgoing Bot API calls."""

    def __init__(self, requests_per_second: float = 15, concurrency: int = 4):
        self.interval = 1 / requests_per_second
        self.lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(concurrency)
        self.next_request = 0.0
        self.cooldown = 0.0

    async def __call__(self, make_request: Any, bot: Any, method: Any) -> Any:
        async with self.semaphore:
            async with self.lock:
                while True:
                    wait = max(self.next_request, self.cooldown) - time.monotonic()
                    if wait <= 0:
                        break
                    await asyncio.sleep(wait)
                self.next_request = time.monotonic() + self.interval
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as error:
                self.cooldown = max(self.cooldown, time.monotonic() + error.retry_after + 1)
                raise
