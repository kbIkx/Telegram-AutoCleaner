import asyncio
import time

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import DeleteMessage, GetChatMember
from aiogram.types import Chat, PhotoSize

from bot.database.connection import Database
from bot.database.repositories import Repository
from bot.services.scheduler import Scheduler
from tests.conftest import BOT_ID, CHAT_ID, USER_ID, make_due, message


@pytest.mark.parametrize("role", ["administrator", "creator"])
async def test_admins_never_queued(app, role):
    cleaner, repo, api, _ = app
    api.roles[(CHAT_ID, USER_ID)] = role
    await cleaner.register(message())
    assert await repo.queue_size() == 0


async def test_delay_and_deletion(app):
    cleaner, repo, api, _ = app
    msg = message()
    await cleaner.register(msg)
    tasks = await repo.due(time.time() + 100, 10)
    assert len(tasks) == 1
    assert tasks[0].delete_at == msg.date.timestamp() + 10
    scheduler = Scheduler(cleaner)
    await scheduler.tick()
    assert not api.called("deleteMessage")
    await make_due(repo)
    await scheduler.tick()
    assert len(api.called("deleteMessage")) == 1
    assert await repo.queue_size() == 0
    assert (await repo.stats(CHAT_ID))["deleted_count"] == 1


async def test_restart_preserves_queue_and_settings(tmp_path):
    path = tmp_path / "restart.db"
    db = await Database.open(path)
    repo = Repository(db)
    await repo.ensure_group(CHAT_ID, "Group")
    await repo.set_delay(CHAT_ID, 3600)
    await repo.set_enabled(CHAT_ID, True)
    await repo.enqueue(CHAT_ID, 5, USER_ID, time.time() + 1)
    await db.close()
    db = await Database.open(path)
    try:
        repo = Repository(db)
        assert await repo.queue_size() == 1
        assert (await repo.group(CHAT_ID)).delete_delay == 3600
        assert (await repo.group(CHAT_ID)).enabled
    finally:
        await db.close()


async def test_disable_enable_cutoff_and_cancellation(app):
    cleaner, repo, api, _ = app
    await cleaner.register(message())
    await make_due(repo)
    stale_task = (await repo.due(time.time(), 10))[0]
    await cleaner.set_enabled(CHAT_ID, False)
    assert await repo.queue_size() == 0
    await cleaner.process(stale_task.id, CHAT_ID)
    assert not api.called("deleteMessage")
    old_message = message(2)
    await cleaner.register(old_message)
    assert await repo.queue_size() == 0
    await cleaner.set_enabled(CHAT_ID, True)
    await cleaner.register(old_message)  # Backlog remains protected.
    assert await repo.queue_size() == 0
    await cleaner.register(message(3))
    assert await repo.queue_size() == 1


async def test_repeated_enable_does_not_reset_cutoff(app):
    cleaner, repo, _, _ = app
    await cleaner.register(message())
    before = (await repo.group(CHAT_ID)).enabled_at
    await cleaner.set_enabled(CHAT_ID, True)
    assert (await repo.group(CHAT_ID)).enabled_at == before
    assert await repo.queue_size() == 1


async def test_new_delay_preserves_existing_deadlines_and_groups(app):
    cleaner, repo, _, _ = app
    await cleaner.register(message())
    before = (await repo.due(time.time() + 100, 10))[0].delete_at
    await repo.ensure_group(-200, "Other")
    await cleaner.set_delay(CHAT_ID, 600)
    assert (await repo.group(-200)).delete_delay == 10
    assert (await repo.due(time.time() + 100, 10))[0].delete_at == before
    await cleaner.register(message(2))
    tasks = await repo.due(time.time() + 1000, 10)
    assert tasks[1].delete_at - tasks[1].sent_at == 600


async def test_duplicates_and_overlapping_ticks_do_not_repeat_delete(app):
    cleaner, repo, api, _ = app
    await asyncio.gather(*(cleaner.register(message()) for _ in range(4)))
    assert await repo.queue_size() == 1
    await make_due(repo)
    scheduler = Scheduler(cleaner)
    await asyncio.gather(scheduler.tick(), scheduler.tick())
    assert len(api.called("deleteMessage")) == 1


async def test_promotion_before_deletion(app):
    cleaner, repo, api, _ = app
    await cleaner.register(message())
    await make_due(repo)
    api.roles[(CHAT_ID, USER_ID)] = "administrator"
    await Scheduler(cleaner).tick()
    assert not api.called("deleteMessage")
    assert await repo.queue_size() == 0


@pytest.mark.parametrize("sender_chat", [CHAT_ID, -999999])
async def test_anonymous_or_channel_sender_preserved(app, sender_chat):
    cleaner, repo, _, _ = app
    await cleaner.register(message(sender_chat=Chat(id=sender_chat, type="supergroup")))
    assert await repo.queue_size() == 0


async def test_bot_and_service_and_unknown_messages_preserved(app):
    cleaner, repo, _, _ = app
    await cleaner.register(message(sender=BOT_ID))
    await cleaner.register(message(2, text=None, new_chat_title="New title"))
    await cleaner.register(message(3, text=None))
    assert await repo.queue_size() == 0


