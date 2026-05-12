"""APScheduler setup for the daily cron jobs.

Three coroutines fire at 06:30 / 12:00 / 21:00 LA-local:
- morning + evening: process pending items, then send a Telegram summary.
- noon: process only, silent unless something failed or the cap was hit.

The FastAPI app starts/stops this scheduler in its lifespan handler
(`bot/main.py`). Jobs are coroutine functions; AsyncIOScheduler awaits
them on the running event loop.
"""
from __future__ import annotations

from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


def build_scheduler(
    morning_job: Callable,
    noon_job: Callable,
    evening_job: Callable,
    timezone: str = "America/Los_Angeles",
) -> AsyncIOScheduler:
    """Return a not-yet-started AsyncIOScheduler with the three cron jobs registered."""
    sched = AsyncIOScheduler(timezone=timezone)
    sched.add_job(
        morning_job,
        trigger=CronTrigger(hour=6, minute=30, timezone=timezone),
        id="cron_morning",
        replace_existing=True,
    )
    sched.add_job(
        noon_job,
        trigger=CronTrigger(hour=12, minute=0, timezone=timezone),
        id="cron_noon",
        replace_existing=True,
    )
    sched.add_job(
        evening_job,
        trigger=CronTrigger(hour=21, minute=0, timezone=timezone),
        id="cron_evening",
        replace_existing=True,
    )
    return sched
