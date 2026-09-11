"""Register /financerun and /status with Telegram's command menu."""

from __future__ import annotations

import asyncio
import sys

from config import get_settings
from telegram import BOT_COMMANDS, TelegramError
from telegram_listener import register_bot_commands


async def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is missing from .env", file=sys.stderr)
        return 1

    try:
        await register_bot_commands(settings)
    except TelegramError as exc:
        print(f"Telegram API error: {exc}", file=sys.stderr)
        return 1

    print("Registered bot commands:")
    for command in BOT_COMMANDS:
        print(f"  /{command['command']} — {command['description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
