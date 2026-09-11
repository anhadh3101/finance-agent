"""hotdata integration: load the sheet into tables and run the analysis queries."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import hotdata

from config import Settings, get_settings
from sheet import SheetData, to_csv

SCHEMA = "main"
TRANSACTIONS_TABLE = "transactions"
BUDGETS_TABLE = "budgets"
DEFAULT_DATABASE_NAME = "finance-agent"

TRANSACTION_LOAD_COLUMNS = ("row_num", "date", "merchant", "amount", "category")
BUDGET_LOAD_COLUMNS = ("category", "monthly_limit")

TRANSACTION_COLUMN_TYPES = {
    "row_num": "BIGINT",
    "date": "DATE",
    "merchant": "VARCHAR",
    "amount": "DECIMAL(12,2)",
    "category": "VARCHAR",
}
BUDGET_COLUMN_TYPES = {
    "category": "VARCHAR",
    "monthly_limit": "DECIMAL(12,2)",
}


class HotdataError(Exception):
    """A hotdata request failed."""


def _resolve_workspace_id(settings: Settings) -> str:
    """Return the configured workspace, or the sole workspace on the account."""
    if settings.hotdata_workspace_id:
        return settings.hotdata_workspace_id

    configuration = hotdata.Configuration(api_key=settings.hotdata_api_key)
    with hotdata.ApiClient(configuration) as client:
        try:
            listed = hotdata.WorkspacesApi(client).list_workspaces()
        except Exception as exc:
            raise HotdataError(f"Could not list hotdata workspaces: {exc}") from exc

    workspaces = [workspace for workspace in (listed.workspaces or []) if workspace.active]
    if not workspaces:
        raise HotdataError(
            "No active hotdata workspace found. Set HOTDATA_WORKSPACE_ID in .env."
        )
    if len(workspaces) > 1:
        names = ", ".join(workspace.name for workspace in workspaces)
        raise HotdataError(
            "Multiple hotdata workspaces found "
            f"({names}). Set HOTDATA_WORKSPACE_ID in .env."
        )
    return workspaces[0].public_id


@contextmanager
def _api_client(settings: Settings | None = None) -> Iterator[hotdata.ApiClient]:
    settings = settings or get_settings()
    if not settings.hotdata_api_key:
        raise HotdataError("HOTDATA_API_KEY is not configured in .env")

    configuration = hotdata.Configuration(
        api_key=settings.hotdata_api_key,
        workspace_id=_resolve_workspace_id(settings),
    )
    with hotdata.ApiClient(configuration) as client:
        yield client


def resolve_database_id(settings: Settings | None = None) -> str:
    """Return the configured hotdata database, finding or creating it by name if needed."""
    settings = settings or get_settings()
    if settings.hotdata_database_id:
        return settings.hotdata_database_id

    name = settings.hotdata_database_name or DEFAULT_DATABASE_NAME
    with _api_client(settings) as client:
        databases = hotdata.DatabasesApi(client)
        try:
            listed = databases.list_databases(search=name, limit=100)
        except Exception as exc:
            raise HotdataError(f"Could not list hotdata databases: {exc}") from exc

        for database in listed.databases or []:
            if database.name == name:
                return database.id

        try:
            created = databases.create_database(
                hotdata.CreateDatabaseRequest(name=name, default_schema=SCHEMA)
            )
        except Exception as exc:
            raise HotdataError(f"Could not create hotdata database `{name}`: {exc}") from exc
        return created.id


def _load_table(
    client: hotdata.ApiClient,
    database_id: str,
    table: str,
    csv_text: str,
    column_types: dict[str, str],
) -> None:
    request = hotdata.LoadManagedTableRequest(
        data=csv_text,
        format="csv",
        mode="replace",
        columns={
            name: hotdata.ColumnDefinition(type_spec) for name, type_spec in column_types.items()
        },
    )
    try:
        hotdata.DatabasesApi(client).load_database_table(
            database_id=database_id,
            var_schema=SCHEMA,
            table=table,
            load_managed_table_request=request,
        )
    except Exception as exc:
        raise HotdataError(f"Could not load the `{table}` table into hotdata: {exc}") from exc


def refresh_tables(
    data: SheetData,
    database_id: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Replace the `transactions` and `budgets` tables with the latest sheet contents."""
    settings = settings or get_settings()
    database_id = database_id or resolve_database_id(settings)

    with _api_client(settings) as client:
        _load_table(
            client,
            database_id,
            TRANSACTIONS_TABLE,
            to_csv(data.transactions, TRANSACTION_LOAD_COLUMNS),
            TRANSACTION_COLUMN_TYPES,
        )
        _load_table(
            client,
            database_id,
            BUDGETS_TABLE,
            to_csv(data.budgets, BUDGET_LOAD_COLUMNS),
            BUDGET_COLUMN_TYPES,
        )

    return database_id


def run_query(
    sql: str,
    database_id: str,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Execute one SQL statement and return rows as dictionaries."""
    settings = settings or get_settings()
    with _api_client(settings) as client:
        try:
            response = hotdata.QueryApi(client).query(
                hotdata.QueryRequest(sql=sql, default_schema=SCHEMA),
                x_database_id=database_id,
            )
        except Exception as exc:
            raise HotdataError(f"hotdata query failed: {exc}") from exc

    columns = list(response.columns or [])
    return [dict(zip(columns, row)) for row in (response.rows or [])]
