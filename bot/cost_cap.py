"""Daily cost-cap helper.

The cap is derived on the fly from `runs.total_cost_cents` summed across
today's LA-local calendar window. No separate counter, no schema change.
The processor passes `today_already_spent_cents` (snapshotted at batch
start) plus the in-batch running total to decide when to halt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LA = ZoneInfo("America/Los_Angeles")


def fetch_today_spend_cents(client, *, now: datetime | None = None) -> int:
    """Return the sum of `total_cost_cents` across all `runs` started today (LA)."""
    now_utc = now if now is not None else datetime.now(timezone.utc)
    now_la = now_utc.astimezone(LA)
    start_la = now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    since_utc = start_la.astimezone(timezone.utc).isoformat()

    rows = (
        client.table("runs")
        .select("total_cost_cents")
        .gte("started_at", since_utc)
        .execute()
        .data
        or []
    )
    return sum((r.get("total_cost_cents") or 0) for r in rows)