async def test_all_album_items_queued(app):
    cleaner, repo, _, _ = app
    for index in range(3):
        await cleaner.register(
            message(
                index + 1,
                text=None,
                media_group_id="album",
                photo=[
                    PhotoSize(
                        file_id=f"file{index}",
                        file_unique_id=f"unique{index}",
                        width=100,
                        height=100,
                    )
                ],
            )
        )
    assert await repo.queue_size() == 3


async def test_pin_event_and_late_pin_lookup_protect(app):
    cleaner, repo, api, _ = app
    await cleaner.register(message())
    await cleaner.register(message(2))
    await cleaner.register(message(10, text=None, pinned_message=message()))
    assert await repo.queue_size() == 1
    api.pins[CHAT_ID] = message(2)
    await make_due(repo, 2)
    await Scheduler(cleaner).tick()
    assert not api.called("deleteMessage")
    assert await repo.queue_size() == 0
    await cleaner.register(message())  # Reordered/replayed update cannot requeue a pin.
    assert await repo.queue_size() == 0


@pytest.mark.parametrize("error_kind", ["network", "retry", "forbidden", "not_found", "permanent"])
async def test_api_errors_do_not_stop_other_groups(app, error_kind):
    cleaner, repo, api, _ = app
    await repo.ensure_group(-200, "Other")
    await repo.set_enabled(-200, True)
    await cleaner.register(message())
    await cleaner.register(message(2, chat_id=-200))
    await make_due(repo)
    await make_due(repo, 2, -200)
    method = DeleteMessage(chat_id=CHAT_ID, message_id=1)
    errors = {
        "network": TelegramNetworkError(method, "network failure"),
        "retry": TelegramRetryAfter(method, "Flood control", retry_after=30),
        "forbidden": TelegramForbiddenError(method, "Forbidden"),
        "not_found": TelegramBadRequest(method, "Bad Request: message to delete not found"),
        "permanent": TelegramBadRequest(method, "Bad Request: message can't be deleted"),
    }
    api.errors[("deleteMessage", CHAT_ID, 1)].append(errors[error_kind])
    await Scheduler(cleaner).tick()
    assert (await repo.stats(-200))["deleted_count"] == 1
    assert (await repo.stats(CHAT_ID))["deleted_count"] == 0
    if error_kind in {"retry", "network"}:
        tasks = await repo.due(time.time() + 1000, 10)
        assert len(tasks) == 1 and tasks[0].attempts == 1
        if error_kind == "retry":
            assert tasks[0].delete_at >= time.time() + 29
        await make_due(repo)
        await Scheduler(cleaner).tick()
        assert (await repo.stats(CHAT_ID))["deleted_count"] == 1
    else:
        assert await repo.queue_size() == 0


async def test_unverifiable_author_retries_then_preserves(app):
    cleaner, repo, api, _ = app
    cleaner.max_attempts = 2
    await cleaner.register(message())
    for _ in range(2):
        await make_due(repo)
        api.errors[("getChatMember", CHAT_ID, USER_ID)].append(
            TelegramBadRequest(GetChatMember(chat_id=CHAT_ID, user_id=USER_ID), "user not found"),
        )
        await Scheduler(cleaner).tick()
    assert not api.called("deleteMessage")
    assert await repo.queue_size() == 0
    assert (await repo.stats(CHAT_ID))["failed_count"] == 1


async def test_expired_message_never_sent_to_api(app):
    cleaner, repo, api, db = app
    await cleaner.register(message())
    async with db.transaction() as connection:
        await connection.execute(
            "UPDATE pending_deletions SET sent_at=?, delete_at=?",
            (time.time() - 48 * 3600, time.time() - 1),
        )
    await Scheduler(cleaner).tick()
    assert not api.called("deleteMessage")
    assert (await repo.stats(CHAT_ID))["failed_count"] == 1


async def test_permissions_lost_disables_and_cancels(app):
    cleaner, repo, api, _ = app
    await cleaner.register(message())
    await cleaner.register(message(2))
    await make_due(repo)
    api.bot_can_delete = False
    await Scheduler(cleaner).tick()
    assert not (await repo.group(CHAT_ID)).enabled
    assert await repo.queue_size() == 0
    assert len(api.called("sendMessage")) == 1
    assert not await cleaner.set_enabled(CHAT_ID, True)


async def test_migration_moves_settings_stats_and_cancels_unsafe_old_ids(app):
    cleaner, repo, _, _ = app
    await cleaner.register(message())
    await cleaner.set_delay(CHAT_ID, 1800)
    await cleaner.migrate(CHAT_ID, -100999, "Supergroup")
    assert await repo.group(CHAT_ID) is None
    assert (await repo.group(-100999)).delete_delay == 1800
    assert (await repo.group(-100999)).enabled
    assert await repo.queue_size() == 0
    await cleaner.migrate(CHAT_ID, -100999, "Supergroup")
    assert (await repo.group(-100999)).enabled
    await cleaner.register(message(100))  # An old replay must not recreate the migrated chat.
    assert await repo.group(CHAT_ID) is None
