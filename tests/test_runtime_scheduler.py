"""Tests for schedule normalize/next and scheduler locking behaviour."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tg_signer.runtime.models import SchedulePlan, isoformat
from tg_signer.runtime.scheduler import (
    Scheduler,
    compute_next_run,
    normalize_schedule_expr,
)
from tg_signer.runtime.store import RuntimeStore

FIXED_TZ = timezone(timedelta(hours=8))


def test_normalize_time_and_cron():
    assert normalize_schedule_expr("06:00:00") == "0 6 * * *"
    assert normalize_schedule_expr("0 8 * * *") == "0 8 * * *"
    with pytest.raises(ValueError):
        normalize_schedule_expr("not-a-schedule")


def test_compute_next_run_after_base():
    base = datetime(2026, 1, 1, 5, 0, 0, tzinfo=FIXED_TZ)
    nxt = compute_next_run("0 6 * * *", base=base, random_seconds=0)
    assert nxt > base
    assert nxt.hour == 6


@pytest.mark.asyncio
async def test_scheduler_runs_due_plan_once(tmp_path: Path):
    store = RuntimeStore(tmp_path / ".signer", session_dir=tmp_path)
    past = isoformat(datetime.now(tz=FIXED_TZ) - timedelta(minutes=1))
    plan = store.create_plan(
        SchedulePlan(
            id=None,
            account="a0",
            task_type="sign",
            task_ref="sg",
            schedule_expr="0 6 * * *",
            enabled=True,
            next_run_at=past,
            max_retries=0,
        )
    )
    calls: list[int] = []

    async def run_plan(p: SchedulePlan, attempt: int, source: str):
        calls.append(p.id)
        await asyncio.sleep(0)

    sch = Scheduler(store, run_plan, tick_seconds=0.05)
    await sch.tick()
    # allow created task to finish
    await asyncio.sleep(0.1)
    assert plan.id in calls
    refreshed = store.get_plan(plan.id)
    assert refreshed is not None
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at != past


@pytest.mark.asyncio
async def test_per_account_serial(tmp_path: Path):
    store = RuntimeStore(tmp_path / ".signer", session_dir=tmp_path)
    past = isoformat(datetime.now(tz=FIXED_TZ) - timedelta(minutes=1))
    p1 = store.create_plan(
        SchedulePlan(
            id=None,
            account="a0",
            task_type="sign",
            task_ref="t1",
            schedule_expr="0 6 * * *",
            next_run_at=past,
            max_retries=0,
        )
    )
    p2 = store.create_plan(
        SchedulePlan(
            id=None,
            account="a0",
            task_type="sign",
            task_ref="t2",
            schedule_expr="0 7 * * *",
            next_run_at=past,
            max_retries=0,
        )
    )
    active = 0
    max_active = 0
    order: list[int] = []

    async def run_plan(p: SchedulePlan, attempt: int, source: str):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        order.append(p.id)
        await asyncio.sleep(0.05)
        active -= 1

    sch = Scheduler(store, run_plan, tick_seconds=0.05)
    await sch.tick()
    await asyncio.sleep(0.25)
    assert max_active == 1
    assert set(order) == {p1.id, p2.id}
