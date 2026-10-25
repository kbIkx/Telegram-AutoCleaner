import asyncio
import sys


def run() -> int:
    if sys.version_info < (3, 12):
        print("Нужен Python 3.12 или новее.", file=sys.stderr)
        return 1
    try:
        from bot.main import main

        asyncio.run(main())
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        # Never print raw API exception text; it may include a tokenized URL or update.
        print(
            f"Запуск остановлен ({type(error).__name__}). Проверьте .env, сеть и права "
            "на запись в data/ и logs/. Подробности — README.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
