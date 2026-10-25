import time
from dataclasses import dataclass

import aiosqlite

from bot.database.connection import Database
from bot.utils.time_parser import validate_delay


@dataclass(frozen=True)
class Group:
    chat_id: int
    title: str
    enabled: bool
    delete_delay: int
    created_at: float
    updated_at: float
    enabled_at: float
    is_forum: bool
    cleanup_thread_id: int | None


@dataclass(frozen=True)
class Pending:
    id: int
    chat_id: int
    message_id: int
    sender_user_id: int
    sent_at: float
    delete_at: float
    attempts: int
    last_error: str | None
    message_thread_id: int | None


async def one(db: aiosqlite.Connection, sql: str, args: tuple = ()) -> aiosqlite.Row | None:
    async with db.execute(sql, args) as cursor:
        return await cursor.fetchone()


class Repository:
    def __init__(self, database: Database, default_delay: int = 1800):
        self.database = database
        self.default_delay = validate_delay(default_delay)

    async def ensure_group(
        self,
        chat_id: int,
        title: str,
        is_forum: bool | None = None,
    ) -> Group:
        now = time.time()
        async with self.database.transaction() as db:
            await db.execute(
                "INSERT INTO groups(chat_id,title,delete_delay,created_at,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
                (chat_id, title, self.default_delay, now, now),
            )
            await db.execute("INSERT OR IGNORE INTO statistics(chat_id) VALUES(?)", (chat_id,))
            if is_forum is not None:
                previous = await one(db, "SELECT is_forum FROM groups WHERE chat_id=?", (chat_id,))
                if bool(previous[0]) != is_forum:
                    # Newly discovered forums must never inherit whole-group cleanup or old tasks.
                    await db.execute(
                        "UPDATE groups SET is_forum=?,cleanup_thread_id=NULL,enabled=0,"
                        "updated_at=? WHERE chat_id=?",
                        (int(is_forum), now, chat_id),
                    )
                    await db.execute("DELETE FROM pending_deletions WHERE chat_id=?", (chat_id,))
            row = await one(db, "SELECT * FROM groups WHERE chat_id=?", (chat_id,))
            return Group(**dict(row))

    async def group(self, chat_id: int) -> Group | None:
        async with self.database.transaction() as db:
            row = await one(db, "SELECT * FROM groups WHERE chat_id=?", (chat_id,))
            return Group(**dict(row)) if row else None

    async def is_migrated(self, chat_id: int) -> bool:
        async with self.database.transaction() as db:
            return bool(
                await one(
                    db,
                    "SELECT 1 FROM migrations WHERE old_chat_id=?",
                    (chat_id,),
                )
            )

    async def set_enabled(
        self,
        chat_id: int,
        enabled: bool,
        thread_id: int | None = None,
    ) -> None:
        now = time.time()
        async with self.database.transaction() as db:
            group = await one(db, "SELECT * FROM groups WHERE chat_id=?", (chat_id,))
            if group is None:
                raise ValueError("Group not registered")
            selected = group["cleanup_thread_id"]
            if enabled and group["is_forum"]:
                if thread_id is None or thread_id < 0:
                    raise ValueError("Откройте нужную тему и выполните /enable внутри неё.")
                selected = thread_id
            changed_scope = selected != group["cleanup_thread_id"]
            # Repeated /enable in the same scope preserves live deadlines.
            await db.execute(
                "UPDATE groups SET enabled=?,updated_at=?,cleanup_thread_id=?, "
                "enabled_at=CASE WHEN ? THEN ? ELSE enabled_at END "
                "WHERE chat_id=?",
                (
                    int(enabled),
                    now,
                    selected,
                    int(enabled and (not group["enabled"] or changed_scope)),
                    now,
                    chat_id,
                ),
            )
            if not enabled or changed_scope:
                await db.execute("DELETE FROM pending_deletions WHERE chat_id=?", (chat_id,))

    async def set_delay(self, chat_id: int, delay: int) -> None:
        validate_delay(delay)
        async with self.database.transaction() as db:
            await db.execute(
                "UPDATE groups SET delete_delay=?,updated_at=? WHERE chat_id=?",
                (delay, time.time(), chat_id),
            )

    async def enqueue(
        self,
        chat_id: int,
        message_id: int,
        sender: int,
        sent_at: float,
        thread_id: int | None = None,
    ) -> bool:
        async with self.database.transaction() as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO pending_deletions "
                "(chat_id,message_id,sender_user_id,sent_at,delete_at,message_thread_id) "
                "SELECT chat_id,?,?,?,?+delete_delay,? FROM groups "
                "WHERE chat_id=? AND enabled=1 AND enabled_at<=? "
                "AND (is_forum=0 OR cleanup_thread_id=?) "
                "AND NOT EXISTS(SELECT 1 FROM protected_messages "
                "WHERE chat_id=? AND message_id=?)",
                (
                    message_id,
                    sender,
                    sent_at,
                    sent_at,
                    thread_id,
                    chat_id,
                    sent_at,
                    thread_id,
                    chat_id,
                    message_id,
                ),
            )
            return cursor.rowcount == 1

    async def due(self, now: float, limit: int) -> list[Pending]:
        async with self.database.transaction() as db:
            async with db.execute(
                "SELECT p.* FROM pending_deletions p JOIN groups g USING(chat_id) "
                "WHERE g.enabled=1 AND p.delete_at<=? ORDER BY p.delete_at,p.id LIMIT ?",
                (now, limit),
            ) as cursor:
                return [Pending(**dict(row)) for row in await cursor.fetchall()]

    async def pending(self, task_id: int) -> Pending | None:
        async with self.database.transaction() as db:
            row = await one(db, "SELECT * FROM pending_deletions WHERE id=?", (task_id,))
            return Pending(**dict(row)) if row else None

    async def finish(self, task: Pending, outcome: str = "skipped") -> None:
        async with self.database.transaction() as db:
            cursor = await db.execute("DELETE FROM pending_deletions WHERE id=?", (task.id,))
            if cursor.rowcount != 1:
                return
            if outcome == "deleted":
                await db.execute(
                    "UPDATE statistics SET deleted_count=deleted_count+1,last_deletion_at=? "
                    "WHERE chat_id=?",
                    (time.time(), task.chat_id),
                )
            elif outcome == "failed":
                await db.execute(
                    "UPDATE statistics SET failed_count=failed_count+1 WHERE chat_id=?",
                    (task.chat_id,),
                )

    async def retry(self, task: Pending, at: float, error_code: str) -> None:
        async with self.database.transaction() as db:
            await db.execute(
                "UPDATE pending_deletions SET attempts=attempts+1,delete_at=?,last_error=? "
                "WHERE id=?",
                (at, error_code, task.id),
            )

    async def protect(self, chat_id: int, message_id: int) -> None:
        async with self.database.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO protected_messages VALUES(?,?,?)",
                (chat_id, message_id, time.time()),
            )
            await db.execute(
                "DELETE FROM pending_deletions WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            )

    async def is_protected(self, chat_id: int, message_id: int) -> bool:
        async with self.database.transaction() as db:
            return bool(
                await one(
                    db,
                    "SELECT 1 FROM protected_messages WHERE chat_id=? AND message_id=?",
                    (chat_id, message_id),
                )
            )

    async def stats(self, chat_id: int) -> dict:
        async with self.database.transaction() as db:
            row = await one(
                db,
                "SELECT s.*, (SELECT COUNT(*) FROM pending_deletions p "
                "WHERE p.chat_id=s.chat_id) AS pending_count "
                "FROM statistics s WHERE s.chat_id=?",
                (chat_id,),
            )
            return dict(row) if row else {}

    async def queue_size(self) -> int:
        async with self.database.transaction() as db:
            return (await one(db, "SELECT COUNT(*) FROM pending_deletions"))[0]

    async def migrate(self, old: int, new: int, title: str) -> None:
        """Transfer settings and stats once; never reuse old message IDs in a new chat."""
        async with self.database.transaction() as db:
            if await one(db, "SELECT 1 FROM migrations WHERE old_chat_id=?", (old,)):
                return
            source = await one(db, "SELECT * FROM groups WHERE chat_id=?", (old,))
            if source is None:
                return
            now = time.time()
            await db.execute(
                "INSERT INTO groups(chat_id,title,enabled,delete_delay,created_at,updated_at,"
                "enabled_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET "
                "title=excluded.title,enabled=excluded.enabled,delete_delay=excluded.delete_delay,"
                "updated_at=excluded.updated_at,enabled_at=excluded.enabled_at",
                (
                    new,
                    title,
                    source["enabled"],
                    source["delete_delay"],
                    source["created_at"],
                    now,
                    now,
                ),
            )
            await db.execute("INSERT OR IGNORE INTO statistics(chat_id) VALUES(?)", (new,))
            await db.execute(
                "UPDATE statistics SET deleted_count=deleted_count+"
                "COALESCE((SELECT deleted_count FROM statistics WHERE chat_id=?),0),"
                "failed_count=failed_count+"
                "COALESCE((SELECT failed_count FROM statistics WHERE chat_id=?),0),"
                "last_deletion_at=MAX(COALESCE(last_deletion_at,0),"
                "COALESCE((SELECT last_deletion_at FROM statistics WHERE chat_id=?),0)) "
                "WHERE chat_id=?",
                (old, old, old, new),
            )
            await db.execute("INSERT INTO migrations VALUES(?,?)", (old, new))
            await db.execute("DELETE FROM groups WHERE chat_id=?", (old,))

    async def prune_protected(self, now: float) -> None:
        # Message IDs cannot be reused within a chat. After 48h they cannot be deleted anyway.
        async with self.database.transaction() as db:
            await db.execute(
                "DELETE FROM protected_messages WHERE observed_at<?",
                (now - 48 * 3600,),
            )
