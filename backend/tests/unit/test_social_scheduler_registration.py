"""
Pin the social-scraper scheduler's cron cadence + job registration.

Parallels backend/tests/unit/test_scheduler_registration.py (Taiwan).
Adding a new job or changing a cron is intentional and should update
this test; accidental changes will fail CI.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.services.social import scheduler as sm


def test_job_callables_exist():
    for name in (
        "job_news_scrape",
        "job_reddit_scrape",
        "job_reddit_keyword_search",
        "job_gpu_price_snapshot",
        "job_social_health_check",
    ):
        assert callable(getattr(sm, name))


def test_all_social_jobs_register_with_expected_cron():
    sched = BackgroundScheduler(timezone=ZoneInfo("Asia/Taipei"))
    expected = [
        ("news_scrape",           sm.job_news_scrape,           CronTrigger(hour="*/2", minute="0")),
        ("reddit_scrape",         sm.job_reddit_scrape,         CronTrigger(hour="*/2", minute="15")),
        ("reddit_keyword_search", sm.job_reddit_keyword_search, CronTrigger(hour="*/4", minute="30")),
        ("gpu_price_snapshot",    sm.job_gpu_price_snapshot,    CronTrigger(hour="*/2", minute="45")),
        ("social_health_check",   sm.job_social_health_check,   CronTrigger(minute="23")),
    ]
    for jid, fn, trig in expected:
        sched.add_job(fn, trig, id=jid)

    assert len(sched.get_jobs()) == 5
    by_id = {j.id: j for j in sched.get_jobs()}
    assert set(by_id) == {e[0] for e in expected}

    # Pin the specific cron strings so churn is loud.
    assert "hour='*/2'"  in str(by_id["news_scrape"].trigger)
    assert "minute='0'"  in str(by_id["news_scrape"].trigger)
    assert "minute='15'" in str(by_id["reddit_scrape"].trigger)
    assert "hour='*/4'"  in str(by_id["reddit_keyword_search"].trigger)
    assert "minute='30'" in str(by_id["reddit_keyword_search"].trigger)
    assert "minute='45'" in str(by_id["gpu_price_snapshot"].trigger)
    assert "minute='23'" in str(by_id["social_health_check"].trigger)


def test_jobs_stagger_to_avoid_simultaneous_start():
    """The minute-offsets should be distinct so we don't stampede the
    network at :00 every two hours."""
    expected_minutes = {"0", "15", "30", "45", "23"}
    observed = set()
    for name, cron in [
        ("news_scrape",           CronTrigger(hour="*/2", minute="0")),
        ("reddit_scrape",         CronTrigger(hour="*/2", minute="15")),
        ("reddit_keyword_search", CronTrigger(hour="*/4", minute="30")),
        ("gpu_price_snapshot",    CronTrigger(hour="*/2", minute="45")),
        ("social_health_check",   CronTrigger(minute="23")),
    ]:
        # Grab the serialised trigger, extract the minute token
        s = str(cron)
        start = s.index("minute='") + len("minute='")
        end = s.index("'", start)
        observed.add(s[start:end])
    assert observed == expected_minutes
