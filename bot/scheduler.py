"""APScheduler setup for the daily-summary jobs.

The FastAPI app starts/stops this scheduler in its lifespan handler
(`bot/main.py`). The morning and evening jobs are coroutine functions
defined there; AsyncIOScheduler awaits them on the running event loop.
"""
from __future__ import annotations

from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


def build_scheduler(
    morning_job: Callable,
    evening_job: Callable,
    timezone: str = "America/Los_Angeles",
) -> AsyncIOScheduler:
    """Return a not-yet-started AsyncIOScheduler with the two daily jobs registered."""
    sched = AsyncIOScheduler(timezone=timezone)
    sched.add_job(
        morning_job,
        trigger=CronTrigger(hour=6, minute=30, timezone=timezone),
        id="daily_summary_morning",
        replace_existing=True,
    )
    sched.add_job(
        evening_job,
        trigger=CronTrigger(hour=21, minute=0, timezone=timezone),
        id="daily_summary_evening",
        replace_existing=True,
    )
    return sched
