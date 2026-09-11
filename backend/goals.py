"""Synchronize and load the active goal across HydraDB and Cognee."""

from __future__ import annotations

from typing import Any

from hydra import HydraError, get_active_goal, upsert_active_goal
from memory import CogneeError, format_goal_text, recall_active_goal, replace_active_goal_in_cognee
from store import load_config


class GoalSyncError(Exception):
    """Goal could not be synchronized to one or more memory backends."""


async def sync_active_goal(description: str, target_amount: float) -> str:
    """Save or update the single active goal in HydraDB and Cognee."""
    goal_text = format_goal_text(description, target_amount)

    try:
        await upsert_active_goal(
            goal_text,
            description=description,
            target_amount=target_amount,
        )
    except HydraError as exc:
        raise GoalSyncError(f"HydraDB: {exc}") from exc

    try:
        await replace_active_goal_in_cognee(description, target_amount)
    except CogneeError as exc:
        raise GoalSyncError(f"Cognee: {exc}") from exc

    return goal_text


async def load_active_goal() -> dict[str, Any]:
    """Load the active goal, preferring HydraDB metadata and falling back to local config."""
    config = load_config()
    config_goal = config.goal.model_dump() if config else None

    try:
        hydra_goal = await get_active_goal()
        if hydra_goal and hydra_goal.get("goal"):
            goal_name = hydra_goal.get("goal")
            target_amount = hydra_goal.get("target_amount") or 0

            if config_goal:
                if not target_amount:
                    target_amount = config_goal["target_amount"]
                if goal_name.startswith("Active financial goal:"):
                    goal_name = config_goal["goal"]

            return {
                "goal": goal_name,
                "target_amount": target_amount,
                "source": "hydradb",
            }
    except HydraError:
        pass

    if config_goal:
        return {
            **config_goal,
            "source": "config",
        }

    return {}


async def load_goal_memory() -> dict[str, Any]:
    """Return structured goal memory for API responses."""
    goal = await load_active_goal()
    cognee_context: list[Any] = []

    try:
        cognee_context = await recall_active_goal()
    except CogneeError:
        pass

    return {
        "goals": [goal] if goal else [],
        "preferences": [],
        "cognee_context": cognee_context,
    }
