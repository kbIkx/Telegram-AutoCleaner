import sqlite3
import time

import pytest
from aiogram.types import CallbackQuery, Chat, MessageEntity, Update, User

from bot.database.connection import Database
from bot.database.repositories import Repository
from bot.main import create_dispatcher
from bot.services.scheduler import Scheduler
from bot.utils.topics import topic_id
from tests.conftest import ADMIN_ID, BOT_ID, CHAT_ID, USER_ID, make_due, message


def forum_message(number=1, thread=10, sender=USER_ID, text="Сообщение"):
    return message(number, sender=sender, text=text).model_copy(
        update={
            "chat": Chat(id=CHAT_ID, type="supergroup", title="music", is_forum=True),
            "is_topic_message": True if thread is not None else None,
            "message_thread_id": thread,
        }
    )


async def select_topic(app, selected=10):
    cleaner, repo, api, db = app
    api.forum_chats.add(CHAT_ID)
    await repo.ensure_group(CHAT_ID, "music", True)
    await cleaner.set_enabled(CHAT_ID, True, selected)
    async with db.transaction() as connection:
        await connection.execute("UPDATE groups SET enabled_at=?", (time.time() - 3600,))


async def test_forum_without_binding_cancels_legacy_whole_group_cleanup(app):
    cleaner, repo, api, _ = app
    await cleaner.register(message())
    assert await repo.queue_size() == 1
    await cleaner.register(forum_message(2))
    group = await repo.group(CHAT_ID)
    assert group.is_forum and not group.enabled
    assert group.cleanup_thread_id is None
    assert await repo.queue_size() == 0
    await Scheduler(cleaner).tick()
    assert not api.called("deleteMessage")


async def test_only_selected_topic_is_queued_and_deleted(app):
    cleaner, repo, api, _ = app
    await select_topic(app, 10)
    await cleaner.register(forum_message(1, 10))
    await cleaner.register(forum_message(2, 20))
    await cleaner.register(forum_message(3, None))
    assert await repo.queue_size() == 1
    tasks = await repo.due(time.time() + 100, 10)
    assert tasks[0].message_thread_id == 10
    await make_due(repo)
    await Scheduler(cleaner).tick()
    assert [call.message_id for call in api.called("deleteMessage")] == [1]


async def test_database_also_rejects_wrong_topic(app):
    _, repo, _, _ = app
    await select_topic(app, 10)
    assert not await repo.enqueue(CHAT_ID, 1, USER_ID, time.time(), 20)
    assert not await repo.enqueue(CHAT_ID, 2, USER_ID, time.time(), None)
    assert await repo.queue_size() == 0


async def test_switching_topic_cancels_old_queue_and_does_not_replay(app):
    cleaner, repo, _, _ = app
    await select_topic(app, 10)
    await cleaner.register(forum_message(1, 10))
    old_other_message = forum_message(2, 20)
    await cleaner.register(old_other_message)
    await cleaner.set_enabled(CHAT_ID, True, 20)
    assert await repo.queue_size() == 0
    await cleaner.register(old_other_message)
    await cleaner.register(forum_message(3, 10))
    assert await repo.queue_size() == 0
    await cleaner.register(forum_message(4, 20))
    tasks = await repo.due(time.time() + 100, 10)
    assert [(task.message_id, task.message_thread_id) for task in tasks] == [(4, 20)]


async def test_repeated_enable_same_topic_preserves_deadlines(app):
    cleaner, repo, _, _ = app
    await select_topic(app, 10)
    await cleaner.register(forum_message())
    before = (await repo.due(time.time() + 100, 10))[0]
    await cleaner.set_enabled(CHAT_ID, True, 10)
    after = (await repo.due(time.time() + 100, 10))[0]
    assert after.id == before.id and after.delete_at == before.delete_at


async def test_missing_topic_context_cannot_enable_forum(app):
    cleaner, repo, _, _ = app
    await select_topic(app, 10)
    await cleaner.set_enabled(CHAT_ID, False)
    with pytest.raises(ValueError, match="нужную тему"):
        await cleaner.set_enabled(CHAT_ID, True)
    assert not (await repo.group(CHAT_ID)).enabled
    assert (await repo.group(CHAT_ID)).cleanup_thread_id == 10


async def test_pre_delete_check_blocks_legacy_unscoped_task_in_newly_discovered_forum(app):
    cleaner, repo, api, _ = app
    await cleaner.register(message())
    await make_due(repo)
    api.forum_chats.add(CHAT_ID)  # No incoming update informed the bot about topics yet.
    await Scheduler(cleaner).tick()
    assert not api.called("deleteMessage")
    assert await repo.queue_size() == 0
    assert not (await repo.group(CHAT_ID)).enabled


async def test_pre_delete_rechecks_task_scope(app):
    cleaner, repo, api, db = app
    await select_topic(app, 10)
    await cleaner.register(forum_message())
    await make_due(repo)
    async with db.transaction() as connection:
        await connection.execute("UPDATE pending_deletions SET message_thread_id=20")
    await Scheduler(cleaner).tick()
    assert not api.called("deleteMessage")
    assert await repo.queue_size() == 0


