"""Finance Agent API — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError

from agent import AgentError, run_workflow
from analysis import analyze
from config import Settings, get_settings
from demo_data import demo_batch, random_transactions
from fetch_chat_id import update_env_chat_id
from goals import GoalSyncError, load_goal_memory, sync_active_goal
from hotdata_client import resolve_database_id
from hydra import HydraError, get_preferences
from memory import configure_cognee_env
from query_engine import HOTDATA, EngineError, engine_name, refresh
from run_state import (
    clear_telegram_poller_active,
    get_run_state,
    mark_telegram_poller_active,
)
from sheet import SheetError, load_sheet
from sheet_write import SheetWriteError, append_transactions
from store import AppConfig, GoalConfig, load_config, save_config, to_public
from telegram import TelegramError, get_latest_chat_id, send_message
from telegram_listener import poll_loop, register_bot_commands

_run_lock = asyncio.Lock()
_autorun_enabled = False
_autorun_task: asyncio.Task[None] | None = None
_telegram_task: asyncio.Task[None] | None = None
_last_result: dict[str, Any] | None = None
AUTORUN_INTERVAL_SECONDS = 120
DASHBOARD_PATH = Path(__file__).resolve().parent / "static" / "dashboard.html"


class ConfigPayload(BaseModel):
    sheet_url: str
    transactions_gid: str = "0"
    budgets_gid: str = "0"
    goal: str
    target_amount: float = Field(gt=0)


class FindChatIdPayload(BaseModel):
    pass


class AutorunPayload(BaseModel):
    enabled: bool


class SendMessagePayload(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class RunResult(BaseModel):
    mode: str
    message: str | None = None
    new_rows: int = 0
    flags: list[str] = Field(default_factory=list)
    preferences_learned: list[str] = Field(default_factory=list)
    error: str | None = None


def _to_run_result(payload: dict[str, Any]) -> RunResult:
    message = payload.get("message")
    return RunResult(
        mode=str(payload.get("mode", "unknown")),
        message=str(message) if message else None,
        new_rows=int(payload.get("new_rows") or 0),
        flags=[str(flag) for flag in payload.get("flags") or []],
        preferences_learned=[str(item) for item in payload.get("preferences_learned") or []],
        error=payload.get("error"),
    )


async def _execute_run() -> RunResult:
    """Run the workflow once under the lock, remembering the result for /api/state."""
    global _last_result
    async with _run_lock:
        payload = await run_workflow()
        _last_result = payload
        return _to_run_result(payload)


async def _telegram_finance_run() -> None:
    """Handle /financerun from Telegram without blocking the poll loop."""
    settings = get_settings()
    chat_id = settings.telegram_chat_id
    if not settings.telegram_bot_token or not chat_id:
        return

    if load_config() is None:
        await send_message(
            settings.telegram_bot_token,
            chat_id,
            "Save your sheet URL and goal on the dashboard before running.",
        )
        return

    if _run_lock.locked():
        await send_message(
            settings.telegram_bot_token,
            chat_id,
            "A run is already in progress. Try again in a minute.",
        )
        return

    try:
        result = await _execute_run()
    except AgentError as exc:
        await send_message(settings.telegram_bot_token, chat_id, f"Run failed: {exc}")
        return

    if result.error:
        await send_message(settings.telegram_bot_token, chat_id, f"Run failed: {result.error}")
        return

    # The workflow already sent the nudge via finance_cli send on fresh/baseline runs.
    if result.mode == "skipped":
        await send_message(
            settings.telegram_bot_token,
            chat_id,
            "No new transactions since the last run. Nothing to send.",
        )


async def _telegram_status_text() -> str:
    memory = await load_goal_memory()
    run_state = get_run_state()
    goal = (memory.get("goals") or [{}])[0]
    goal_line = goal.get("goal") or "No goal saved yet."
    target = goal.get("target_amount")
    target_line = f"Target bank balance: ${float(target):,.0f}" if target else ""

    runs = run_state.get("runs") or []
    if runs:
        latest = runs[0]
        run_line = (
            f"Last run: {latest.get('mode', '?')} "
            f"({latest.get('new_rows', 0)} new rows, "
            f"{'message sent' if latest.get('sent') else 'no message'})."
        )
    else:
        run_line = "No runs recorded yet."

    parts = [
        goal_line,
        target_line,
        run_line,
        "Send /financerun to trigger a cycle.",
        "Send /adddemo to append random transactions.",
    ]
    return "\n".join(part for part in parts if part)


async def _telegram_add_demo(args: str) -> None:
    """Handle /adddemo — append random or batch demo rows to the transactions tab."""
    settings = get_settings()
    chat_id = settings.telegram_chat_id
    if not settings.telegram_bot_token or not chat_id:
        return

    config = load_config()
    if config is None:
        await send_message(
            settings.telegram_bot_token,
            chat_id,
            "Save your sheet URL and goal on the dashboard before adding demo data.",
        )
        return

    try:
        sheet_data = await asyncio.to_thread(
            load_sheet,
            config.sheet_url,
            config.transactions_gid,
            config.budgets_gid,
        )
    except SheetError as exc:
        await send_message(
            settings.telegram_bot_token,
            chat_id,
            f"Could not read the sheet: {exc}",
        )
        return

    normalized = (args or "").strip().lower()
    try:
        if normalized.startswith("batch"):
            parts = normalized.split()
            batch_name = parts[1] if len(parts) > 1 else random.choice(["a", "b", "c"])
            rows = demo_batch(batch_name, sheet_data=sheet_data)
            label = f"batch {batch_name.upper()}"
        else:
            count = int(normalized) if normalized else random.randint(1, 3)
            count = max(1, min(count, 10))
            rows = random_transactions(count, sheet_data=sheet_data)
            label = f"{len(rows)} random row(s)"
    except ValueError as exc:
        await send_message(settings.telegram_bot_token, chat_id, str(exc))
        return

    try:
        written = await append_transactions(rows, config.sheet_url, settings)
    except SheetWriteError as exc:
        await send_message(
            settings.telegram_bot_token,
            chat_id,
            f"Could not append to the sheet: {exc}",
        )
        return

    lines = [
        f"Appended {written} demo row(s) ({label}):",
        *(
            f"- {row['date']} {row['merchant']} ${row['amount']} ({row['category']})"
            for row in rows
        ),
        "",
        "Send /financerun when you want the agent to analyze them.",
    ]
    await send_message(settings.telegram_bot_token, chat_id, "\n".join(lines))


async def _autorun_loop() -> None:
    while True:
        await asyncio.sleep(AUTORUN_INTERVAL_SECONDS)
        if not _autorun_enabled:
            return
        if _run_lock.locked() or load_config() is None:
            continue
        try:
            await _execute_run()
        except AgentError:
            # An autorun failure should not kill the loop; the next tick tries again.
            continue


def _require_agent_secret(
    x_agent_secret: str | None,
    settings: Settings,
) -> None:
    if not settings.agent_shared_secret:
        raise HTTPException(status_code=503, detail="AGENT_SHARED_SECRET is not configured")
    if x_agent_secret != settings.agent_shared_secret:
        raise HTTPException(status_code=401, detail="Invalid agent secret")


def _payload_to_config(payload: ConfigPayload) -> AppConfig:
    return AppConfig(
        sheet_url=payload.sheet_url.strip(),
        transactions_gid=payload.transactions_gid.strip() or "0",
        budgets_gid=payload.budgets_gid.strip() or "0",
        goal=GoalConfig(
            goal=payload.goal.strip(),
            target_amount=payload.target_amount,
        ),
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _telegram_task
    configure_cognee_env(get_settings())
    settings = get_settings()

    if settings.telegram_bot_token:
        try:
            await register_bot_commands(settings)
        except TelegramError:
            pass

        mark_telegram_poller_active()
        _telegram_task = asyncio.create_task(
            poll_loop(
                on_finance_run=_telegram_finance_run,
                on_add_demo=_telegram_add_demo,
                on_status=_telegram_status_text,
                settings=settings,
            )
        )

    yield

    global _autorun_task
    if _autorun_task is not None:
        _autorun_task.cancel()
        _autorun_task = None
    if _telegram_task is not None:
        _telegram_task.cancel()
        _telegram_task = None
    clear_telegram_poller_active()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Finance Agent API",
        description="Spending coach backend — sheet, memory, Telegram, and agent runs.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard")

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(DASHBOARD_PATH)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        config = load_config()
        if config is None:
            raise HTTPException(status_code=404, detail="No configuration saved yet.")
        return to_public(config).model_dump()

    @app.post("/api/config")
    async def save_user_config(payload: ConfigPayload) -> dict[str, str]:
        try:
            config = _payload_to_config(payload)
        except (ValueError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                detail = "; ".join(error["msg"] for error in exc.errors())
            else:
                detail = str(exc)
            raise HTTPException(status_code=400, detail=detail) from exc

        try:
            await sync_active_goal(config.goal.goal, config.goal.target_amount)
        except GoalSyncError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        save_config(config)
        return {"status": "saved"}

    @app.post("/api/register-telegram-commands")
    async def register_telegram_commands() -> dict[str, str]:
        if not settings.telegram_bot_token:
            raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN is not configured in .env")

        try:
            await register_bot_commands(settings)
        except TelegramError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return {"status": "registered"}

    @app.post("/api/find-chat-id")
    async def find_chat_id(_payload: FindChatIdPayload) -> dict[str, str]:
        if not settings.telegram_bot_token:
            raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN is not configured in .env")

        try:
            chat_id = await get_latest_chat_id(settings.telegram_bot_token)
        except TelegramError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if not chat_id:
            raise HTTPException(
                status_code=404,
                detail="No messages found. Message your bot on Telegram once, then try again.",
            )

        update_env_chat_id(chat_id)
        get_settings.cache_clear()
        return {"chat_id": chat_id}

    @app.post("/api/run", response_model=RunResult)
    async def run_agent() -> RunResult:
        if load_config() is None:
            raise HTTPException(status_code=400, detail="Save configuration before running the agent.")

        if _run_lock.locked():
            raise HTTPException(status_code=409, detail="A run is already in progress")

        try:
            return await _execute_run()
        except AgentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/autorun")
    async def set_autorun(payload: AutorunPayload) -> dict[str, bool]:
        global _autorun_enabled, _autorun_task
        _autorun_enabled = payload.enabled

        if _autorun_enabled and (_autorun_task is None or _autorun_task.done()):
            _autorun_task = asyncio.create_task(_autorun_loop())
        elif not _autorun_enabled and _autorun_task is not None:
            _autorun_task.cancel()
            _autorun_task = None

        return {"enabled": _autorun_enabled}

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        memory = await load_goal_memory()
        run_state = get_run_state()

        try:
            memory["preferences"] = await get_preferences()
        except HydraError as exc:
            raise HTTPException(status_code=502, detail=f"HydraDB: {exc}") from exc

        return {
            "memory": memory,
            "runs": run_state["runs"],
            "last_row": run_state["last_row"],
            "autorun": _autorun_enabled,
            "last_message": (_last_result or {}).get("message"),
            "last_result": _last_result,
        }

    @app.post("/tools/refresh-data")
    async def refresh_data(
        x_agent_secret: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Reload the sheet into hotdata. The workflow does this itself; this is for debugging."""
        _require_agent_secret(x_agent_secret, settings)
        config = load_config()
        if config is None:
            raise HTTPException(status_code=400, detail="Save configuration first.")

        try:
            data = await asyncio.to_thread(
                load_sheet, config.sheet_url, config.transactions_gid, config.budgets_gid
            )
            engine, handle = await asyncio.to_thread(refresh, data)
        except (SheetError, EngineError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return {
            "status": "loaded",
            "engine": engine,
            "handle": handle,
            "transactions": len(data.transactions),
            "budgets": len(data.budgets),
            "as_of": data.as_of.isoformat(),
            "warnings": data.warnings,
        }

    @app.post("/tools/spend-analysis")
    async def spend_analysis(
        last_row: int = 0,
        x_agent_secret: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Re-run the queries against whatever is already loaded. Call refresh-data first."""
        _require_agent_secret(x_agent_secret, settings)
        try:
            engine = engine_name(settings)
            handle = (
                await asyncio.to_thread(resolve_database_id) if engine == HOTDATA else engine
            )
            return await asyncio.to_thread(analyze, handle, last_row, engine)
        except EngineError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/tools/memory")
    async def load_memory(
        x_agent_secret: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_agent_secret(x_agent_secret, settings)
        memory = await load_goal_memory()
        memory["preferences"] = await get_preferences()
        return memory

    @app.post("/tools/send-telegram")
    async def send_telegram(
        payload: SendMessagePayload,
        x_agent_secret: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_agent_secret(x_agent_secret, settings)
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            raise HTTPException(status_code=503, detail="Telegram is not configured in .env")

        try:
            await send_message(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                payload.text,
            )
        except TelegramError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return {"ok": True}

    return app


app = create_app()
