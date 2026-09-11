"""Cognee integration for semantic goal memory."""

from __future__ import annotations

import os
from typing import Any

import cognee

from config import Settings, get_settings

COGNEE_DATASET = "active_goal"
COGNEE_HISTORY_DATASET = "finance_history"


class CogneeError(Exception):
    """Cognee processing failed."""


def format_goal_text(description: str, target_amount: float) -> str:
    return (
        f"Active financial goal: {description}. "
        f"The minimum required bank balance after completing this goal "
        f"is ${target_amount:,.2f}."
    )


def configure_cognee_env(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.llm_api_key:
        os.environ["LLM_API_KEY"] = settings.llm_api_key
    os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")


async def replace_active_goal_in_cognee(
    description: str,
    target_amount: float,
    settings: Settings | None = None,
) -> str:
    """Replace the active goal in Cognee and rebuild its knowledge graph."""
    settings = settings or get_settings()
    if not settings.llm_api_key:
        raise CogneeError("LLM_API_KEY is not configured in .env")

    configure_cognee_env(settings)
    goal_text = format_goal_text(description, target_amount)

    try:
        await cognee.forget(dataset=COGNEE_DATASET)
    except Exception as exc:
        message = str(exc).lower()
        if "none" not in message and "not found" not in message:
            raise CogneeError(f"Could not clear previous Cognee goal: {exc}") from exc

    await cognee.add(goal_text, dataset_name=COGNEE_DATASET)
    await cognee.cognify(datasets=[COGNEE_DATASET])

    return goal_text


async def add_history(texts: list[str], settings: Settings | None = None) -> bool:
    """Append run summaries and user replies to Cognee's history graph.

    Returns False when Cognee is not configured, so callers can carry on without it.
    `cognify` calls an LLM and is slow, so only run this after the user has their message.
    """
    settings = settings or get_settings()
    texts = [text.strip() for text in texts if text and text.strip()]
    if not texts or not settings.llm_api_key:
        return False

    configure_cognee_env(settings)

    try:
        for text in texts:
            await cognee.add(text, dataset_name=COGNEE_HISTORY_DATASET)
        await cognee.cognify(datasets=[COGNEE_HISTORY_DATASET])
    except Exception as exc:
        raise CogneeError(f"Could not add history to Cognee: {exc}") from exc

    return True


async def recall_history(query: str, settings: Settings | None = None) -> list[Any]:
    """Search the Cognee history graph for prior runs, replies, and patterns."""
    settings = settings or get_settings()
    if not settings.llm_api_key:
        return []

    configure_cognee_env(settings)

    try:
        results = await cognee.search(query_text=query, datasets=[COGNEE_HISTORY_DATASET])
    except Exception:
        # The dataset does not exist until the first run records history.
        return []

    return list(results or [])


async def recall_active_goal(settings: Settings | None = None) -> list[Any]:
    """Search Cognee for knowledge related to the active goal."""
    settings = settings or get_settings()
    if not settings.llm_api_key:
        return []

    configure_cognee_env(settings)

    try:
        results = await cognee.search(
            query_text="What is the active financial goal and target bank balance?",
            datasets=[COGNEE_DATASET],
        )
    except Exception as exc:
        raise CogneeError(f"Could not recall goal from Cognee: {exc}") from exc

    return list(results or [])