async def test_turning_off_topics_does_not_broaden_cleanup(app):
    cleaner, repo, api, _ = app
    await select_topic(app, 10)
    await cleaner.register(forum_message())
    await make_due(repo)
    api.forum_chats.clear()
    await Scheduler(cleaner).tick()
    assert not api.called("deleteMessage")
    assert not (await repo.group(CHAT_ID)).enabled


async def test_general_topic_is_distinct_and_unknown_topic_is_preserved(app):
    cleaner, repo, api, _ = app
    await select_topic(app, 0)
    await cleaner.register(forum_message(1, None))
    await cleaner.register(forum_message(2, 10))
    malformed = forum_message(3, 10).model_copy(update={"message_thread_id": None})
    await cleaner.register(malformed)
    assert topic_id(malformed) is None
    assert await repo.queue_size() == 1
    await make_due(repo)
    await Scheduler(cleaner).tick()
    assert [call.message_id for call in api.called("deleteMessage")] == [1]


async def test_binding_and_queued_topic_survive_restart(tmp_path):
    path = tmp_path / "forum.db"
    db = await Database.open(path)
    repo = Repository(db)
    await repo.ensure_group(CHAT_ID, "music", True)
    await repo.set_enabled(CHAT_ID, True, 10)
    await repo.enqueue(CHAT_ID, 5, USER_ID, time.time() + 1, 10)
    await db.close()
    db = await Database.open(path)
    try:
        repo = Repository(db)
        group = await repo.group(CHAT_ID)
        assert group.is_forum and group.cleanup_thread_id == 10 and group.enabled
        task = (await repo.due(time.time() + 10000, 10))[0]
        assert task.message_thread_id == 10
    finally:
        await db.close()


async def test_v1_database_migrates_without_losing_settings_and_statistics(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE groups(chat_id INTEGER PRIMARY KEY,title TEXT NOT NULL,
              enabled INTEGER NOT NULL,delete_delay INTEGER NOT NULL,created_at REAL NOT NULL,
              updated_at REAL NOT NULL,enabled_at REAL NOT NULL DEFAULT 0);
            CREATE TABLE pending_deletions(id INTEGER PRIMARY KEY AUTOINCREMENT,
              chat_id INTEGER NOT NULL,message_id INTEGER NOT NULL,sender_user_id INTEGER NOT NULL,
              sent_at REAL NOT NULL,delete_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,UNIQUE(chat_id,message_id));
            CREATE TABLE statistics(chat_id INTEGER PRIMARY KEY,deleted_count INTEGER NOT NULL,
              failed_count INTEGER NOT NULL,last_deletion_at REAL);
            INSERT INTO groups VALUES(-1,'Old group',1,1800,1,1,1);
            INSERT INTO statistics VALUES(-1,7,2,9);
            INSERT INTO pending_deletions(chat_id,message_id,sender_user_id,sent_at,delete_at)
              VALUES(-1,5,101,100,1900);
            PRAGMA user_version=1;
        """)
    db = await Database.open(path)
    try:
        repo = Repository(db)
        group = await repo.group(-1)
        assert group.delete_delay == 1800 and group.enabled and not group.is_forum
        assert group.cleanup_thread_id is None
        assert (await repo.stats(-1))["deleted_count"] == 7
        assert (await repo.due(time.time(), 10))[0].message_thread_id is None
        async with db.connection.execute("PRAGMA user_version") as cursor:
            assert (await cursor.fetchone())[0] == 2
    finally:
        await db.close()


@pytest.mark.parametrize("sender,expected", [(ADMIN_ID, 30), (USER_ID, 10)])
async def test_enable_command_binds_actual_topic_and_checks_admin(app, sender, expected):
    cleaner, repo, api, _ = app
    await select_topic(app, 10)
    msg = forum_message(50, 30, sender, "/enable").model_copy(
        update={
            "entities": [MessageEntity(type="bot_command", offset=0, length=7)],
        }
    )
    await create_dispatcher(cleaner).feed_update(cleaner.bot, Update(update_id=50, message=msg))
    assert (await repo.group(CHAT_ID)).cleanup_thread_id == expected
    assert api.called("sendMessage")[-1].message_thread_id == 30


@pytest.mark.parametrize("sender,expected", [(ADMIN_ID, 40), (USER_ID, 10)])
async def test_callback_binds_panel_topic_not_supplied_chat_or_topic(app, sender, expected):
    cleaner, repo, _, _ = app
    await select_topic(app, 10)
    query = CallbackQuery(
        id="topic-query",
        from_user=User(id=sender, is_bot=False, first_name="Test"),
        chat_instance="instance",
        message=forum_message(900, 40, BOT_ID),
        data="ac:enable",
    )
    await create_dispatcher(cleaner).feed_update(
        cleaner.bot,
        Update(update_id=60, callback_query=query),
    )
    assert (await repo.group(CHAT_ID)).cleanup_thread_id == expected
