from datetime import datetime, timezone
from unittest.mock import MagicMock

from bot.cost_cap import fetch_today_spend_cents


def _mock_runs_query(rows: list[dict]) -> MagicMock:
    """Mock a Supabase client whose runs.select(...).gte(...).execute().data == rows."""
    client = MagicMock()
    chain = client.table.return_value.select.return_value
    chain.gte.return_value.execute.return_value.data = rows
    return client


def test_fetch_today_spend_returns_zero_when_no_runs():
    client = _mock_runs_query([])
    now_utc = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)  # 8:00 AM LA
    assert fetch_today_spend_cents(client, now=now_utc) == 0


def test_fetch_today_spend_sums_total_cost_cents():
    client = _mock_runs_query(
        [
            {"total_cost_cents": 12},
            {"total_cost_cents": 25},
            {"total_cost_cents": 0},
        ]
    )
    now_utc = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)
    assert fetch_today_spend_cents(client, now=now_utc) == 37


def test_fetch_today_spend_handles_null_costs():
    client = _mock_runs_query(
        [
            {"total_cost_cents": None},
            {"total_cost_cents": 8},
        ]
    )
    now_utc = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)
    assert fetch_today_spend_cents(client, now=now_utc) == 8


def test_fetch_today_spend_windows_at_la_midnight():
    # 2026-05-12 06:00 UTC == 2026-05-11 23:00 LA (PDT, UTC-7).
    # So "today in LA" starts at 2026-05-12T07:00:00+00:00 (= midnight LA on the 12th).
    client = _mock_runs_query([{"total_cost_cents": 42}])
    now_utc = datetime(2026, 5, 12, 6, 0, tzinfo=timezone.utc)
    fetch_today_spend_cents(client, now=now_utc)

    chain = client.table.return_value.select.return_value
    chain.gte.assert_called_once()
    args, _ = chain.gte.call_args
    assert args[0] == "started_at"
    # LA midnight on 2026-05-11 (since at 06:00 UTC we're still on the 11th in LA).
    # 2026-05-11 00:00 LA (PDT UTC-7) == 2026-05-11 07:00 UTC.
    assert args[1] == "2026-05-11T07:00:00+00:00"


def test_fetch_today_spend_defaults_now_to_utc_now(monkeypatch):
    # When `now` is not passed, the function uses datetime.now(timezone.utc).
    # Just confirm the call goes through (no AttributeError, returns int).
    client = _mock_runs_query([{"total_cost_cents": 5}])
    result = fetch_today_spend_cents(client)
    assert result == 5
