"""Random demo transactions for sheet append during live demos."""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from sheet import SheetData

CATEGORIES = (
    "food_delivery",
    "groceries",
    "dining",
    "gym",
    "subscriptions",
    "transport",
    "shopping",
)

MERCHANTS: dict[str, list[str]] = {
    "food_delivery": ["DoorDash", "Uber Eats", "Grubhub", "Postmates"],
    "groceries": ["Trader Joe's", "Safeway", "Whole Foods", "Costco", "Target"],
    "dining": ["Chipotle", "Sweetgreen", "Panera", "Starbucks"],
    "gym": ["Planet Fitness"],
    "subscriptions": ["Netflix", "Spotify", "Hulu", "Disney+", "Apple One"],
    "transport": ["Uber", "Lyft", "BART", "Caltrain"],
    "shopping": ["Amazon", "Best Buy", "Target", "IKEA", "Nike"],
}

AMOUNT_RANGES: dict[str, tuple[float, float]] = {
    "food_delivery": (16.0, 45.0),
    "groceries": (35.0, 120.0),
    "dining": (12.0, 28.0),
    "gym": (25.0, 25.0),
    "subscriptions": (9.99, 17.99),
    "transport": (10.0, 28.0),
    "shopping": (35.0, 180.0),
}

# Runbook demo batches for `/adddemo batch`.
DEMO_BATCHES: dict[str, list[dict[str, str]]] = {
    "a": [
        {"merchant": "DoorDash", "amount": "28.40", "category": "food_delivery"},
        {"merchant": "Uber Eats", "amount": "31.20", "category": "food_delivery"},
        {"merchant": "DoorDash", "amount": "26.75", "category": "food_delivery"},
        {"merchant": "Grubhub", "amount": "33.10", "category": "food_delivery"},
        {"merchant": "Hulu", "amount": "15.99", "category": "subscriptions"},
    ],
    "b": [
        {"merchant": "DoorDash", "amount": "29.50", "category": "food_delivery"},
        {"merchant": "Uber Eats", "amount": "27.80", "category": "food_delivery"},
        {"merchant": "DoorDash", "amount": "32.25", "category": "food_delivery"},
        {"merchant": "Trader Joe's", "amount": "58.40", "category": "groceries"},
    ],
    "c": [
        {"merchant": "Best Buy", "amount": "480.00", "category": "shopping"},
    ],
}


def _next_dates(start: date, count: int) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(count)]


def _random_amount(category: str) -> str:
    low, high = AMOUNT_RANGES[category]
    value = Decimal(str(random.uniform(low, high))).quantize(Decimal("0.01"))
    return format(value, "f")


def random_transactions(
    count: int,
    *,
    sheet_data: SheetData | None = None,
) -> list[dict[str, str]]:
    """Build `count` plausible rows dated after the sheet's latest transaction."""
    count = max(1, min(count, 10))
    start = (sheet_data.as_of + timedelta(days=1)) if sheet_data else date.today()
    dates = _next_dates(start, count)

    rows: list[dict[str, str]] = []
    for row_date in dates:
        category = random.choice(CATEGORIES)
        merchant = random.choice(MERCHANTS[category])
        rows.append(
            {
                "date": row_date.isoformat(),
                "merchant": merchant,
                "amount": _random_amount(category),
                "category": category,
            }
        )
    return rows


def demo_batch(name: str, *, sheet_data: SheetData | None = None) -> list[dict[str, str]]:
    """Return one named demo batch (a/b/c) with dates applied after the sheet's latest row."""
    key = name.strip().lower()
    if key not in DEMO_BATCHES:
        raise ValueError(f"Unknown batch `{name}`. Use a, b, or c.")

    start = (sheet_data.as_of + timedelta(days=1)) if sheet_data else date.today()
    dates = _next_dates(start, len(DEMO_BATCHES[key]))

    rows: list[dict[str, str]] = []
    for row_date, template in zip(dates, DEMO_BATCHES[key]):
        rows.append({"date": row_date.isoformat(), **template})
    return rows
