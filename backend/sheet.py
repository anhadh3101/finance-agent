"""Google Sheet CSV download, validation, and cleaning."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
ALLOWED_HOST = "docs.google.com"
MAX_CSV_BYTES = 5 * 1024 * 1024

TRANSACTION_COLUMNS = ("date", "merchant", "amount", "category")
BUDGET_COLUMNS = ("category", "monthly_limit")

# Used only when the sheet has no readable `budgets` tab, so a demo run can still finish.
DEFAULT_BUDGETS: dict[str, Decimal] = {
    "food_delivery": Decimal("200"),
    "groceries": Decimal("400"),
    "dining": Decimal("150"),
    "gym": Decimal("25"),
    "subscriptions": Decimal("40"),
    "transport": Decimal("120"),
    "shopping": Decimal("150"),
}


class SheetError(Exception):
    """The sheet could not be downloaded or does not match the expected shape."""


@dataclass
class SheetData:
    transactions: list[dict[str, object]]
    budgets: list[dict[str, object]]
    as_of: date
    warnings: list[str] = field(default_factory=list)


def extract_sheet_id(sheet_url: str) -> str:
    """Pull the sheet ID out of a user-supplied URL without ever fetching that URL."""
    if ALLOWED_HOST not in sheet_url:
        raise SheetError(f"Sheet URL must be hosted on {ALLOWED_HOST}.")
    match = SHEET_ID_PATTERN.search(sheet_url)
    if not match:
        raise SheetError("Could not find a sheet ID in the URL.")
    return match.group(1)


def _gviz_url(sheet_id: str, tab_name: str) -> str:
    return (
        f"https://{ALLOWED_HOST}/spreadsheets/d/{sheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={tab_name}"
    )


def _export_url(sheet_id: str, gid: str) -> str:
    return (
        f"https://{ALLOWED_HOST}/spreadsheets/d/{sheet_id}"
        f"/export?format=csv&gid={int(gid)}"
    )


def _download(client: httpx.Client, url: str) -> str:
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    if len(response.content) > MAX_CSV_BYTES:
        raise SheetError("CSV export is larger than the 5 MB limit.")
    return response.text


def _parse_csv(text: str, required: tuple[str, ...], tab_name: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    headers = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    missing = [column for column in required if column not in headers]
    if missing:
        raise SheetError(
            f"Column `{missing[0]}` is missing from the {tab_name} tab. Found: {headers or 'nothing'}"
        )
    rows: list[dict[str, str]] = []
    for raw in reader:
        rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()})
    return rows


def _parse_amount(value: str, label: str) -> Decimal:
    cleaned = value.replace("$", "").replace(",", "").strip()
    if not cleaned:
        raise SheetError(f"Empty {label} value in the sheet.")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise SheetError(f"`{value}` is not a valid {label}.") from exc


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise SheetError(f"`{value}` is not a date in YYYY-MM-DD format.") from exc


def _fetch_tab(
    client: httpx.Client,
    sheet_id: str,
    tab_name: str,
    gid: str,
    required: tuple[str, ...],
) -> list[dict[str, str]]:
    """Try the named-tab endpoint first, then fall back to the gid export."""
    attempts = [_gviz_url(sheet_id, tab_name), _export_url(sheet_id, gid)]
    last_error: Exception | None = None
    for url in attempts:
        try:
            return _parse_csv(_download(client, url), required, tab_name)
        except (httpx.HTTPError, SheetError) as exc:
            last_error = exc
    raise SheetError(f"Could not read the {tab_name} tab: {last_error}")


def load_sheet(
    sheet_url: str,
    transactions_gid: str = "0",
    budgets_gid: str = "0",
) -> SheetData:
    """Download both tabs, clean them, and number transactions in sheet order."""
    sheet_id = extract_sheet_id(sheet_url)
    warnings: list[str] = []

    with httpx.Client(timeout=30.0) as client:
        raw_transactions = _fetch_tab(
            client, sheet_id, "transactions", transactions_gid, TRANSACTION_COLUMNS
        )
        try:
            raw_budgets = _fetch_tab(client, sheet_id, "budgets", budgets_gid, BUDGET_COLUMNS)
        except SheetError as exc:
            warnings.append(f"Using built-in budgets because the budgets tab is unreadable: {exc}")
            raw_budgets = []

    transactions: list[dict[str, object]] = []
    for index, row in enumerate(raw_transactions, start=1):
        if not any(row.get(column) for column in TRANSACTION_COLUMNS):
            continue
        transactions.append(
            {
                "row_num": index,
                "date": _parse_date(row["date"]),
                "merchant": row["merchant"],
                "amount": _parse_amount(row["amount"], "amount"),
                "category": row["category"].lower(),
            }
        )

    if not transactions:
        raise SheetError("The transactions tab has no data rows.")

    # Re-number after skipping blanks so row_num stays 1..N and contiguous.
    for position, row in enumerate(transactions, start=1):
        row["row_num"] = position

    budgets: list[dict[str, object]] = []
    for row in raw_budgets:
        if not row.get("category"):
            continue
        budgets.append(
            {
                "category": row["category"].lower(),
                "monthly_limit": _parse_amount(row["monthly_limit"], "monthly_limit"),
            }
        )

    if not budgets:
        budgets = [
            {"category": category, "monthly_limit": limit}
            for category, limit in DEFAULT_BUDGETS.items()
        ]

    as_of = max(row["date"] for row in transactions)  # type: ignore[type-var]
    return SheetData(transactions=transactions, budgets=budgets, as_of=as_of, warnings=warnings)


def to_csv(rows: list[dict[str, object]], columns: tuple[str, ...]) -> str:
    """Render cleaned rows as CSV text for an inline hotdata load."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row[column] for column in columns])
    return buffer.getvalue()
