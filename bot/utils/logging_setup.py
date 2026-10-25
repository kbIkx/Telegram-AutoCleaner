import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


class SecretFilter(logging.Filter):
    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage().replace(self.token, "[REDACTED]")
        record.msg = re.sub(r"\b[0-9]{5,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED]", text)
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def setup_logging(path: Path, level: str, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s")
    formatter.converter = time.gmtime
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.addFilter(SecretFilter(token))
        handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=handlers, force=True)
    # These libraries can include full updates, request URLs, and tokenized request errors.
    for name in ("aiogram", "aiohttp", "aiosqlite"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
