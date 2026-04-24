"""
Smoke test for the Taiwan scheduler: the module imports without
triggering side effects, all job callables exist, and the cron
expressions we expect are present in the scheduler.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.services.taiwan import scheduler as sm


def test_job_callables_exist():
    for name in (
        "job_company_master",
        "job_monthly_revenue_daily",
        "job_monthly_revenue_window",
        "job_material_info_window",
        "job_twse_weekly_patch",
        "job_tpex_weekly_patch",
        "job_health_check",
    ):
        assert callable(getattr(sm, name))


def test_all_seven_jobs_register_with_expected_cron():
    """Mirror the real scheduler's add_job calls and verify the expected
    id → cron string pairing. If someone changes a cron by accident this
    test pins the intended cadence."""
    sched = BackgroundScheduler(timezone=ZoneInfo("Asia/Taipei"))
    expected = [
        ("monthly_revenue_daily",   sm.job_monthly_revenue_daily,  CronTrigger(hour="10", minute="0")),
        ("monthly_revenue_window",  sm.job_monthly_revenue_window, CronTrigger(day="1-15", minute="0,30")),
        ("material_info_window",    sm.job_material_info_window,   CronTrigger(day="1-15", minute="7,22,37,52")),
        ("twse_weekly_patch",       sm.job_twse_weekly_patch,      CronTrigger(day_of_week="sun", hour="3", minute="0")),
        ("tpex_weekly_patch",       sm.job_tpex_weekly_patch,      CronTrigger(day_of_week="sun", hour="3", minute="30")),
        ("company_master_refresh",  sm.job_company_master,         CronTrigger(day="1", hour="3", minute="0")),
        ("health_check",            sm.job_health_check,           CronTrigger(minute="17")),
    ]
    for jid, fn, trigger in expected:
        sched.add_job(fn, trigger, id=jid)

    assert len(sched.get_jobs()) == 7
    by_id = {j.id: j for j in sched.get_jobs()}
    assert set(by_id) == {e[0] for e in expected}

    # Spot-check a couple of cron string serialisations.
    assert "day='1-15'" in str(by_id["monthly_revenue_window"].trigger)
    assert "minute='0,30'" in str(by_id["monthly_revenue_window"].trigger)
    assert "minute='7,22,37,52'" in str(by_id["material_info_window"].trigger)
    assert "day_of_week='sun'" in str(by_id["twse_weekly_patch"].trigger)
    assert "day='1'" in str(by_id["company_master_refresh"].trigger)


def test_prior_month_helper_rolls_january_to_december():
    from datetime import datetime
    assert sm._prior_month(datetime(2026, 1, 15)) == (2025, 12)
    assert sm._prior_month(datetime(2026, 3, 15)) == (2026, 2)
    assert sm._prior_month(datetime(2099, 12, 31)) == (2099, 11)
