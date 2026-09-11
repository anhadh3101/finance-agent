"""Chooses where the spend queries run: hotdata, or a local DuckDB stand-in.

hotdata is the real engine. The local engine exists so the agent still runs before a
hotdata key is available, and it is never used silently: the engine name travels with
every result and the run context reports it as a warning.

Both engines speak the same SQL dialect, so `analysis.py` does not care which is live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import hotdata_client
from config import Settings, get_settings
from sheet import SheetData

HOTDATA = "hotdata"
LOCAL = "duckdb-local"
LOCAL_DB_PATH = Path(__file__).resolve().parent / ".local_spend.duckdb"


class EngineError(Exception):
    """The query engine could not be reached."""


def engine_name(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.hotdata_api_key:
        return HOTDATA
    if settings.allow_local_query_fallback:
        return LOCAL
    raise EngineError(
        "HOTDATA_API_KEY is not set. Add it to .env, or set "
        "ALLOW_LOCAL_QUERY_FALLBACK=true to run the queries locally in DuckDB."
    )


def _local_connection():
    import duckdb

    return duckdb.connect(str(LOCAL_DB_PATH))


def _local_refresh(data: SheetData) -> str:
    connection = _local_connection()
    try:
        connection.execute("DROP TABLE IF EXISTS transactions")
        connection.execute(
            """
            CREATE TABLE transactions (
                row_num  BIGINT,
                date     DATE,
                merchant VARCHAR,
                amount   DECIMAL(12,2),
                category VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO transactions VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["row_num"],
                    row["date"],
                    row["merchant"],
                    row["amount"],
                    row["category"],
                )
                for row in data.transactions
            ],
        )

        connection.execute("DROP TABLE IF EXISTS budgets")
        connection.execute(
            "CREATE TABLE budgets (category VARCHAR, monthly_limit DECIMAL(12,2))"
        )
        connection.executemany(
            "INSERT INTO budgets VALUES (?, ?)",
            [(row["category"], row["monthly_limit"]) for row in data.budgets],
        )
    finally:
        connection.close()

    return LOCAL


def _local_query(sql: str) -> list[dict[str, Any]]:
    connection = _local_connection()
    try:
        cursor = connection.execute(sql)
        columns = [description[0] for description in cursor.description or []]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


def refresh(data: SheetData, settings: Settings | None = None) -> tuple[str, str]:
    """Replace the transactions and budgets tables. Returns (engine, handle)."""
    settings = settings or get_settings()
    engine = engine_name(settings)

    if engine == HOTDATA:
        try:
            handle = hotdata_client.refresh_tables(data, settings=settings)
        except hotdata_client.HotdataError as exc:
            raise EngineError(str(exc)) from exc
        return engine, handle

    return engine, _local_refresh(data)


def query(
    sql: str,
    handle: str,
    engine: str | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Run one SQL statement on whichever engine is live."""
    settings = settings or get_settings()
    engine = engine or engine_name(settings)

    if engine == HOTDATA:
        try:
            return hotdata_client.run_query(sql, handle, settings=settings)
        except hotdata_client.HotdataError as exc:
            raise EngineError(str(exc)) from exc

    return _local_query(sql)
