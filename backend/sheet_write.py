"""Append rows to the Google Sheet `transactions` tab."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from config import Settings, get_settings
from sheet import SheetError, extract_sheet_id

TRANSACTIONS_RANGE = "transactions!A:D"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class SheetWriteError(Exception):
    """The sheet could not be updated."""


def _rows_to_values(rows: list[dict[str, str]]) -> list[list[str]]:
    return [
        [row["date"], row["merchant"], row["amount"], row["category"]]
        for row in rows
    ]


def _service_account_token(settings: Settings) -> str:
    if not settings.google_service_account_file:
        raise SheetWriteError(
            "Sheet write is not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE in .env "
            "and share the sheet with the service account email as Editor."
        )

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise SheetWriteError(
            "google-auth is not installed. Run: pip install google-auth"
        ) from exc

    credentials = service_account.Credentials.from_service_account_file(
        settings.google_service_account_file,
        scopes=[SHEETS_SCOPE],
    )
    credentials.refresh(Request())
    if not credentials.token:
        raise SheetWriteError("Could not obtain a Google access token.")
    return credentials.token


async def _append_via_service_account(
    sheet_id: str,
    values: list[list[str]],
    settings: Settings,
) -> None:
    token = await asyncio.to_thread(_service_account_token, settings)
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/"
        f"{TRANSACTIONS_RANGE}:append"
    )
    params = {
        "valueInputOption": "USER_ENTERED",
        "insertDataOption": "INSERT_ROWS",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            params=params,
            json={"values": values},
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code >= 400:
        detail = response.text[:400]
        raise SheetWriteError(f"Google Sheets append failed ({response.status_code}): {detail}")


async def _append_via_webhook(
    values: list[list[str]],
    settings: Settings,
) -> None:
    if not settings.google_sheet_append_webhook:
        raise SheetWriteError("GOOGLE_SHEET_APPEND_WEBHOOK is not configured.")

    payload: dict[str, Any] = {"rows": values}
    if settings.google_sheet_append_secret:
        payload["secret"] = settings.google_sheet_append_secret

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(settings.google_sheet_append_webhook, json=payload)

    if response.status_code >= 400:
        raise SheetWriteError(
            f"Sheet append webhook failed ({response.status_code}): {response.text[:400]}"
        )

    try:
        body = response.json()
    except ValueError:
        return
    if isinstance(body, dict) and body.get("ok") is False:
        raise SheetWriteError(str(body.get("error") or "Sheet append webhook returned ok=false"))


async def append_transactions(
    rows: list[dict[str, str]],
    sheet_url: str,
    settings: Settings | None = None,
) -> int:
    """Append transaction rows to the sheet. Returns the number of rows written."""
    if not rows:
        raise SheetWriteError("No rows to append.")

    settings = settings or get_settings()
    values = _rows_to_values(rows)

    if settings.google_sheet_append_webhook:
        await _append_via_webhook(values, settings)
        return len(rows)

    sheet_id = extract_sheet_id(sheet_url)
    await _append_via_service_account(sheet_id, values, settings)
    return len(rows)
