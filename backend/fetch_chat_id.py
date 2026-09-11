"""Fetch Telegram chat ID via getUpdates and write it to .env."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from config import ENV_PATH, get_settings
from telegram import TelegramError, get_latest_chat_id

CHAT_ID_PATTERN = re.compile(r"^TELEGRAM_CHAT_ID=.*$", re.MULTILINE)


def update_env_chat_id(chat_id: str) -> None:
    text = ENV_PATH.read_text(encoding="utf-8")
    line = f"TELEGRAM_CHAT_ID={chat_id}"
    if CHAT_ID_PATTERN.search(text):
        text = CHAT_ID_PATTERN.sub(line, text)
    else:
        text = text.rstrip() + f"\n{line}\n"
    ENV_PATH.write_text(text, encoding="utf-8")


async def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is missing from .env", file=sys.stderr)
        return 1

    try:
        chat_id = await get_latest_chat_id(settings.telegram_bot_token)
    except TelegramError as exc:
        print(f"Telegram API error: {exc}", file=sys.stderr)
        return 1

    if not chat_id:
        print(
            "No messages found. Open Telegram, message your bot once, then run this again.",
            file=sys.stderr,
        )
        return 1

    update_env_chat_id(chat_id)
    print(f"Saved TELEGRAM_CHAT_ID={chat_id} to {ENV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
