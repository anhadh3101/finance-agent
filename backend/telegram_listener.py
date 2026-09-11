"""Background Telegram polling: slash commands and queued user replies."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from config import Settings, get_settings
from run_state import append_pending_replies, get_run_state, save_tg_offset
from store import load_config
from telegram import (
    ADD_DEMO_COMMAND,
    FINANCE_RUN_COMMAND,
    STATUS_COMMAND,
    TelegramError,
    fetch_updates,
    send_message,
)

POLL_INTERVAL_SECONDS = 2.0
RunHandler = Callable[[], Awaitable[None]]
StatusHandler = Callable[[], Awaitable[str]]
AddDemoHandler = Callable[[str], Awaitable[None]]


async def register_bot_commands(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.telegram_bot_token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not configured in .env")

    from telegram import delete_webhook, set_bot_commands

    await delete_webhook(settings.telegram_bot_token)
    await set_bot_commands(settings.telegram_bot_token)


async def poll_once(
    *,
    on_finance_run: RunHandler | None = None,
    on_add_demo: AddDemoHandler | None = None,
    on_status: StatusHandler | None = None,
    settings: Settings | None = None,
) -> None:
    """Process one batch of Telegram updates."""
    settings = settings or get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    state = get_run_state()
    messages, next_offset = await fetch_updates(
        settings.telegram_bot_token,
        state["tg_offset"],
    )
    if next_offset != state["tg_offset"]:
        save_tg_offset(next_offset)

    authorized = str(settings.telegram_chat_id)
    pending: list[str] = []

    for message in messages:
        if message.chat_id != authorized:
            continue

        if message.command == FINANCE_RUN_COMMAND:
            if on_finance_run is None:
                continue
            try:
                await send_message(
                    settings.telegram_bot_token,
                    authorized,
                    "Starting finance run. This can take up to a minute.",
                )
            except TelegramError:
                pass
            asyncio.create_task(on_finance_run())
            continue

        if message.command == ADD_DEMO_COMMAND:
            if on_add_demo is None:
                continue
            asyncio.create_task(on_add_demo(message.command_args))
            continue

        if message.command == STATUS_COMMAND:
            if on_status is None:
                continue
            try:
                text = await on_status()
                await send_message(settings.telegram_bot_token, authorized, text)
            except TelegramError as exc:
                await send_message(
                    settings.telegram_bot_token,
                    authorized,
                    f"Could not load status: {exc}",
                )
            continue

        if message.command:
            # Unknown command — ignore rather than treating it as a preference reply.
            continue

        pending.append(message.text)

    if pending:
        append_pending_replies(pending)


async def poll_loop(
    *,
    on_finance_run: RunHandler,
    on_add_demo: AddDemoHandler,
    on_status: StatusHandler,
    settings: Settings | None = None,
) -> None:
    """Poll Telegram until cancelled. Only one consumer should run at a time."""
    settings = settings or get_settings()
    while True:
        try:
            if settings.telegram_bot_token and settings.telegram_chat_id and load_config():
                await poll_once(
                    on_finance_run=on_finance_run,
                    on_add_demo=on_add_demo,
                    on_status=on_status,
                    settings=settings,
                )
        except asyncio.CancelledError:
            raise
        except TelegramError:
            pass
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
