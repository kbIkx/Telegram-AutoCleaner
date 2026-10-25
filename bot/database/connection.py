import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    delete_delay INTEGER NOT NULL CHECK(delete_delay BETWEEN 10 AND 169200),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    enabled_at REAL NOT NULL DEFAULT 0,
    is_forum INTEGER NOT NULL DEFAULT 0 CHECK(is_forum IN (0,1)),
    cleanup_thread_id INTEGER
);
CREATE TABLE IF NOT EXISTS pending_deletions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL REFERENCES groups(chat_id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL,
    sender_user_id INTEGER NOT NULL,
    sent_at REAL NOT NULL,
    delete_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    message_thread_id INTEGER,
    UNIQUE(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS pending_due ON pending_deletions(delete_at, id);
CREATE TABLE IF NOT EXISTS statistics (
    chat_id INTEGER PRIMARY KEY REFERENCES groups(chat_id) ON DELETE CASCADE,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    last_deletion_at REAL
);
CREATE TABLE IF NOT EXISTS protected_messages (
    chat_id INTEGER NOT NULL REFERENCES groups(chat_id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL,
    observed_at REAL NOT NULL,
    PRIMARY KEY(chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS migrations (
    old_chat_id INTEGER PRIMARY KEY,
    new_chat_id INTEGER NOT NULL
);
PRAGMA user_version = 2;
"""


class Database:
    def __init__(self, connection: aiosqlite.Connection):
        self.connection = connection
        self.lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path) -> "Database":
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        connection = await aiosqlite.connect(path, isolation_level=None)
        try:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA synchronous = FULL")
            await connection.execute("PRAGMA busy_timeout = 5000")
            async with connection.execute("PRAGMA user_version") as cursor:
                version = (await cursor.fetchone())[0]
            if version not in (0, 1, 2):
                raise RuntimeError("Неподдерживаемая версия схемы базы данных.")
            if version == 1:
                await connection.executescript("""
                    BEGIN IMMEDIATE;
                    ALTER TABLE groups ADD COLUMN is_forum INTEGER NOT NULL DEFAULT 0;
                    ALTER TABLE groups ADD COLUMN cleanup_thread_id INTEGER;
                    ALTER TABLE pending_deletions ADD COLUMN message_thread_id INTEGER;
                    PRAGMA user_version = 2;
                    COMMIT;
                """)
            await connection.executescript(SCHEMA)
            return cls(connection)
        except BaseException:
            await connection.close()
            raise

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self.lock:
            await self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise

    async def close(self) -> None:
        await self.connection.close()
