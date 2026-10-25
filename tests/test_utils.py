import logging

import pytest

from bot.config import Config
from bot.utils.logging_setup import SecretFilter
from bot.utils.process_lock import ProcessLock
from bot.utils.time_parser import parse_delay
from tests.conftest import TOKEN


@pytest.mark.parametrize(
    "value,seconds",
    [("10s", 10), ("30s", 30), ("5m", 300), ("1h", 3600), ("24h", 86400), ("47h", 169200)],
)
def test_time_parser(value, seconds):
    assert parse_delay(value) == seconds


def test_missing_token_fails_without_disclosing_secret(tmp_path):
    with pytest.raises(ValueError, match="BOT_TOKEN"):
        Config.load(tmp_path)


def test_config_paths_and_repr(tmp_path):
    (tmp_path / ".env").write_text(f"BOT_TOKEN={TOKEN}\n", encoding="utf-8")
    config = Config.load(tmp_path)
    assert config.database_path == tmp_path / "data/autocleaner.db"
    assert TOKEN not in repr(config)


def test_process_lock_prevents_two_instances_and_releases(tmp_path):
    path = tmp_path / "bot.lock"
    with ProcessLock(path):
        with pytest.raises(RuntimeError), ProcessLock(path):
            pass
    with ProcessLock(path):
        pass


def test_log_redaction_removes_token_and_traceback():
    record = logging.LogRecord("test", 20, "test", 1, "Token %s", (TOKEN,), None)
    assert SecretFilter(TOKEN).filter(record)
    assert TOKEN not in record.getMessage()
    assert "REDACTED" in record.getMessage()
    assert record.exc_info is None
