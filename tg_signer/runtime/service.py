"""Runtime service: owns store, scheduler and optional default runner."""

from __future__ import annotations

import logging
from typing import Optional

from tg_signer.runtime.models import JobStatus, RuntimeStats, SchedulePlan, isoformat
from tg_signer.runtime.runner import JobRunner
from tg_signer.runtime.scheduler import (
    Scheduler,
    compute_next_run,
    normalize_schedule_expr,
)
from tg_signer.runtime.store import RuntimeStore
from tg_signer.utils import get_now

logger = logging.getLogger("tg-signer.runtime")

_RUNTIME: Optional["RuntimeService"] = None


def get_runtime() -> Optional["RuntimeService"]:
    return _RUNTIME


def set_runtime(runtime: Optional["RuntimeService"]) -> None:
    global _RUNTIME
    _RUNTIME = runtime


class RuntimeService:
    def __init__(
        self,
        workdir: str,
        session_dir: str = ".",
        *,
        default_proxy: str | dict | None = None,
        num_of_dialogs: int = 50,
        tick_seconds: float = 2.0,
        loop=None,
        auto_start: bool = False,
    ) -> None:
        self.workdir = workdir
        self.session_dir = session_dir
        self.store = RuntimeStore(workdir, session_dir=session_dir)
        self.runner = JobRunner(
            self.store,
            workdir=workdir,
            session_dir=session_dir,
            default_proxy=default_proxy,
            num_of_dialogs=num_of_dialogs,
            loop=loop,
        )
        self.scheduler = Scheduler(
            self.store,
            self.runner.run_plan,
            tick_seconds=tick_seconds,
        )
        if auto_start:
            # caller must await start()
            pass

    async def start(self) -> None:
        await self.scheduler.start()
        set_runtime(self)
        logger.info("RuntimeService started workdir=%s", self.workdir)

    async def stop(self) -> None:
        await self.scheduler.stop()
        if get_runtime() is self:
            set_runtime(None)
        logger.info("RuntimeService stopped")

    def prepare_plan(
        self, plan: SchedulePlan, *, force_next: bool = False
    ) -> SchedulePlan:
        plan.schedule_expr = normalize_schedule_expr(plan.schedule_expr)
        if plan.enabled and (force_next or not plan.next_run_at):
            plan.next_run_at = isoformat(
                compute_next_run(
                    plan.schedule_expr,
                    base=get_now(),
                    random_seconds=plan.random_seconds,
                )
            )
        return plan

    def create_plan(self, plan: SchedulePlan) -> SchedulePlan:
        return self.store.create_plan(self.prepare_plan(plan, force_next=True))

    def update_plan(self, plan: SchedulePlan) -> SchedulePlan:
        force_next = False
        if plan.id is not None:
            old = self.store.get_plan(plan.id)
            if old is not None:
                old_expr = normalize_schedule_expr(old.schedule_expr)
                new_expr = normalize_schedule_expr(plan.schedule_expr)
                if (
                    old_expr != new_expr
                    or int(old.random_seconds or 0) != int(plan.random_seconds or 0)
                    or (not old.enabled and plan.enabled)
                ):
                    force_next = True
        return self.store.update_plan(
            self.prepare_plan(plan, force_next=force_next)
        )

    async def run_plan_now(self, plan_id: int) -> None:
        await self.scheduler.run_now(plan_id)

    def stats(self) -> RuntimeStats:
        plans = self.store.list_plans()
        enabled = [p for p in plans if p.enabled]
        return RuntimeStats(
            scheduled=len(enabled),
            running=self.store.count_running_jobs(),
            failed=self.store.count_jobs_by_status(JobStatus.FAILED),
            total_plans=len(plans),
            last_tick_at=self.scheduler.last_tick_at,
            scheduler_running=self.scheduler.running,
        )
