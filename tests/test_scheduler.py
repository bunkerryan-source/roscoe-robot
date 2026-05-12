from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.scheduler import build_scheduler


async def _noop():
    return None


def test_build_scheduler_returns_async_scheduler_with_two_jobs():
    sched = build_scheduler(
        morning_job=_noop,
        evening_job=_noop,
        timezone="America/Los_Angeles",
    )
    assert isinstance(sched, AsyncIOScheduler)
    jobs = sched.get_jobs()
    assert len(jobs) == 2
    assert {j.id for j in jobs} == {"daily_summary_morning", "daily_summary_evening"}


def _hour_minute(job):
    """Extract (hour, minute) ints from a CronTrigger's fields, ignoring framework details."""
    h = m = None
    for field in job.trigger.fields:
        if field.name == "hour":
            h = field.expressions[0].first
        if field.name == "minute":
            m = field.expressions[0].first
    return h, m


def test_morning_job_scheduled_for_0630_la():
    sched = build_scheduler(_noop, _noop, "America/Los_Angeles")
    morning = sched.get_job("daily_summary_morning")
    assert _hour_minute(morning) == (6, 30)


def test_evening_job_scheduled_for_2100_la():
    sched = build_scheduler(_noop, _noop, "America/Los_Angeles")
    evening = sched.get_job("daily_summary_evening")
    assert _hour_minute(evening) == (21, 0)


def test_scheduler_uses_la_timezone():
    sched = build_scheduler(_noop, _noop, "America/Los_Angeles")
    morning = sched.get_job("daily_summary_morning")
    # CronTrigger's timezone attribute holds the zone the cron fires against.
    assert str(morning.trigger.timezone) == "America/Los_Angeles"
