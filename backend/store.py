"""Local JSON persistence for user configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


class GoalConfig(BaseModel):
    """One active savings goal."""

    goal: str
    target_amount: float = Field(gt=0)

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Goal description is required.")
        return value


class AppConfig(BaseModel):
    sheet_url: str
    transactions_gid: str = "0"
    budgets_gid: str = "0"
    goal: GoalConfig

    @field_validator("sheet_url")
    @classmethod
    def validate_sheet_url(cls, value: str) -> str:
        value = value.strip()
        if "docs.google.com" not in value:
            raise ValueError("Sheet URL must be a Google Sheets link (docs.google.com).")
        if not SHEET_ID_PATTERN.search(value):
            raise ValueError("Could not find a sheet ID in the URL.")
        return value


class AppConfigPublic(BaseModel):
    sheet_url: str
    transactions_gid: str
    budgets_gid: str
    goal: GoalConfig


def _normalize_goal_data(goal_data: dict[str, Any]) -> dict[str, Any]:
    if "name" in goal_data and "goal" not in goal_data:
        goal_data["goal"] = goal_data.pop("name")
    if "target" in goal_data and "target_amount" not in goal_data:
        goal_data["target_amount"] = goal_data.pop("target")
    return {
        "goal": goal_data.get("goal", ""),
        "target_amount": goal_data.get("target_amount", 0),
    }


def load_config() -> AppConfig | None:
    if not CONFIG_PATH.exists():
        return None
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data.pop("bot_token", None)
    data.pop("chat_id", None)
    if "goal" in data:
        data["goal"] = _normalize_goal_data(data["goal"])
    return AppConfig.model_validate(data)


def save_config(config: AppConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        config.model_dump_json(indent=2),
        encoding="utf-8",
    )


def to_public(config: AppConfig) -> AppConfigPublic:
    return AppConfigPublic(
        sheet_url=config.sheet_url,
        transactions_gid=config.transactions_gid,
        budgets_gid=config.budgets_gid,
        goal=config.goal,
    )
