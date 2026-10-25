import logging
from dataclasses import dataclass, field
from pathlib import Path

from aiogram.utils.token import validate_token
from dotenv import dotenv_values

from bot.utils.time_parser import validate_delay

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    database_path: Path
    log_path: Path
    log_level: str = "INFO"
    default_delete_delay: int = 1800
    poll_interval: float = 1.0
    batch_size: int = 30
    concurrency: int = 3
    max_attempts: int = 5
    requests_per_second: float = 15.0

    @classmethod
    def load(cls, root: Path = PROJECT_ROOT) -> "Config":
        # Read this exact file only; never search parent folders or accept token CLI args.
        values = dotenv_values(root / ".env", interpolate=False)
        token = values.get("BOT_TOKEN") or ""
        try:
            validate_token(token)
        except Exception:
            raise ValueError("Укажите корректный BOT_TOKEN в файле .env.") from None

        def value(name: str, default: str) -> str:
            return values.get(name) or default

        def path(name: str, default: str) -> Path:
            result = Path(value(name, default)).expanduser()
            return result if result.is_absolute() else root / result

        try:
            config = cls(
                token=token,
                database_path=path("DATABASE_PATH", "data/autocleaner.db"),
                log_path=path("LOG_PATH", "logs/autocleaner.log"),
                log_level=value("LOG_LEVEL", "INFO").upper(),
                default_delete_delay=validate_delay(int(value("DEFAULT_DELETE_DELAY", "1800"))),
                poll_interval=float(value("POLL_INTERVAL", "1")),
                batch_size=int(value("BATCH_SIZE", "30")),
                concurrency=int(value("MAX_CONCURRENT_DELETIONS", "3")),
                max_attempts=int(value("MAX_ATTEMPTS", "5")),
                requests_per_second=float(value("API_REQUESTS_PER_SECOND", "15")),
            )
        except (ValueError, OverflowError):
            raise ValueError("Проверьте числовые параметры в .env.") from None
        if (
            not 0.1 <= config.poll_interval <= 60
            or not 1 <= config.batch_size <= 100
            or not 1 <= config.concurrency <= 10
            or not 1 <= config.max_attempts <= 20
            or not 0.1 <= config.requests_per_second <= 25
            or config.log_level not in logging.getLevelNamesMapping()
        ):
            raise ValueError("Параметры .env выходят за допустимые границы.")
        return config
