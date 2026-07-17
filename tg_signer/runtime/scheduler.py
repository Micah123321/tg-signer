"""Schedule expression helpers and asyncio scheduler loop."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import Awaitable, Callable, Optional

from croniter import CroniterBadCronError, croniter

from tg_signer.runtime.models import SchedulePlan, isoformat, parse_iso
from tg_signer.runtime.store import RuntimeStore
from tg_signer.utils import get_now, get_timezone

logger = logging.getLogger("tg-signer.runtime")

RunPlanFn = Callable[[SchedulePlan, int, str], Awaitable[None]]


def normalize_schedule_expr(expr: str) -> str:
    """Accept HH:MM[:SS] or crontab; return crontab string."""
    text = (expr or "").replace("：", ":").strip()
    if not text:
        raise ValueError("empty schedule expression")
    try:
        t = dt_time.fromisoformat(text)
        return f"{t.minute} {t.hour} * * *"
    except ValueError:
        pass
    try:
        croniter(text)
    except (KeyError, ValueError, CroniterBadCronError) as exc:
        raise ValueError(f"invalid schedule expression: {expr}") from exc
    return text


def compute_next_run(
    schedule_expr: str,
    *,
    base: datetime | None = None,
    random_seconds: int = 0,
) -> datetime:
    cron_expr = normalize_schedule_expr(schedule_expr)
    now = base or get_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=get_timezone())
    it = croniter(cron_expr, now)
    next_dt: datetime = it.next(datetime)
    if next_dt.tzinfo is None:
        next_dt = next_dt.replace(tzinfo=now.tzinfo or get_timezone())
    jitter = random.randint(0, max(0, int(random_seconds or 0)))
    if jitter:
        next_dt = next_dt + timedelta(seconds=jitter)
    return next_dt


class Scheduler:
    """Tick-based scheduler with per-account serial execution."""

    def __init__(
        self,
        store: RuntimeStore,
        run_plan: RunPlanFn,
        *,
        tick_seconds: float = 2.0,
    ) -> None:
        self.store = store
        self.run_plan = run_plan
        self.tick_seconds = tick_seconds
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._inflight: set[int] = set()
        self.last_tick_at: Optional[str] = None

    def _lock_for(self, account: str) -> asyncio.Lock:
        lock = self._account_locks.get(account)
        if lock is None:
            lock = asyncio.Lock()
            self._account_locks[account] = lock
        return lock

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        self._ensure_next_runs()
        self._task = asyncio.create_task(self._loop(), name="tg-signer-scheduler")

    async def stop(self) -> None:
        self._stopping = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _ensure_next_runs(self) -> None:
        now = get_now()
        for plan in self.store.list_plans(enabled_only=True):
            if not plan.next_run_at:
                nxt = compute_next_run(
                    plan.schedule_expr,
                    base=now,
                    random_seconds=plan.random_seconds,
                )
                plan.next_run_at = isoformat(nxt)
                self.store.update_plan(plan)

    async def _loop(self) -> None:
        logger.info("scheduler started tick=%ss", self.tick_seconds)
        try:
            while not self._stopping:
                try:
                    await self.tick()
                except Exception:  # noqa: BLE001
                    logger.exception("scheduler tick failed")
                await asyncio.sleep(self.tick_seconds)
        except asyncio.CancelledError:
            logger.info("scheduler cancelled")
            raise

    async def tick(self) -> None:
        now = get_now()
        self.last_tick_at = isoformat(now)
        for plan in self.store.list_plans(enabled_only=True):
            if plan.id is None:
                continue
            if plan.id in self._inflight:
                continue
            account = self.store.get_account(plan.account)
            if account is not None and not account.enabled:
                continue
            due = self._is_due(plan, now)
            if not due:
                continue
            self._inflight.add(plan.id)
            asyncio.create_task(
                self._execute_plan(plan),
                name=f"plan-{plan.id}-{plan.account}",
            )

    def _is_due(self, plan: SchedulePlan, now: datetime) -> bool:
        if not plan.next_run_at:
            return True
        nxt = parse_iso(plan.next_run_at)
        if nxt is None:
            return True
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=now.tzinfo or get_timezone())
        return now >= nxt

    async def _execute_plan(self, plan: SchedulePlan, *, source: str = "scheduler") -> None:
        assert plan.id is not None
        lock = self._lock_for(plan.account)
        attempt = 1
        max_retries = max(0, int(plan.max_retries or 0))
        try:
            async with lock:
                while True:
                    try:
                        await self.run_plan(plan, attempt, source)
                        break
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "plan %s failed attempt=%s: %s",
                            plan.id,
                            attempt,
                            exc,
                        )
                        if attempt > max_retries:
                            break
                        attempt += 1
                        await asyncio.sleep(min(30, 2 ** min(attempt, 5)))
                # advance next_run regardless of success (avoid tight loop)
                now = get_now()
                fresh = self.store.get_plan(plan.id) or plan
                fresh.last_run_at = isoformat(now)
                fresh.next_run_at = isoformat(
                    compute_next_run(
                        fresh.schedule_expr,
                        base=now,
                        random_seconds=fresh.random_seconds,
                    )
                )
                self.store.update_plan(fresh)
        finally:
            self._inflight.discard(plan.id)

    async def run_now(self, plan_id: int) -> None:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise KeyError(f"plan not found: {plan_id}")
        if plan.id in self._inflight:
            raise RuntimeError("plan already running")
        self._inflight.add(plan.id)
        await self._execute_plan(plan, source="manual")
