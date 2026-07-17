"""Execute a schedule plan once without entering long-lived run loops."""

from __future__ import annotations

import logging
from typing import Any

from tg_signer.core import UserSigner, get_proxy
from tg_signer.runtime.models import JobRun, JobStatus, SchedulePlan, isoformat
from tg_signer.runtime.store import RuntimeStore
from tg_signer.utils import get_now

logger = logging.getLogger("tg-signer.runtime")


class JobRunner:
    def __init__(
        self,
        store: RuntimeStore,
        *,
        workdir: str,
        session_dir: str = ".",
        default_proxy: str | dict | None = None,
        num_of_dialogs: int = 50,
        loop=None,
    ) -> None:
        self.store = store
        self.workdir = workdir
        self.session_dir = session_dir
        self.default_proxy = default_proxy
        self.num_of_dialogs = num_of_dialogs
        self.loop = loop

    def resolve_proxy(self, account: str) -> Any:
        meta = self.store.get_account(account)
        if meta and meta.proxy:
            return get_proxy(meta.proxy)
        if isinstance(self.default_proxy, str):
            return get_proxy(self.default_proxy)
        return self.default_proxy

    async def run_plan(
        self, plan: SchedulePlan, attempt: int = 1, source: str = "scheduler"
    ) -> None:
        job = JobRun(
            id=None,
            plan_id=plan.id,
            account=plan.account,
            task_type=plan.task_type,
            task_ref=plan.task_ref,
            status=JobStatus.RUNNING,
            attempt=attempt,
            started_at=isoformat(get_now()),
            source=source,
        )
        self.store.create_job_run(job)
        try:
            if plan.task_type == "sign":
                await self._run_sign(plan)
            elif plan.task_type == "automation":
                await self._run_automation(plan)
            elif plan.task_type == "monitor":
                raise NotImplementedError(
                    "monitor 计划执行尚未支持；请使用 CLI 常驻 monitor"
                )
            else:
                raise ValueError(f"unknown task_type: {plan.task_type}")
            job.status = JobStatus.COMPLETED
            job.finished_at = isoformat(get_now())
            job.error = None
            self.store.update_job_run(job)
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.FAILED
            job.finished_at = isoformat(get_now())
            job.error = str(exc)
            self.store.update_job_run(job)
            raise

    async def _run_sign(self, plan: SchedulePlan) -> None:
        proxy = self.resolve_proxy(plan.account)
        signer = UserSigner(
            task_name=plan.task_ref,
            account=plan.account,
            proxy=proxy,
            session_dir=self.session_dir,
            workdir=self.workdir,
            loop=self.loop,
        )
        # only_once + force_rerun: schedule plan owns timing; ignore config.sign_at gate
        # so a shared task config's fixed sign_at does not skip plan-driven runs.
        await signer.run_once(self.num_of_dialogs, force_rerun=True)

    async def _run_automation(self, plan: SchedulePlan) -> None:
        # P1: thin adapter — run UserAutomation once if available
        try:
            from tg_signer.automation.engine import UserAutomation
        except ImportError as exc:  # pragma: no cover
            raise NotImplementedError("automation module unavailable") from exc

        proxy = self.resolve_proxy(plan.account)
        worker = UserAutomation(
            task_name=plan.task_ref,
            account=plan.account,
            proxy=proxy,
            session_dir=self.session_dir,
            workdir=self.workdir,
            loop=self.loop,
        )
        run_once = getattr(worker, "run_once", None)
        if callable(run_once):
            await run_once()
            return
        # Fallback: execute startup rules only if exposed
        run_startup = getattr(worker, "run_startup_all", None) or getattr(
            worker, "run_startups", None
        )
        if callable(run_startup):
            await run_startup()
            return
        raise NotImplementedError(
            "当前 automation 仅支持常驻 run；计划触发需后续适配 run_once"
        )
