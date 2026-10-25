# Telegram AutoCleaner

An asynchronous Telegram bot built with Python 3.12+ and aiogram 3. It removes group members' messages after a configurable delay while preserving messages from administrators and the group owner. The bot's user interface is in Russian.

## Forum topics (version 1.1)

In a forum supergroup, cleanup is restricted to **one explicitly selected topic**. To clean only the **Information** topic, open it and run these commands there:

```text
/settime@kbIk_AutoCleaner_bot 30m
/enable@kbIk_AutoCleaner_bot
/status@kbIk_AutoCleaner_bot
```

The status should show `Scope: This topic only (#...)`. Messages from other topics, including General, are not queued. In a group without topics, cleanup covers the whole group.

To move cleanup, run `/enable` in a different topic. The previous queue is cancelled, and only new messages in the newly selected topic are processed. Running `/enable` again in the same topic preserves existing deadlines. `/disable` stops cleanup and cancels the group's queue. Only one topic per supergroup can be active at a time.

When a previously configured group is detected as a forum, cleanup is disabled and the old queue (which has no topic information) is cancelled. Nothing is deleted until an administrator explicitly chooses a topic. Scope is checked both when a message is received and immediately before deletion. A forum-mode change also disables cleanup. If the topic context cannot be verified, the message is kept.

Topic IDs are taken from the actual Telegram message or panel, never from a user-supplied `chat_id` or callback parameter. Replies and settings panels stay in the current topic. The database migrates automatically from schema version 1 to version 2 while preserving settings and statistics.

## Quick start on Windows 10/11

