"""Telegram Bot API helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

# Telegram bot commands: lowercase letters, digits, underscores only (no hyphens).
FINANCE_RUN_COMMAND = "financerun"
STATUS_COMMAND = "status"
ADD_DEMO_COMMAND = "adddemo"

BOT_COMMANDS: list[dict[str, str]] = [
    {
        "command": FINANCE_RUN_COMMAND,
        "description": "Run one finance agent cycle (sheet, hotdata, nudge)",
    },
    {
        "command": ADD_DEMO_COMMAND,
        "description": "Append random demo transactions to the sheet",
    },
    {
        "command": STATUS_COMMAND,
        "description": "Show the active goal and recent run summary",
    },
]


class TelegramError(Exception):
    pass


@dataclass
class TelegramMessage:
    update_id: int
    chat_id: str
    text: str
    command: str | None = None
    command_args: str = ""


def parse_command(text: str) -> tuple[str | None, str]:
    """Return (command_name, args) for `/name args`, or (None, text) for plain text."""
    text = text.strip()
    if not text.startswith("/"):
        return None, text

    body = text[1:]
    if not body:
        return None, text

    # `/financerun@MyBotName` -> strip the @bot suffix Telegram adds in groups.
    if "@" in body:
        body = body.split("@", 1)[0]

    if " " in body:
        command, args = body.split(" ", 1)
        return command.lower(), args.strip()

    return body.lower(), ""


async def delete_webhook(bot_token: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/deleteWebhook"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json={"drop_pending_updates": False})
        data = response.json()
    if not data.get("ok"):
        raise TelegramError(data.get("description", "deleteWebhook failed"))


async def set_bot_commands(bot_token: str) -> None:
    """Register slash commands so they appear in Telegram's / menu."""
    url = f"https://api.telegram.org/bot{bot_token}/setMyCommands"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json={"commands": BOT_COMMANDS})
        data = response.json()
    if not data.get("ok"):
        raise TelegramError(data.get("description", "setMyCommands failed"))


async def fetch_updates(
    bot_token: str,
    offset: int = 0,
    *,
    timeout: int = 0,
) -> tuple[list[TelegramMessage], int]:
    """Pull pending updates and return parsed messages plus the next offset."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params: dict[str, int] = {"timeout": timeout}
    if offset:
        params["offset"] = offset

    async with httpx.AsyncClient(timeout=max(20.0, timeout + 5)) as client:
        response = await client.get(url, params=params)
        data = response.json()

    if not data.get("ok"):
        raise TelegramError(data.get("description", "getUpdates failed"))

    messages: list[TelegramMessage] = []
    next_offset = offset

    for update in data.get("result", []):
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            next_offset = max(next_offset, update_id + 1)

        raw = update.get("message") or update.get("edited_message") or {}
        text = (raw.get("text") or "").strip()
        chat = raw.get("chat") or {}
        chat_id = chat.get("id")
        if not text or chat_id is None:
            continue

        command, args = parse_command(text)
        messages.append(
            TelegramMessage(
                update_id=update_id if isinstance(update_id, int) else 0,
                chat_id=str(chat_id),
                text=text,
                command=command,
                command_args=args,
            )
        )

    return messages, next_offset


async def get_latest_chat_id(bot_token: str) -> str | None:
    """Return the chat ID from the most recent incoming message, if any."""
    await delete_webhook(bot_token)
    messages, _offset = await fetch_updates(bot_token, offset=0)
    if not messages:
        return None
    return messages[-1].chat_id


async def send_message(bot_token: str, chat_id: str, text: str) -> None:
    """Send a plain-text message. No parse_mode, so the text needs no escaping."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, json={"chat_id": chat_id, "text": text})
        data = response.json()
    if not data.get("ok"):
        raise TelegramError(data.get("description", "sendMessage failed"))


async def get_replies(
    bot_token: str,
    chat_id: str,
    offset: int = 0,
) -> tuple[list[str], int]:
    """Fetch new plain-text replies from this chat (bot commands are ignored)."""
    messages, next_offset = await fetch_updates(bot_token, offset)
    texts: list[str] = []
    for message in messages:
        if message.chat_id != str(chat_id):
            continue
        if message.command:
            continue
        texts.append(message.text)
    return texts, next_offset
