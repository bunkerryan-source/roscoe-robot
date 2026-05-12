from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.scheduler import build_scheduler


async def _noop():
    return None


def _build():
    return build_scheduler(
        morning_job=_noop,
        noon_job=_noop,
        evening_job=_noop,
        timezone="America/Los_Angeles",
    )


def test_build_scheduler_returns_async_scheduler_with_three_jobs():
    sched = _build()
    assert isinstance(sched, AsyncIOScheduler)
    jobs = sched.get_jobs()
    assert len(jobs) == 3
    assert {j.id for j in jobs} == {"cron_morning", "cron_noon", "cron_evening"}


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
    sched = _build()
    assert _hour_minute(sched.get_job("cron_morning")) == (6, 30)


def test_noon_job_scheduled_for_1200_la():
    sched = _build()
    assert _hour_minute(sched.get_job("cron_noon")) == (12, 0)


def test_evening_job_scheduled_for_2100_la():
    sched = _build()
    assert _hour_minute(sched.get_job("cron_evening")) == (21, 0)


def test_all_jobs_use_la_timezone():
    sched = _build()
    for job_id in ("cron_morning", "cron_noon", "cron_evening"):
        job = sched.get_job(job_id)
        assert str(job.trigger.timezone) == "America/Los_Angeles"
