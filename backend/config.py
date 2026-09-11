"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    hotdata_api_key: str = ""
    hotdata_workspace_id: str = ""
    hotdata_database_id: str = ""
    hotdata_database_name: str = "finance-agent"
    # Fallback only: run the spend queries in local DuckDB when hotdata has no key yet.
    allow_local_query_fallback: bool = False
    claude_bin: str = "claude"
    claude_timeout_seconds: float = 240.0
    hydradb_key: str = Field(default="", validation_alias=AliasChoices("HYDRADB_KEY", "hydradb_key"))
    hydradb_database: str = Field(
        default="default-tenant",
        validation_alias=AliasChoices("HYDRADB_DATABASE", "hydradb_database"),
    )
    hydradb_url: str = ""
    hydradb_credentials: str = ""
    llm_api_key: str = ""
    agent_shared_secret: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    google_service_account_file: str = ""
    google_sheet_append_webhook: str = ""
    google_sheet_append_secret: str = ""
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
