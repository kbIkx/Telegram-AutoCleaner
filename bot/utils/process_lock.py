import os
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class ProcessLock:
    """An OS lock survives a crash safely: the OS releases it, no stale PID heuristics."""

    def __init__(self, path: Path):
        self.path = path
        self.file: BinaryIO | None = None

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError(
                "Этот файл базы уже используется другим экземпляром бота."
            ) from None
        return self

    def __exit__(
        self, exc_type: type | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if self.file:
            self.file.close()
