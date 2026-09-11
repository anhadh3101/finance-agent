"""Spend analysis: the hotdata queries plus the deterministic flag math."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Any

from query_engine import query

# Spending is flagged once month-to-date is 20% past the straight-line pace.
PACE_TOLERANCE = Decimal("1.2")
# A never-before-seen merchant is only a large charge above this absolute amount.
UNKNOWN_MERCHANT_THRESHOLD = Decimal("200")
# A known merchant is a large charge at this multiple of its historical average.
KNOWN_MERCHANT_MULTIPLE = 3
# Below this many charges in the month, a category is treated as a fixed recurring cost.
MIN_CHARGES_FOR_PACE_FLAG = 2

# `asof` is a reserved word in this SQL dialect (ASOF JOIN), hence `latest`.
Q1_SPEND_VS_BUDGET = """
WITH latest AS (SELECT MAX(date) AS d FROM transactions)
SELECT t.category            AS category,
       SUM(t.amount)         AS spent,
       COUNT(*)              AS charges,
       MAX(b.monthly_limit)  AS monthly_limit
FROM transactions t
JOIN budgets b ON b.category = t.category
CROSS JOIN latest
WHERE date_trunc('month', t.date) = date_trunc('month', latest.d)
GROUP BY t.category
ORDER BY t.category
"""

Q2_NEW_ROWS = """
SELECT row_num, date, merchant, amount, category
FROM transactions
WHERE row_num > {last_row}
ORDER BY row_num
"""

Q3_LARGE_CHARGES = """
SELECT n.row_num      AS row_num,
       n.merchant     AS merchant,
       n.amount       AS amount,
       n.category     AS category,
       AVG(h.amount)  AS typical,
       COUNT(h.amount) AS seen
FROM transactions n
LEFT JOIN transactions h
       ON h.merchant = n.merchant
      AND h.row_num <= {last_row}
WHERE n.row_num > {last_row}
GROUP BY n.row_num, n.merchant, n.amount, n.category
HAVING (COUNT(h.amount) > 0 AND n.amount > {multiple} * AVG(h.amount))
    OR (COUNT(h.amount) = 0 AND n.amount > {unknown_threshold})
ORDER BY n.amount DESC
"""

Q4_NEW_SUBSCRIPTIONS = """
SELECT n.merchant AS merchant, MAX(n.amount) AS amount
FROM transactions n
WHERE n.row_num > {last_row}
  AND n.category = 'subscriptions'
  AND n.merchant NOT IN (
        SELECT merchant FROM transactions WHERE row_num <= {last_row}
      )
GROUP BY n.merchant
ORDER BY n.merchant
"""

Q_MAX_ROW = "SELECT MAX(row_num) AS max_row, MAX(date) AS as_of FROM transactions"


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value: Any) -> float:
    return float(round(_decimal(value), 2))


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def expected_pace(monthly_limit: Decimal, as_of: date) -> Decimal:
    """Straight-line share of a monthly budget that should be spent by `as_of`."""
    days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
    return (monthly_limit * as_of.day / days_in_month).quantize(Decimal("0.01"))


def analyze(handle: str, last_row: int, engine: str | None = None) -> dict[str, Any]:
    """Run Q1-Q4 and turn the results into candidate flags.

    `last_row` is coerced to an int before it reaches the SQL text, so it cannot
    be used to inject a query.
    """
    last_row = int(last_row)

    def run(sql: str) -> list[dict[str, Any]]:
        return query(sql, handle, engine=engine)

    bounds = run(Q_MAX_ROW)
    max_row = int(bounds[0]["max_row"] or 0) if bounds else 0
    as_of = _as_date(bounds[0]["as_of"]) if bounds and bounds[0]["as_of"] else date.today()

    spend_rows = run(Q1_SPEND_VS_BUDGET)
    new_rows = run(Q2_NEW_ROWS.format(last_row=last_row))
    large_charges = run(
        Q3_LARGE_CHARGES.format(
            last_row=last_row,
            multiple=KNOWN_MERCHANT_MULTIPLE,
            unknown_threshold=UNKNOWN_MERCHANT_THRESHOLD,
        )
    )
    new_subscriptions = run(Q4_NEW_SUBSCRIPTIONS.format(last_row=last_row))

    categories_with_new_rows = {str(row["category"]).lower() for row in new_rows}

    spend_vs_budget: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []

    for row in spend_rows:
        category = str(row["category"]).lower()
        spent = _decimal(row["spent"])
        charges = int(row["charges"] or 0)
        monthly_limit = _decimal(row["monthly_limit"])
        pace = expected_pace(monthly_limit, as_of)
        over_pace = spent > pace * PACE_TOLERANCE
        over_limit = spent > monthly_limit

        spend_vs_budget.append(
            {
                "category": category,
                "spent": _money(spent),
                "charges": charges,
                "monthly_limit": _money(monthly_limit),
                "expected_pace": _money(pace),
                "over_pace": over_pace,
                "over_limit": over_limit,
            }
        )

        # A single fixed monthly charge (gym, one subscription) sits above straight-line
        # pace for most of the month by design. Only repeated spending is worth a nudge,
        # unless the budget is genuinely blown.
        is_recurring_fixed_cost = charges < MIN_CHARGES_FOR_PACE_FLAG and not over_limit

        if (
            category in categories_with_new_rows
            and (over_pace or over_limit)
            and not is_recurring_fixed_cost
        ):
            flags.append(
                {
                    "type": "overspend",
                    "category": category,
                    "merchant": None,
                    "spent": _money(spent),
                    "expected_pace": _money(pace),
                    "monthly_limit": _money(monthly_limit),
                    "charges": charges,
                }
            )

    for row in large_charges:
        flags.append(
            {
                "type": "large_charge",
                "category": str(row["category"]).lower(),
                "merchant": row["merchant"],
                "amount": _money(row["amount"]),
                "typical": _money(row["typical"]) if row["seen"] else None,
                "times_seen_before": int(row["seen"] or 0),
            }
        )

    for row in new_subscriptions:
        flags.append(
            {
                "type": "new_subscription",
                "category": "subscriptions",
                "merchant": row["merchant"],
                "amount": _money(row["amount"]),
            }
        )

    return {
        "as_of": as_of.isoformat(),
        "last_row": last_row,
        "max_row": max_row,
        "new_row_count": len(new_rows),
        "new_rows": [
            {
                "row_num": int(row["row_num"]),
                "date": _as_date(row["date"]).isoformat(),
                "merchant": row["merchant"],
                "amount": _money(row["amount"]),
                "category": str(row["category"]).lower(),
            }
            for row in new_rows
        ],
        "spend_vs_budget": spend_vs_budget,
        "flags": flags,
    }
