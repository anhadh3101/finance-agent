"""Command line surface for the Claude Code `finance-run` workflow.

Every call to a data source, memory store, or the Telegram API happens here, not in
the model's context. The workflow reads `context`, decides what to say, then calls
`send` and `record`. Secrets stay in .env and are never printed.

    context                      Print one JSON document with everything a run needs
    send --text "..."            Send one plain-text Telegram message
    record --mode ... [...]      Persist the run, nudge, and preferences
    reset                        Clear bookkeeping so the next run is a baseline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

# Cognee logs a banner to stderr on import. Quiet it so the workflow reads only JSON.
os.environ.setdefault("LOG_LEVEL", "ERROR")

from analysis import analyze  # noqa: E402
from config import get_settings  # noqa: E402
from goals import load_active_goal  # noqa: E402
from hydra import (  # noqa: E402
    HydraError,
    get_preferences,
    get_recent_nudges,
    record_nudge,
    record_preference,
)
from memory import CogneeError, add_history, recall_history  # noqa: E402
from query_engine import HOTDATA, EngineError, refresh  # noqa: E402
from run_state import (  # noqa: E402
    drain_pending_replies,
    get_run_state,
    reset_run_state,
    save_run_state,
    telegram_poller_active,
)
from sheet import SheetError, load_sheet  # noqa: E402
from store import load_config  # noqa: E402
from telegram import TelegramError, get_replies, send_message  # noqa: E402

PREFERENCE_KINDS = ("ignore", "protect", "keep")


def _fail(message: str) -> int:
    print(json.dumps({"error": message}), file=sys.stdout)
    return 1


def _emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, default=str))
    return 0


async def cmd_context(args: argparse.Namespace) -> int:
    settings = get_settings()
    config = load_config()
    if config is None:
        return _fail("No configuration saved. Open /dashboard and save a sheet URL and goal.")

    warnings: list[str] = []

    try:
        sheet_data = load_sheet(
            config.sheet_url,
            config.transactions_gid,
            config.budgets_gid,
        )
    except SheetError as exc:
        return _fail(f"Sheet: {exc}")
    warnings.extend(sheet_data.warnings)

    try:
        engine, handle = refresh(sheet_data, settings)
    except EngineError as exc:
        return _fail(f"Query engine: {exc}")
    if engine != HOTDATA:
        warnings.append(f"Queries ran on the local `{engine}` engine, not hotdata.")

    run_state = get_run_state()

    try:
        spend = analyze(handle, run_state["last_row"], engine=engine)
    except EngineError as exc:
        return _fail(f"Query engine: {exc}")

    replies: list[str] = []
    tg_offset = run_state["tg_offset"]
    if settings.telegram_bot_token and settings.telegram_chat_id:
        if telegram_poller_active():
            replies = drain_pending_replies()
        else:
            try:
                replies, tg_offset = await get_replies(
                    settings.telegram_bot_token,
                    settings.telegram_chat_id,
                    run_state["tg_offset"],
                )
            except TelegramError as exc:
                warnings.append(f"Could not read Telegram replies: {exc}")
    else:
        warnings.append("Telegram is not configured; no message will be sent.")

    goal = await load_active_goal()
    preferences = await get_preferences(settings)
    recent_nudges = await get_recent_nudges(settings=settings)

    history: list[Any] = []
    if args.with_history:
        try:
            history = await recall_history(
                "What spending patterns, preferences, and past nudges are known about this user?"
            )
        except CogneeError as exc:
            warnings.append(f"Cognee history unavailable: {exc}")

    return _emit(
        {
            "run_id": uuid.uuid4().hex[:12],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "engine": engine,
            "is_first_run": not run_state["has_run_before"],
            "as_of": spend["as_of"],
            "last_row": spend["last_row"],
            "max_row": spend["max_row"],
            "tg_offset": tg_offset,
            "new_row_count": spend["new_row_count"],
            "new_rows": spend["new_rows"],
            "spend_vs_budget": spend["spend_vs_budget"],
            "flags": spend["flags"],
            "goal": goal,
            "preferences": preferences,
            "recent_nudges": recent_nudges,
            "new_replies": replies,
            "cognee_history": [str(item) for item in history],
            "run_log": run_state["runs"][:5],
            "telegram_ready": bool(
                settings.telegram_bot_token and settings.telegram_chat_id
            ),
            "warnings": warnings,
        }
    )


async def cmd_send(args: argparse.Namespace) -> int:
    settings = get_settings()
    text = args.text.strip()
    if not text:
        return _fail("Message text is empty.")
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return _fail("Telegram is not configured in .env.")

    try:
        await send_message(settings.telegram_bot_token, settings.telegram_chat_id, text)
    except TelegramError as exc:
        return _fail(f"Telegram: {exc}")

    return _emit({"sent": True, "characters": len(text)})


def _parse_preferences(raw: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for entry in raw:
        parts = entry.split("|")
        if len(parts) < 2:
            raise ValueError(f"`{entry}` must look like kind|target|reason")
        kind = parts[0].strip().lower()
        if kind not in PREFERENCE_KINDS:
            raise ValueError(f"Preference kind must be one of {PREFERENCE_KINDS}, got `{kind}`")
        parsed.append(
            {
                "kind": kind,
                "target": parts[1].strip(),
                "reason": parts[2].strip() if len(parts) > 2 else "",
            }
        )
    return parsed


async def cmd_record(args: argparse.Namespace) -> int:
    settings = get_settings()

    try:
        preferences = _parse_preferences(args.preference or [])
    except ValueError as exc:
        return _fail(str(exc))

    flags = [flag for flag in (args.flags or "").split(",") if flag]
    categories = [category for category in (args.categories or "").split(",") if category]
    run_id = args.run_id or uuid.uuid4().hex[:12]

    saved_preferences: list[str] = []
    warnings: list[str] = []

    for preference in preferences:
        try:
            await record_preference(
                preference["kind"],
                preference["target"],
                preference["reason"],
                settings=settings,
            )
            saved_preferences.append(f"{preference['kind']}:{preference['target']}")
        except HydraError as exc:
            warnings.append(f"Could not save preference {preference['target']}: {exc}")

    run_entry = {
        "run_id": run_id,
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": args.mode,
        "new_rows": args.new_rows,
        "flags": flags,
        "sent": bool(args.message),
        "last_row": int(args.last_row),
    }

    state = get_run_state()
    warnings.extend(
        await save_run_state(
            last_row=int(args.last_row),
            tg_offset=int(args.tg_offset) if args.tg_offset is not None else state["tg_offset"],
            runs=[run_entry, *state["runs"]],
            settings=settings,
        )
    )

    if args.message:
        try:
            await record_nudge(
                run_id,
                args.message,
                flag_types=flags,
                categories=categories,
                settings=settings,
            )
        except HydraError as exc:
            warnings.append(f"Could not save the nudge: {exc}")

    cognee_written = False
    if not args.no_cognee:
        history_texts = [text for text in [args.summary, *(args.reply or [])] if text]
        if history_texts:
            try:
                cognee_written = await add_history(history_texts, settings)
            except CogneeError as exc:
                warnings.append(f"Cognee: {exc}")

    return _emit(
        {
            "recorded": True,
            "run_id": run_id,
            "mode": args.mode,
            "last_row": int(args.last_row),
            "preferences_saved": saved_preferences,
            "cognee_written": cognee_written,
            "warnings": warnings,
        }
    )


async def cmd_reset(_args: argparse.Namespace) -> int:
    """Clear local bookkeeping so the next run is a baseline again."""
    reset_run_state()
    return _emit({"reset": True, "next_run": "baseline"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finance_cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    context = subparsers.add_parser("context", help="Print the full run context as JSON")
    context.add_argument(
        "--with-history",
        action="store_true",
        help="Include a Cognee semantic search over past runs (slower)",
    )
    context.set_defaults(handler=cmd_context)

    send = subparsers.add_parser("send", help="Send one plain-text Telegram message")
    send.add_argument("--text", required=True)
    send.set_defaults(handler=cmd_send)

    record = subparsers.add_parser("record", help="Persist the run, nudge, and preferences")
    record.add_argument("--mode", required=True, choices=["baseline", "fresh", "skipped"])
    record.add_argument("--last-row", required=True, type=int)
    record.add_argument("--tg-offset", type=int, default=None)
    record.add_argument("--new-rows", type=int, default=0)
    record.add_argument("--flags", default="", help="Comma-separated flag types")
    record.add_argument("--categories", default="", help="Comma-separated flagged categories")
    record.add_argument("--message", default="", help="The message that was sent, if any")
    record.add_argument("--summary", default="", help="Run summary text for Cognee")
    record.add_argument("--reply", action="append", help="A user reply to remember (repeatable)")
    record.add_argument(
        "--preference",
        action="append",
        help="A preference as kind|target|reason, kind in ignore/protect/keep (repeatable)",
    )
    record.add_argument("--run-id", default="")
    record.add_argument(
        "--no-cognee",
        action="store_true",
        help="Skip the slow Cognee cognify step",
    )
    record.set_defaults(handler=cmd_record)

    # Deliberately absent from the workflow's allowed tools: resetting is a human choice.
    reset = subparsers.add_parser(
        "reset", help="Clear local run bookkeeping so the next run is a baseline"
    )
    reset.set_defaults(handler=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
