import re

MIN_DELAY = 10
MAX_DELAY = 47 * 3600


def validate_delay(seconds: int) -> int:
    if not MIN_DELAY <= seconds <= MAX_DELAY:
        raise ValueError("Допустимое время: от 10 секунд до 47 часов.")
    return seconds


def parse_delay(value: str) -> int:
    match = re.fullmatch(r"([0-9]{1,6})([smh])", value.strip().lower())
    if not match:
        raise ValueError("Формат: /settime 30s, /settime 5m или /settime 1h.")
    return validate_delay(int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]])


def format_delay(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч"
    if seconds % 60 == 0:
        return f"{seconds // 60} мин"
    return f"{seconds} сек"