1. Install Python 3.12 or later from [python.org](https://www.python.org/downloads/). Select **Add Python to PATH** in the installer and verify with `python --version`.
2. Extract the project to a permanent folder, such as `C:\Bots\AutoCleaner`, and open PowerShell in that folder.
3. Create a virtual environment and install the pinned dependencies:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-lock.txt
   Copy-Item .env.example .env
   notepad .env
   ```

4. Open the official [@BotFather](https://t.me/BotFather) in Telegram, run `/newbot`, and choose a bot name and username. Put the token from BotFather in `BOT_TOKEN` in `.env`. Never send the token to anyone or put it on a command line.
5. Start the bot:

   ```powershell
   .\.venv\Scripts\python.exe run.py
   ```

   If the virtual environment is already active, `python run.py` is sufficient. PowerShell activation is optional; there is no need to change the ExecutionPolicy.
6. Add the bot to the group and make it an administrator with **Delete messages** permission. From your own Telegram administrator account, run:

   ```text
   /settime 30m
   /enable
   /settings
   ```

Cleanup is **disabled by default** in every new group. Nothing is deleted until `/enable` is run. Stop the process with Ctrl+C. The pending queue is restored from SQLite after restart.

## Receiving messages and checking permissions

The bot must remain an administrator. This allows it to receive group messages and makes `getChatMember` checks more reliable. In BotFather, you can run `/setprivacy`, select the bot, and choose **Disable**. If needed, remove and re-add the bot after changing the privacy setting. Administrators receive group messages even when privacy mode is enabled, but administrator status is still required.

Every administrative command and every button press rechecks the user's current permissions through the Telegram API. Commands sent on behalf of a group or channel are not accepted; send them from your personal account. A group cannot be managed through a private chat or a supplied `chat_id`.

If the bot loses its required permissions, cleanup is disabled and that group's queue is cancelled. The bot posts a warning in the group if it still has permission to send messages. A private notification cannot be guaranteed unless the administrator has opened a private chat with the bot. After restoring the permissions, an administrator must run `/enable` again.

## Commands and settings panel

| Command | Action |
| --- | --- |
| `/start` | Greeting and group-add link; help when used in a group |
| `/help` | Help |
| `/settings` | Panel with delay, state, queue, and buttons |
| `/settime 30s` | Delete new messages after 30 seconds |
| `/settime 5m` | Delete after 5 minutes |
| `/settime 1h` | Delete after 1 hour |
| `/enable` | Enable cleanup for new messages |
| `/disable` | Disable cleanup and cancel the group's queue |
| `/status` | Show state, permissions, queue, and statistics |

The allowed delay is **10 seconds to 47 hours**: one integer followed by `s`, `m`, or `h`, for example `45s`, `90m`, or `47h`. Compound and fractional intervals are rejected. The panel offers 30 seconds, 1/5/15/30 minutes, and 1/6/12/24 hours. Buttons edit the existing panel message. Queue details and statistics are visible only to administrators.

Changing the delay does not change deadlines already in the queue. The timer starts from the Telegram message timestamp, not when the bot receives the update. On enable, older messages are not scanned or queued, including messages sent while cleanup was disabled. Telegram timestamps have one-second precision, so a message timestamped in the same second as enabling may be kept; this boundary case is handled conservatively.

## Supported messages and exclusions

Text, photos, videos, voice messages, video notes, documents, stickers, animations, audio, polls, contacts, locations, and dice are supported. Forwarded messages and replies are handled according to the author of the current message. Each item in an album is handled as a separate message and gets its own queue entry. Service messages and unknown message types are ignored.

Messages from the bot, anonymous administrators, channels, and chats are kept. A user's status is checked on receipt and again before deletion; if they have become an administrator or owner, the task is cancelled. If the sender cannot be checked initially, the message is not queued. If a queued sender cannot be checked later, the bot makes a limited number of retries and then keeps the message.

## Pinned messages and Telegram API limitations

When the bot receives a pin event, it removes that message from the queue and records its ID in a separate protection table. Before every deletion, it also calls `getChat` and protects the latest pinned message visible to the Bot API. The protection survives restarts and remains in effect conservatively even after unpinning. Pending Telegram updates are processed before the scheduler starts.

**Telegram does not allow the bot to guarantee protection for every pinned message.** The Bot API does not expose a complete list of pinned messages or allow reading an arbitrary message by ID. A pin event may not arrive, and Telegram retains updates for no more than 24 hours. Permissions or pin status can also change between the check and deletion; the API has no atomic “check and delete” operation. Publish important permanent announcements as an administrator. To pin a member's message, run `/disable` first, pin it, then enable cleanup again. The cancelled queue is not restored.

Telegram generally refuses to delete messages older than 48 hours. The bot uses a 60-second safety margin and discards expired tasks. Actual deletion may be delayed by load, network conditions, or API limits.

When a group migrates to a supergroup, settings and statistics are carried over, and both migration service events and `TelegramMigrateToChat` are handled. Tasks for the old chat are cancelled because old `message_id` values cannot safely be used in the new chat. Cleanup continues for new messages using the new `chat_id`; old messages are intentionally left untouched.

The bot cannot recover history that Telegram did not send and is not an archive. Do not run multiple instances with the same token.

## Configuration

The bot reads `.env` from the project root regardless of the current working directory. `BOT_TOKEN` is read only from this file. Other values are also loaded from `.env`; relative paths are resolved from the project root.

| Variable | Default | Description |
| --- | --- | --- |
| `BOT_TOKEN` | required | Token from BotFather |
| `DATABASE_PATH` | `data/autocleaner.db` | Local SQLite database path |
| `LOG_LEVEL` | `INFO` | Logging level |
| `DEFAULT_DELETE_DELAY` | `1800` | Default delay for new groups, in seconds |
| `LOG_PATH` | `logs/autocleaner.log` | Log file path |
| `POLL_INTERVAL` | `1` | Queue polling interval, 0.1–60 seconds |
| `BATCH_SIZE` | `30` | Maximum batch size, 1–100 tasks |
| `MAX_CONCURRENT_DELETIONS` | `3` | Concurrent workers, 1–10 |
| `MAX_ATTEMPTS` | `5` | Maximum failed attempts, 1–20 |
| `API_REQUESTS_PER_SECOND` | `15` | Shared API request rate, 0.1–25 |

Direct dependencies are listed in `requirements.txt`; the complete transitive dependency set and SHA-256 hashes are pinned in `requirements-lock.txt`. The lock file is recommended for installation. You can also install with `pip install -r requirements.txt`. Regenerate the lock file and run the test suite when updating dependencies.

`.env`, SQLite databases, and logs are excluded from Git. Never publish `.env`. The database stores no message text, media, or captions; it stores IDs, timestamps, settings, counters, and safe error codes. The group title is stored for the interface. Logs contain operational events and IDs, but no message content, tokens, or raw API exceptions. Log rotation keeps up to five 5 MiB archives plus the current file.

## Project layout and reliability

```text
AutoCleaner/
├── bot/
│   ├── main.py                  # Lifecycle and long polling
│   ├── config.py                # .env loading and validation
│   ├── handlers/                # Commands, callbacks, messages, bot permissions
│   ├── keyboards/               # Inline keyboards
│   ├── services/                # Permission checks, cleanup, scheduler, API limiter
│   ├── database/                # SQLite, transactions, repositories
│   └── utils/                   # Time, logging, and process lock
├── tests/                       # Tests without a live Telegram bot
├── deploy/autocleaner.service
├── data/                        # Created automatically
├── logs/                        # Created automatically
├── .env.example
├── requirements.txt
├── requirements-lock.txt
├── requirements-dev.txt
├── TEST_REPORT.md
└── run.py
```

The queue is stored in SQLite with WAL mode, foreign keys, a unique `(chat_id, message_id)` pair, and a deadline index. Settings changes, queue cancellation, and counter updates use transactions. Timestamps are UTC Unix time, and the schema is versioned and created automatically. One process can serve multiple independent groups. A lock file beside the database prevents two processes from using the same database; the operating system releases the lock after a crash. Multiple databases or computers using the same token are not supported. Keep SQLite on a local disk, not a network filesystem.

The scheduler reads small batches and does not create a sleeping task for every message. Concurrent deletions are bounded, and a shared API limiter controls both rate and concurrency, including command requests. `RetryAfter` postpones the affected task and activates a shared cooldown. Network and server errors are retried with increasing delays. When attempts are exhausted, the message is kept and the task is removed. A failure in one task does not stop the others. A per-group lock coordinates deletion with disabling cleanup and pin protection; once `/disable` completes, queued deletions for that group no longer run.

The deletion counter increments only after `deleteMessage` succeeds; a message that is already gone counts as skipped. `failed_count` counts tasks that ultimately could not be processed, including expired tasks and exhausted retries. Cancellation, an administrator promotion, a pin, or an already-missing message does not increase that counter.

If the process crashes after a successful Telegram request but before the SQLite update, the idempotent deletion may be attempted again. The queue survives, but the successful deletion may not appear in the counter. A transaction spanning Telegram and local SQLite is not possible.

## Run on Linux

Install Python 3.12+ and your distribution's venv package, then run:

```bash
cd /path/to/AutoCleaner
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --require-hashes -r requirements-lock.txt
cp .env.example .env
chmod 600 .env
nano .env
.venv/bin/python run.py
```

### Run continuously with systemd

This example uses `/opt/autocleaner` and a dedicated user:

```bash
sudo useradd --system --home /opt/autocleaner --shell /usr/sbin/nologin autocleaner
sudo mkdir -p /opt/autocleaner
# Copy the project to /opt/autocleaner, configure .env, and create .venv.
sudo mkdir -p /opt/autocleaner/data /opt/autocleaner/logs
sudo chown -R root:autocleaner /opt/autocleaner
sudo chmod 640 /opt/autocleaner/.env
sudo chown -R autocleaner:autocleaner /opt/autocleaner/data /opt/autocleaner/logs
sudo cp /opt/autocleaner/deploy/autocleaner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autocleaner
sudo systemctl status autocleaner
sudo journalctl -u autocleaner -f
```

The unit restarts the process after a failure and allows writes only to `data/` and `logs/`. Paths in `.env` must match these directories; adjust `ReadWritePaths` if they differ. After changing `.env`, run `sudo systemctl restart autocleaner`. The virtual-environment Python must be accessible to the service user and must not be under `/home`, which is restricted by `ProtectHome`.

### Run continuously on Windows

In Task Scheduler, create a task triggered **At system startup**, set it to run whether or not a user is logged in, and use:

- Program: `C:\Bots\AutoCleaner\.venv\Scripts\python.exe`
- Arguments: `C:\Bots\AutoCleaner\run.py`
- Start in: `C:\Bots\AutoCleaner`

Configure a one-minute restart after failure, disable the execution time limit, and select **Do not start a new instance** if the task is already running. The task account must be able to read `.env` and write to `data/` and `logs/`. Keep the computer powered on, awake, and connected to Telegram. WinSW can also run the bot as a service; on an always-on Linux server, use systemd.

Stop the bot before making a backup, then copy `data/` and `.env` to a secure location. Do not copy only the main `.db` file from a running SQLite database without its WAL; stopping the bot ensures a consistent backup.

## Tests and local checks

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q bot run.py tests
python -m ruff check .
python -m ruff format --check .
python -m pip check
```

Tests use a mocked Telegram HTTP session and real temporary SQLite databases. No test requires a token, network access, or a live group. They cover the critical requirements, command/callback routing, and startup/shutdown. Results are in `TEST_REPORT.md`. A real group and Linux systemd were not tested in this environment.

Before using the bot in a real group, test it in a separate test group: `/settime 10s`, `/enable`, a member message, an administrator message, a photo album, `/status`, a restart with a pending message, and `/disable`. Then set the intended delay.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Process exits immediately | Python 3.12+, dependencies, `.env`, token format, network, and directory permissions |
| Member messages are not received | Bot administrator status; check BotFather privacy mode and re-add the bot |
| Nothing is deleted | `/status`, delete permission, and `/enable`; old messages are not processed |
| A message was kept | Sender is an administrator or unknown, it is pinned, permissions could not be checked, it expired, or retries were exhausted |
| Deletion is late | Queue load, API limits, or temporary network issues; check logs and `/status` |
| `permissions_lost` in the log | Restore permissions and run `/enable`; the old queue was cancelled |
| `worker_error` / `scheduler_error` | Disk space, SQLite permissions, and local disk; fix the cause and restart |
| A second instance will not start | Stop the old process; do not remove a lock file while its process is running |
| Polling fails or conflicts | Stop other processes using the token; startup removes the webhook without discarding updates |
| Some history is missing after a long outage | Telegram retains updates for up to 24 hours; expired messages are kept |

If a token is invalid or revoked, obtain a new one from BotFather and replace it in `.env`. During a long network outage, the service may exit during initial startup and systemd/Task Scheduler will retry it. After a successful start, aiogram retries long polling automatically.

## Development references

Documentation checked on October 25, 2026:

- [aiogram 3.31: Dispatcher and long polling](https://docs.aiogram.dev/en/latest/dispatcher/dispatcher.html)
- [Telegram Bot API: deleteMessage](https://core.telegram.org/bots/api#deletemessage)
- [Telegram Bot API: getChatMember](https://core.telegram.org/bots/api#getchatmember)
- [Telegram Bot API: ChatFullInfo and the latest pinned message](https://core.telegram.org/bots/api#chatfullinfo)
- [Telegram Bot API: receiving updates](https://core.telegram.org/bots/api#getting-updates)
- [Telegram FAQ: messages and privacy mode](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get)
