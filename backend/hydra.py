"""HydraDB integration for the active goal, run state, preferences, and nudges."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from hydra_db import AsyncHydraDB

from config import Settings, get_settings

ACTIVE_GOAL_SOURCE_ID = "active-goal"
RUN_STATE_SOURCE_ID = "agent-run-state"
PREFERENCE_PREFIX = "preference-"
NUDGE_PREFIX = "nudge-"
MAX_RUN_LOG = 20

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


class HydraError(Exception):
    """HydraDB request failed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str, limit: int = 48) -> str:
    slug = _SLUG_PATTERN.sub("-", value.lower()).strip("-")
    return slug[:limit] or "item"


@lru_cache
def _client_for_key(api_key: str) -> AsyncHydraDB:
    return AsyncHydraDB(token=api_key)


def get_hydra_client(settings: Settings | None = None) -> AsyncHydraDB:
    settings = settings or get_settings()
    if not settings.hydradb_key:
        raise HydraError("HYDRADB_KEY is not configured in .env")
    return _client_for_key(settings.hydradb_key)


def resolve_database_id(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.hydradb_database:
        return settings.hydradb_database
    return "default-tenant"


async def upsert_memory(
    source_id: str,
    text: str,
    metadata: dict[str, Any],
    *,
    infer: bool = True,
    settings: Settings | None = None,
) -> None:
    """Create or replace one memory record, keyed by `source_id`."""
    settings = settings or get_settings()
    client = get_hydra_client(settings)
    database = resolve_database_id(settings)

    memories = json.dumps(
        [
            {
                "source_id": source_id,
                "text": text,
                "infer": infer,
                "additional_metadata": metadata,
            }
        ]
    )

    try:
        response = await client.context.ingest(
            database=database,
            type="memory",
            memories=memories,
            upsert="true",
        )
    except Exception as exc:
        raise HydraError(f"Could not write `{source_id}`: {exc}") from exc

    if not response.success:
        detail = response.error.message if response.error else "HydraDB ingest failed"
        raise HydraError(detail)


async def upsert_active_goal(
    goal_text: str,
    *,
    description: str,
    target_amount: float,
    settings: Settings | None = None,
) -> None:
    """Create or replace the single active goal memory in HydraDB."""
    await upsert_memory(
        ACTIVE_GOAL_SOURCE_ID,
        goal_text,
        {
            "type": "active_goal",
            "goal_name": description,
            "target_amount": target_amount,
        },
        settings=settings,
    )


async def get_active_goal(settings: Settings | None = None) -> dict[str, str | float] | None:
    """Fetch the active goal memory from HydraDB, if it exists."""
    settings = settings or get_settings()
    if not settings.hydradb_key:
        return None

    client = get_hydra_client(settings)
    database = resolve_database_id(settings)

    try:
        listed = await client.context.list(
            database=database,
            type="memory",
            ids=[ACTIVE_GOAL_SOURCE_ID],
            page=1,
            page_size=1,
        )
    except Exception as exc:
        raise HydraError(f"Could not list active goal: {exc}") from exc

    if not listed.success:
        return None

    memories = listed.data.user_memories if listed.data else None
    if not memories:
        return None

    memory = memories[0]
    memory_id = getattr(memory, "memory_id", None) or ACTIVE_GOAL_SOURCE_ID
    list_metadata = memory.additional_metadata or {}

    try:
        detail = await client.context.inspect(
            id=memory_id,
            database=database,
        )
    except Exception as exc:
        raise HydraError(f"Could not inspect active goal: {exc}") from exc

    if not detail.success or not detail.data:
        return None

    payload = detail.data
    text = getattr(payload, "text", None) or getattr(payload, "content", None) or ""
    inspect_metadata = getattr(payload, "additional_metadata", None) or {}
    meta_dict = dict(list_metadata)
    if isinstance(inspect_metadata, dict):
        meta_dict.update(inspect_metadata)

    description = meta_dict.get("goal_name") or text
    target_amount = meta_dict.get("target_amount", 0)

    return {
        "source_id": ACTIVE_GOAL_SOURCE_ID,
        "goal": description,
        "target_amount": float(target_amount or 0),
        "type": meta_dict.get("type", "active_goal"),
    }


async def _list_metadata(
    prefix: str | None = None,
    ids: list[str] | None = None,
    settings: Settings | None = None,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """List memory records and return their metadata, optionally filtered by ID prefix."""
    settings = settings or get_settings()
    if not settings.hydradb_key:
        return []

    client = get_hydra_client(settings)
    database = resolve_database_id(settings)

    kwargs: dict[str, Any] = {
        "database": database,
        "type": "memory",
        "page": 1,
        "page_size": page_size,
    }
    if ids:
        kwargs["ids"] = ids

    try:
        listed = await client.context.list(**kwargs)
    except Exception as exc:
        raise HydraError(f"Could not list memories: {exc}") from exc

    if not listed.success or not listed.data:
        return []

    records: list[dict[str, Any]] = []
    for memory in listed.data.user_memories or []:
        metadata = dict(memory.additional_metadata or {})
        memory_id = str(getattr(memory, "memory_id", "") or "")
        if prefix and not memory_id.startswith(prefix):
            continue
        metadata["_id"] = memory_id
        records.append(metadata)
    return records


async def mirror_run_state(
    *,
    last_row: int,
    tg_offset: int,
    runs: list[dict[str, Any]],
    settings: Settings | None = None,
) -> None:
    """Mirror the run log into HydraDB alongside the goal, preferences, and nudges.

    `run_state.py` owns the authoritative copy; see the note there on why. Stored as
    opaque JSON with `infer=False` so HydraDB does not extract meaning from bookkeeping.
    """
    trimmed = runs[:MAX_RUN_LOG]
    state = {"last_row": int(last_row), "tg_offset": int(tg_offset), "runs": trimmed}
    latest = trimmed[0] if trimmed else {}
    text = (
        f"Agent run state: last processed sheet row {int(last_row)}, "
        f"{len(trimmed)} recent runs recorded. "
        f"Most recent run mode: {latest.get('mode', 'none')}."
    )
    await upsert_memory(
        RUN_STATE_SOURCE_ID,
        text,
        {"type": "run_state", "state": json.dumps(state)},
        infer=False,
        settings=settings,
    )


async def record_preference(
    kind: str,
    target: str,
    reason: str,
    *,
    settings: Settings | None = None,
) -> str:
    """Store one user preference (ignore, protect, or keep) extracted from a reply."""
    source_id = f"{PREFERENCE_PREFIX}{kind}-{slugify(target)}"
    text = f"The user wants to {kind} {target}. Reason: {reason}" if reason else (
        f"The user wants to {kind} {target}."
    )
    await upsert_memory(
        source_id,
        text,
        {
            "type": "preference",
            "kind": kind,
            "target": target,
            "reason": reason,
            "recorded_at": _now(),
        },
        settings=settings,
    )
    return source_id


async def get_preferences(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Return every stored preference so flags can be filtered against them."""
    try:
        records = await _list_metadata(prefix=PREFERENCE_PREFIX, settings=settings)
    except HydraError:
        return []

    preferences = []
    for record in records:
        if record.get("type") != "preference":
            continue
        preferences.append(
            {
                "kind": record.get("kind", ""),
                "target": record.get("target", ""),
                "reason": record.get("reason", ""),
            }
        )
    return preferences


async def record_nudge(
    run_id: str,
    text: str,
    *,
    flag_types: list[str],
    categories: list[str],
    settings: Settings | None = None,
) -> None:
    """Store a sent nudge so later runs can reuse wording that worked."""
    await upsert_memory(
        f"{NUDGE_PREFIX}{run_id}",
        f"Nudge sent to the user: {text}",
        {
            "type": "nudge",
            "run_id": run_id,
            "flag_types": ",".join(flag_types),
            "categories": ",".join(categories),
            "sent_at": _now(),
        },
        settings=settings,
    )


async def get_recent_nudges(
    limit: int = 5,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Return the most recent nudges, newest first."""
    try:
        records = await _list_metadata(prefix=NUDGE_PREFIX, settings=settings)
    except HydraError:
        return []

    nudges = [record for record in records if record.get("type") == "nudge"]
    nudges.sort(key=lambda record: str(record.get("sent_at", "")), reverse=True)
    return [
        {
            "flag_types": [part for part in str(record.get("flag_types", "")).split(",") if part],
            "categories": [part for part in str(record.get("categories", "")).split(",") if part],
            "sent_at": record.get("sent_at", ""),
        }
        for record in nudges[:limit]
    ]
