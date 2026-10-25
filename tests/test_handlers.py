import time

import pytest
from aiogram.types import CallbackQuery, MessageEntity, Update, User

from bot.main import create_dispatcher
from tests.conftest import ADMIN_ID, BOT_ID, CHAT_ID, USER_ID, message


async def send_command(cleaner, text, sender=ADMIN_ID, chat_id=CHAT_ID, update_id=1):
    msg = message(
        update_id,
        sender=sender,
        chat_id=chat_id,
        text=text,
        entities=[MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))],
    )
    await create_dispatcher(cleaner).feed_update(
        cleaner.bot, Update(update_id=update_id, message=msg)
    )


async def click(cleaner, action, sender=ADMIN_ID, chat_id=CHAT_ID, panel_sender=BOT_ID):
    query = CallbackQuery(
        id="query",
        from_user=User(id=sender, is_bot=False, first_name="Test"),
        chat_instance="instance",
        message=message(900, sender=panel_sender, chat_id=chat_id),
        data=f"ac:{action}",
    )
    await create_dispatcher(cleaner).feed_update(
        cleaner.bot,
        Update(update_id=100, callback_query=query),
    )


@pytest.mark.parametrize(
    "command", ["/disable", "/enable", "/settime 30s", "/settings", "/status"]
)
async def test_member_cannot_manage_or_read_data(app, command):
    cleaner, repo, api, _ = app
    before = await repo.group(CHAT_ID)
    await send_command(cleaner, command, sender=USER_ID)
    after = await repo.group(CHAT_ID)
    assert after.enabled == before.enabled
    assert after.delete_delay == before.delete_delay
    assert all("Управление доступно только" in call.text for call in api.called("sendMessage"))
    assert not api.called("editMessageText")


@pytest.mark.parametrize("value", ["", "0s", "9s", "48h", "-1m", "abc", "1d", "1.5h", "30m 1h"])
async def test_settime_invalid_rejected_by_command(app, value):
    cleaner, repo, api, _ = app
    await send_command(cleaner, f"/settime {value}")
    assert (await repo.group(CHAT_ID)).delete_delay == 10
    assert api.called("sendMessage")


async def test_command_disable_and_enable(app):
    cleaner, repo, api, _ = app
    await cleaner.register(message())
    await send_command(cleaner, "/disable")
    assert not (await repo.group(CHAT_ID)).enabled
    assert await repo.queue_size() == 0
    await send_command(cleaner, "/enable", update_id=2)
    assert (await repo.group(CHAT_ID)).enabled
    assert len(api.called("getChatMember")) >= 3


@pytest.mark.parametrize("action", ["disable", "enable", "delay:1800", "stats", "home", "help"])
async def test_callbacks_recheck_admin_each_time(app, action):
    cleaner, repo, api, _ = app
    await click(cleaner, action, sender=USER_ID)
    assert (await repo.group(CHAT_ID)).enabled
    assert (await repo.group(CHAT_ID)).delete_delay == 10
    assert not api.called("editMessageText")
    assert api.called("answerCallbackQuery")[-1].show_alert
    assert any(call.user_id == USER_ID for call in api.called("getChatMember"))


@pytest.mark.parametrize(
    "action", ["home", "time", "delay:60", "stats", "help", "disable", "enable"]
)
async def test_admin_callbacks_edit_existing_panel(app, action):
    cleaner, repo, api, _ = app
    await click(cleaner, action)
    assert len(api.called("editMessageText")) == 1
    assert not api.called("sendMessage")
    if action == "delay:60":
        assert (await repo.group(CHAT_ID)).delete_delay == 60


async def test_callback_cannot_supply_foreign_chat_or_spoof_panel(app):
    cleaner, repo, api, _ = app
    await click(cleaner, f"disable:{CHAT_ID}")
    await click(cleaner, "disable", panel_sender=USER_ID)
    assert (await repo.group(CHAT_ID)).enabled
    assert not api.called("editMessageText")


async def test_demoted_admin_cannot_use_old_panel(app):
    cleaner, repo, api, _ = app
    await click(cleaner, "home")
    api.roles[(CHAT_ID, ADMIN_ID)] = "member"
    await click(cleaner, "disable")
    assert (await repo.group(CHAT_ID)).enabled
    assert len(api.called("editMessageText")) == 1


async def test_bot_removed_cancels_group_queue(app):
    cleaner, repo, _, _ = app
    await cleaner.register(message())
    user = {"id": BOT_ID, "is_bot": True, "first_name": "Bot"}
    update = Update.model_validate(
        {
            "update_id": 55,
            "my_chat_member": {
                "chat": {"id": CHAT_ID, "type": "supergroup", "title": "Group"},
                "from": {"id": ADMIN_ID, "is_bot": False, "first_name": "Admin"},
                "date": int(time.time()),
                "old_chat_member": {"status": "member", "user": user},
                "new_chat_member": {"status": "left", "user": user},
            },
        }
    )
    await create_dispatcher(cleaner).feed_update(cleaner.bot, update)
    assert not (await repo.group(CHAT_ID)).enabled
    assert await repo.queue_size() == 0


async def test_admin_settime_and_panel(app):
    cleaner, repo, api, _ = app
    await send_command(cleaner, "/settime 30m")
    assert (await repo.group(CHAT_ID)).delete_delay == 1800
    await send_command(cleaner, "/settings", update_id=2)
    assert api.called("sendMessage")[-1].reply_markup


async def test_admin_api_failure_denies_command(app):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import GetChatMember

    cleaner, repo, api, _ = app
    api.errors[("getChatMember", CHAT_ID, ADMIN_ID)].extend(
        [
            TelegramNetworkError(GetChatMember(chat_id=CHAT_ID, user_id=ADMIN_ID), "offline")
            for _ in range(2)
        ]
    )
    await send_command(cleaner, "/disable")
    assert (await repo.group(CHAT_ID)).enabled
    assert "Не удалось проверить" in api.called("sendMessage")[-1].text
