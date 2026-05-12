"""Tests for the three cron coroutines in bot/main.py.

Each cron job is a coroutine that:
1. Snapshots today's already-spent cents.
2. Either skips silently (if already over cap) or runs the batch.
3. Conditionally sends Telegram messages (halt alert + summary).

These tests stub the underlying primitives (`fetch_today_spend_cents`,
`run_batch`, `send_message`, and the summary fetchers) and assert on the
call sequence/arguments. APScheduler timing is its own contract, tested
at the build_scheduler level in test_scheduler.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def cron(env, mocker):
    """Import bot.main with all external clients mocked, return the module."""
    mocker.patch("bot.db.create_client", return_value=MagicMock())
    mocker.patch("bot.media.dropbox.Dropbox", return_value=MagicMock())

    import bot.main as bm

    # Rebind module-level singletons to plain mocks so they're not real Supabase
    # clients carrying state from a previous test.
    mocker.patch.object(bm, "supabase", MagicMock())
    mocker.patch.object(bm, "anthropic_client", MagicMock())
    mocker.patch.object(bm, "dropbox_factory", MagicMock())
    # Replace send_message with an AsyncMock so we can assert call_args.
    mocker.patch.object(bm, "send_message", AsyncMock())
    return bm


def _batch_result(
    *,
    processed: int = 2,
    needs_review: int = 0,
    failed: int = 0,
    cost: int = 30,
    halted: bool = False,
    remaining: int = 0,
):
    return {
        "items_processed": processed,
        "items_needs_review": needs_review,
        "items_failed": failed,
        "total_cost_cents": cost,
        "duration_seconds": 1.2,
        "halted_at_cap": halted,
        "items_remaining_pending": remaining,
    }


# --- Morning ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_morning_cron_runs_batch_then_summary_when_under_cap(cron, mocker):
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=0)
    rb = mocker.patch("bot.main.run_batch", return_value=_batch_result())
    mocker.patch("bot.main.fetch_items_for_summary", return_value=[])
    mocker.patch("bot.main.build_morning_summary", return_value="morning brief text")

    await cron._morning_cron()

    rb.assert_called_once()
    kwargs = rb.call_args.kwargs
    assert kwargs["trigger"] == "scheduled-630"
    assert kwargs["daily_cap_cents"] == cron.config.daily_cost_cap_cents
    assert kwargs["today_already_spent_cents"] == 0

    # Summary message sent; no halt alert.
    sent = [c.args[2] for c in cron.send_message.await_args_list]
    assert any("morning brief text" in t for t in sent)
    assert not any("halted" in t for t in sent)


@pytest.mark.asyncio
async def test_morning_cron_skips_batch_silently_when_already_over_cap(cron, mocker):
    cap = cron.config.daily_cost_cap_cents
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=cap + 1)
    rb = mocker.patch("bot.main.run_batch")
    mocker.patch("bot.main.fetch_items_for_summary", return_value=[])
    mocker.patch("bot.main.build_morning_summary", return_value="brief")

    await cron._morning_cron()

    rb.assert_not_called()
    # Summary still sent (it's a daily brief about yesterday).
    assert cron.send_message.await_count >= 1
    sent = [c.args[2] for c in cron.send_message.await_args_list]
    assert not any("halted" in t for t in sent)


@pytest.mark.asyncio
async def test_morning_cron_sends_halt_alert_then_summary_when_cap_hit(cron, mocker):
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=0)
    cap = cron.config.daily_cost_cap_cents
    mocker.patch(
        "bot.main.run_batch",
        return_value=_batch_result(processed=1, cost=cap, halted=True, remaining=3),
    )
    mocker.patch("bot.main.fetch_items_for_summary", return_value=[])
    mocker.patch("bot.main.build_morning_summary", return_value="brief")

    await cron._morning_cron()

    sent = [c.args[2] for c in cron.send_message.await_args_list]
    halt_msgs = [t for t in sent if "halted" in t]
    assert len(halt_msgs) == 1
    assert "3 items remain pending" in halt_msgs[0]
    dollars = f"${cap/100:.2f}"
    assert dollars in halt_msgs[0]


# --- Noon -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noon_cron_is_silent_on_clean_run(cron, mocker):
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=0)
    mocker.patch("bot.main.run_batch", return_value=_batch_result())

    await cron._noon_cron()

    cron.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_noon_cron_passes_scheduled_1200_trigger(cron, mocker):
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=0)
    rb = mocker.patch("bot.main.run_batch", return_value=_batch_result())

    await cron._noon_cron()

    assert rb.call_args.kwargs["trigger"] == "scheduled-1200"


@pytest.mark.asyncio
async def test_noon_cron_speaks_on_failures(cron, mocker):
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=0)
    mocker.patch(
        "bot.main.run_batch",
        return_value=_batch_result(processed=1, failed=2),
    )

    await cron._noon_cron()

    sent = [c.args[2] for c in cron.send_message.await_args_list]
    assert len(sent) == 1
    assert "2 failed" in sent[0]


@pytest.mark.asyncio
async def test_noon_cron_sends_halt_alert_when_cap_hit(cron, mocker):
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=0)
    cap = cron.config.daily_cost_cap_cents
    mocker.patch(
        "bot.main.run_batch",
        return_value=_batch_result(processed=1, cost=cap, halted=True, remaining=4),
    )

    await cron._noon_cron()

    sent = [c.args[2] for c in cron.send_message.await_args_list]
    halt_msgs = [t for t in sent if "halted" in t]
    assert len(halt_msgs) == 1
    assert "4 items remain pending" in halt_msgs[0]


@pytest.mark.asyncio
async def test_noon_cron_skips_batch_when_over_cap(cron, mocker):
    cap = cron.config.daily_cost_cap_cents
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=cap + 50)
    rb = mocker.patch("bot.main.run_batch")

    await cron._noon_cron()

    rb.assert_not_called()
    cron.send_message.assert_not_awaited()


# --- Evening ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_evening_cron_runs_batch_then_summary_when_under_cap(cron, mocker):
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=0)
    rb = mocker.patch("bot.main.run_batch", return_value=_batch_result())
    mocker.patch("bot.main.fetch_items_for_summary", return_value=[])
    mocker.patch("bot.main.build_evening_summary", return_value="evening brief text")

    await cron._evening_cron()

    rb.assert_called_once()
    assert rb.call_args.kwargs["trigger"] == "scheduled-2100"

    sent = [c.args[2] for c in cron.send_message.await_args_list]
    assert any("evening brief text" in t for t in sent)
    assert not any("halted" in t for t in sent)


@pytest.mark.asyncio
async def test_evening_cron_skips_batch_when_over_cap(cron, mocker):
    cap = cron.config.daily_cost_cap_cents
    mocker.patch("bot.main.fetch_today_spend_cents", return_value=cap)
    rb = mocker.patch("bot.main.run_batch")
    mocker.patch("bot.main.fetch_items_for_summary", return_value=[])
    mocker.patch("bot.main.build_evening_summary", return_value="brief")

    await cron._evening_cron()

    rb.assert_not_called()
    # Summary still sent.
    sent = [c.args[2] for c in cron.send_message.await_args_list]
    assert any("brief" in t for t in sent)


# --- /process bypass --------------------------------------------------------


@pytest.mark.asyncio
async def test_process_command_bypasses_cap(cron, mocker):
    """The /process handler must not pass a daily cap to run_batch."""
    rb = mocker.patch("bot.main.run_batch", return_value=_batch_result())
    mocker.patch("bot.main.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, **kw: fn(**kw)))

    await cron._run_batch_and_reply(chat_id=12345, reply_to=1)

    kwargs = rb.call_args.kwargs
    # Either the kwarg is absent or explicitly None — both mean "no cap".
    assert kwargs.get("daily_cap_cents") is None
