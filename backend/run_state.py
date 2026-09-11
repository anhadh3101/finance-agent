"""Run bookkeeping: `last_row`, the Telegram offset, and the run log.

A local JSON file is authoritative here, deliberately. `last_row` decides whether a
transaction has already been nudged about, so a stale read means either a duplicate
message or a missed one. HydraDB's ingest is an upsert over an indexed store and was
observed returning the previous value on an immediate read-back, which is fine for
memory but not for a counter the next run depends on.

Every write is still mirrored into HydraDB so the run log lives alongside the goal,
preferences, and nudges. The mirror is best-effort: a failure there never blocks a run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import Settings
from hydra import HydraError, mirror_run_state

STATE_PATH = Path(__file__).resolve().parent.parent / "run_state.json"
MAX_RUN_LOG = 20

EMPTY_STATE: dict[str, Any] = {
    "last_row": 0,
    "tg_offset": 0,
    "runs": [],
    "pending_replies": [],
    "has_run_before": False,
}

POLLER_FLAG_PATH = Path(__file__).resolve().parent.parent / ".telegram_poller"


def _read_state_file() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return dict(EMPTY_STATE)
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(EMPTY_STATE)
    return data if isinstance(data, dict) else dict(EMPTY_STATE)


def get_run_state() -> dict[str, Any]:
    """Read the current bookkeeping. Missing or corrupt state reads as a first run."""
    data = _read_state_file()
    runs = data.get("runs")
    runs = list(runs)[:MAX_RUN_LOG] if isinstance(runs, list) else []
    pending = data.get("pending_replies")
    pending = [str(item) for item in pending] if isinstance(pending, list) else []

    return {
        "last_row": int(data.get("last_row", 0) or 0),
        "tg_offset": int(data.get("tg_offset", 0) or 0),
        "runs": runs,
        "pending_replies": pending,
        "has_run_before": bool(runs),
    }


def telegram_poller_active() -> bool:
    """True when the FastAPI process owns Telegram polling."""
    return POLLER_FLAG_PATH.exists()


def mark_telegram_poller_active() -> None:
    POLLER_FLAG_PATH.write_text("1", encoding="utf-8")


def clear_telegram_poller_active() -> None:
    POLLER_FLAG_PATH.unlink(missing_ok=True)


def save_tg_offset(tg_offset: int) -> None:
    """Advance the Telegram update cursor without touching other bookkeeping."""
    data = _read_state_file()
    data["tg_offset"] = int(tg_offset)
    if "runs" not in data:
        data["runs"] = []
    if "last_row" not in data:
        data["last_row"] = 0
    STATE_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def append_pending_replies(texts: list[str]) -> None:
    """Queue user replies for the next workflow context fetch."""
    cleaned = [text.strip() for text in texts if text and text.strip()]
    if not cleaned:
        return
    data = _read_state_file()
    pending = data.get("pending_replies")
    pending = [str(item) for item in pending] if isinstance(pending, list) else []
    pending.extend(cleaned)
    data["pending_replies"] = pending[-50:]
    if "runs" not in data:
        data["runs"] = []
    if "last_row" not in data:
        data["last_row"] = 0
    if "tg_offset" not in data:
        data["tg_offset"] = 0
    STATE_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def drain_pending_replies() -> list[str]:
    """Return and clear queued replies."""
    data = _read_state_file()
    pending = data.get("pending_replies")
    pending = [str(item) for item in pending] if isinstance(pending, list) else []
    data["pending_replies"] = []
    STATE_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return pending


async def save_run_state(
    *,
    last_row: int,
    tg_offset: int,
    runs: list[dict[str, Any]],
    settings: Settings | None = None,
) -> list[str]:
    """Write bookkeeping locally, then mirror it to HydraDB. Returns any warnings."""
    trimmed = runs[:MAX_RUN_LOG]
    existing = _read_state_file()
    pending = existing.get("pending_replies")
    pending = [str(item) for item in pending] if isinstance(pending, list) else []

    state = {
        "last_row": int(last_row),
        "tg_offset": int(tg_offset),
        "runs": trimmed,
        "pending_replies": pending,
    }

    STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    try:
        await mirror_run_state(
            last_row=int(last_row),
            tg_offset=int(tg_offset),
            runs=trimmed,
            settings=settings,
        )
    except HydraError as exc:
        return [f"Run state saved locally but not mirrored to HydraDB: {exc}"]

    return []


def reset_run_state() -> None:
    """Clear local bookkeeping so the next run is treated as the baseline."""
    STATE_PATH.unlink(missing_ok=True)
    clear_telegram_poller_active()
