"""Tests for RuntimeStore (accounts / plans / jobs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tg_signer.runtime.models import AccountMeta, JobRun, JobStatus, SchedulePlan
from tg_signer.runtime.store import RuntimeStore


@pytest.fixture
def store(tmp_path: Path) -> RuntimeStore:
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    workdir = tmp_path / ".signer"
    return RuntimeStore(workdir, session_dir=session_dir)


def test_schema_and_account_crud(store: RuntimeStore):
    acc = store.upsert_account(
        AccountMeta(name="a0", proxy="socks5://127.0.0.1:1080", labels="main")
    )
    assert acc.name == "a0"
    loaded = store.get_account("a0")
    assert loaded is not None
    assert loaded.proxy == "socks5://127.0.0.1:1080"
    assert loaded.enabled is True
    assert store.delete_account("a0") is True
    assert store.get_account("a0") is None


def test_session_discovery_merge(store: RuntimeStore, tmp_path: Path):
    session_dir = store.session_dir
    (session_dir / "a0.session").write_text("", encoding="utf-8")
    (session_dir / "a1.session_string").write_text("x", encoding="utf-8")
    store.upsert_account(AccountMeta(name="a0", proxy="socks5://x"))
    merged = store.list_accounts_merged()
    names = {a.name: a for a in merged}
    assert "a0" in names and names["a0"].session_present
    assert names["a0"].proxy == "socks5://x"
    assert "a1" in names and names["a1"].session_present


def test_plan_crud_and_import_export(store: RuntimeStore):
    plan = store.create_plan(
        SchedulePlan(
            id=None,
            account="a0",
            task_type="sign",
            task_ref="sg-sign",
            schedule_expr="0 6 * * *",
            random_seconds=30,
        )
    )
    assert plan.id is not None
    plan.enabled = False
    store.update_plan(plan)
    loaded = store.get_plan(plan.id)
    assert loaded is not None
    assert loaded.enabled is False

    payload = store.export_plans_json()
    data = json.loads(payload)
    assert data["version"] == 1
    assert len(data["plans"]) == 1

    store.delete_plan(plan.id)
    assert store.list_plans() == []
    n = store.import_plans_json(payload, replace=True)
    assert n == 1
    assert len(store.list_plans()) == 1


def test_import_fills_missing_next_run(store: RuntimeStore):
    n = store.import_plans(
        [
            {
                "account": "a0",
                "task_type": "sign",
                "task_ref": "t1",
                "schedule_expr": "0 6 * * *",
                "enabled": True,
            }
        ],
        replace=True,
    )
    assert n == 1
    plan = store.list_plans()[0]
    assert plan.next_run_at is not None


def test_job_run_lifecycle(store: RuntimeStore):
    plan = store.create_plan(
        SchedulePlan(
            id=None,
            account="a0",
            task_type="sign",
            task_ref="t1",
            schedule_expr="06:00:00",
        )
    )
    job = store.create_job_run(
        JobRun(
            id=None,
            plan_id=plan.id,
            account="a0",
            task_type="sign",
            task_ref="t1",
            status=JobStatus.RUNNING,
            attempt=1,
            started_at="2026-01-01T06:00:00",
        )
    )
    assert job.id is not None
    job.status = JobStatus.COMPLETED
    job.finished_at = "2026-01-01T06:01:00"
    store.update_job_run(job)
    recent = store.list_recent_jobs(10)
    assert recent[0].status == JobStatus.COMPLETED
    assert store.count_jobs_by_status(JobStatus.COMPLETED) == 1
