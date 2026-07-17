"""SQLite persistence for runtime accounts, plans and job history."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, List, Optional

from tg_signer.runtime.models import (
    AccountMeta,
    JobRun,
    JobStatus,
    SchedulePlan,
    isoformat,
)
from tg_signer.utils import get_now


class RuntimeStore:
    """Share ``data.sqlite3`` with SignRecordStore; own schema v2+ tables."""

    DB_FILENAME = "data.sqlite3"
    # v1 owned by SignRecordStore; runtime tables start at v2
    RUNTIME_SCHEMA_VERSION = 2

    def __init__(self, workdir: str | Path, session_dir: str | Path | None = None):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.session_dir = Path(session_dir) if session_dir is not None else Path(".")

    @property
    def db_path(self) -> Path:
        return self.workdir / self.DB_FILENAME

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        # Ensure v1 sign_records exists when runtime boots first.
        if version < 1:
            from tg_signer.sign_record_store import SignRecordStore

            SignRecordStore._migrate_to_v1(conn)
            conn.execute("PRAGMA user_version = 1")
            version = 1
        if version < 2:
            self._migrate_to_v2(conn)
            conn.execute("PRAGMA user_version = 2")
        conn.commit()

    @staticmethod
    def _migrate_to_v2(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                name TEXT PRIMARY KEY,
                proxy TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                labels TEXT NOT NULL DEFAULT '',
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS schedule_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account TEXT NOT NULL,
                task_type TEXT NOT NULL,
                task_ref TEXT NOT NULL,
                schedule_expr TEXT NOT NULL,
                timezone TEXT,
                random_seconds INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                max_retries INTEGER NOT NULL DEFAULT 1,
                next_run_at TEXT,
                last_run_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_schedule_plans_enabled_next
            ON schedule_plans(enabled, next_run_at);

            CREATE TABLE IF NOT EXISTS job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER,
                account TEXT NOT NULL,
                task_type TEXT NOT NULL,
                task_ref TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                started_at TEXT,
                finished_at TEXT,
                error TEXT,
                source TEXT NOT NULL DEFAULT 'scheduler'
            );

            CREATE INDEX IF NOT EXISTS idx_job_runs_started
            ON job_runs(started_at DESC);
            """
        )

    # --- accounts ---------------------------------------------------------

    def upsert_account(self, account: AccountMeta) -> AccountMeta:
        now = isoformat(get_now())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts(name, proxy, enabled, labels, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    proxy=excluded.proxy,
                    enabled=excluded.enabled,
                    labels=excluded.labels,
                    updated_at=excluded.updated_at
                """,
                (
                    account.name,
                    account.proxy,
                    1 if account.enabled else 0,
                    account.labels or "",
                    now,
                ),
            )
            conn.commit()
        account.updated_at = now
        return account

    def get_account(self, name: str) -> Optional[AccountMeta]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE name = ?", (name,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_account(row)

    def list_accounts(self) -> List[AccountMeta]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM accounts ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def delete_account(self, name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM accounts WHERE name = ?", (name,))
            conn.commit()
            return cur.rowcount > 0

    def discover_session_accounts(
        self, session_dir: str | Path | None = None
    ) -> List[str]:
        root = Path(session_dir) if session_dir is not None else self.session_dir
        if not root.is_dir():
            return []
        names: set[str] = set()
        for path in root.iterdir():
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(".session"):
                names.add(name[: -len(".session")])
            elif name.endswith(".session_string"):
                names.add(name[: -len(".session_string")])
        return sorted(names, key=str.lower)

    def list_accounts_merged(
        self, session_dir: str | Path | None = None
    ) -> List[AccountMeta]:
        """Merge DB metadata with session files on disk."""
        discovered = self.discover_session_accounts(session_dir)
        by_name = {a.name: a for a in self.list_accounts()}
        for name in discovered:
            if name in by_name:
                by_name[name].session_present = True
            else:
                by_name[name] = AccountMeta(name=name, session_present=True)
        # keep DB-only accounts (no session yet)
        for name, acc in list(by_name.items()):
            if name not in discovered and not acc.session_present:
                acc.session_present = False
        return sorted(by_name.values(), key=lambda a: a.name.lower())

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> AccountMeta:
        return AccountMeta(
            name=row["name"],
            proxy=row["proxy"],
            enabled=bool(row["enabled"]),
            labels=row["labels"] or "",
            updated_at=row["updated_at"],
            session_present=False,
        )

    # --- plans ------------------------------------------------------------

    def create_plan(self, plan: SchedulePlan) -> SchedulePlan:
        now = isoformat(get_now())
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO schedule_plans(
                    account, task_type, task_ref, schedule_expr, timezone,
                    random_seconds, enabled, max_retries, next_run_at, last_run_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.account,
                    plan.task_type,
                    plan.task_ref,
                    plan.schedule_expr,
                    plan.timezone,
                    int(plan.random_seconds or 0),
                    1 if plan.enabled else 0,
                    int(plan.max_retries if plan.max_retries is not None else 1),
                    plan.next_run_at,
                    plan.last_run_at,
                    now,
                    now,
                ),
            )
            conn.commit()
            plan.id = int(cur.lastrowid)
        plan.created_at = now
        plan.updated_at = now
        return plan

    def update_plan(self, plan: SchedulePlan) -> SchedulePlan:
        if plan.id is None:
            raise ValueError("plan.id is required for update")
        now = isoformat(get_now())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE schedule_plans SET
                    account=?, task_type=?, task_ref=?, schedule_expr=?,
                    timezone=?, random_seconds=?, enabled=?, max_retries=?,
                    next_run_at=?, last_run_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    plan.account,
                    plan.task_type,
                    plan.task_ref,
                    plan.schedule_expr,
                    plan.timezone,
                    int(plan.random_seconds or 0),
                    1 if plan.enabled else 0,
                    int(plan.max_retries if plan.max_retries is not None else 1),
                    plan.next_run_at,
                    plan.last_run_at,
                    now,
                    plan.id,
                ),
            )
            conn.commit()
        plan.updated_at = now
        return plan

    def get_plan(self, plan_id: int) -> Optional[SchedulePlan]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM schedule_plans WHERE id = ?", (plan_id,)
            ).fetchone()
        return self._row_to_plan(row) if row else None

    def list_plans(self, enabled_only: bool = False) -> List[SchedulePlan]:
        sql = "SELECT * FROM schedule_plans"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row_to_plan(r) for r in rows]

    def delete_plan(self, plan_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM schedule_plans WHERE id = ?", (plan_id,))
            conn.commit()
            return cur.rowcount > 0

    def set_plan_enabled(self, plan_id: int, enabled: bool) -> bool:
        now = isoformat(get_now())
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE schedule_plans SET enabled=?, updated_at=? WHERE id=?",
                (1 if enabled else 0, now, plan_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def export_plans(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.list_plans()]

    def import_plans(
        self, items: Iterable[dict[str, Any]], *, replace: bool = False
    ) -> int:
        from tg_signer.runtime.scheduler import (
            compute_next_run,
            normalize_schedule_expr,
        )

        count = 0
        with self._connect() as conn:
            if replace:
                conn.execute("DELETE FROM schedule_plans")
            for raw in items:
                plan = SchedulePlan.from_dict(raw)
                now = isoformat(get_now())
                try:
                    plan.schedule_expr = normalize_schedule_expr(plan.schedule_expr)
                except ValueError:
                    pass
                # Avoid mass immediate fire when imports omit next_run_at.
                if plan.enabled and not plan.next_run_at:
                    plan.next_run_at = isoformat(
                        compute_next_run(
                            plan.schedule_expr,
                            base=get_now(),
                            random_seconds=plan.random_seconds,
                        )
                    )
                conn.execute(
                    """
                    INSERT INTO schedule_plans(
                        account, task_type, task_ref, schedule_expr, timezone,
                        random_seconds, enabled, max_retries, next_run_at, last_run_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.account,
                        plan.task_type,
                        plan.task_ref,
                        plan.schedule_expr,
                        plan.timezone,
                        int(plan.random_seconds or 0),
                        1 if plan.enabled else 0,
                        int(plan.max_retries if plan.max_retries is not None else 1),
                        plan.next_run_at,
                        plan.last_run_at,
                        plan.created_at or now,
                        now,
                    ),
                )
                count += 1
            conn.commit()
        return count

    def export_plans_json(self) -> str:
        return json.dumps(
            {"version": 1, "plans": self.export_plans()},
            ensure_ascii=False,
            indent=2,
        )

    def import_plans_json(self, text: str, *, replace: bool = False) -> int:
        data = json.loads(text)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("plans") or []
        else:
            raise ValueError("invalid plans JSON")
        return self.import_plans(items, replace=replace)

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> SchedulePlan:
        return SchedulePlan(
            id=row["id"],
            account=row["account"],
            task_type=row["task_type"],
            task_ref=row["task_ref"],
            schedule_expr=row["schedule_expr"],
            timezone=row["timezone"],
            random_seconds=row["random_seconds"] or 0,
            enabled=bool(row["enabled"]),
            max_retries=row["max_retries"] if row["max_retries"] is not None else 1,
            next_run_at=row["next_run_at"],
            last_run_at=row["last_run_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # --- job runs ---------------------------------------------------------

    def create_job_run(self, job: JobRun) -> JobRun:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO job_runs(
                    plan_id, account, task_type, task_ref, status, attempt,
                    started_at, finished_at, error, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.plan_id,
                    job.account,
                    job.task_type,
                    job.task_ref,
                    job.status.value
                    if isinstance(job.status, JobStatus)
                    else job.status,
                    int(job.attempt),
                    job.started_at,
                    job.finished_at,
                    job.error,
                    job.source or "scheduler",
                ),
            )
            conn.commit()
            job.id = int(cur.lastrowid)
        return job

    def update_job_run(self, job: JobRun) -> JobRun:
        if job.id is None:
            raise ValueError("job.id is required for update")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE job_runs SET
                    status=?, attempt=?, started_at=?, finished_at=?, error=?
                WHERE id=?
                """,
                (
                    job.status.value
                    if isinstance(job.status, JobStatus)
                    else job.status,
                    int(job.attempt),
                    job.started_at,
                    job.finished_at,
                    job.error,
                    job.id,
                ),
            )
            conn.commit()
        return job

    def list_recent_jobs(self, limit: int = 50) -> List[JobRun]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM job_runs
                ORDER BY COALESCE(started_at, '') DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def count_jobs_by_status(self, status: JobStatus | str) -> int:
        value = status.value if isinstance(status, JobStatus) else status
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM job_runs WHERE status = ?",
                (value,),
            ).fetchone()
        return int(row["c"] if row else 0)

    def count_running_jobs(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM job_runs WHERE status IN ('running', 'queued', 'retrying')"
            ).fetchone()
        return int(row["c"] if row else 0)

    def count_failed_recent(self, limit_scan: int = 100) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM (
                    SELECT status FROM job_runs
                    ORDER BY COALESCE(started_at, '') DESC, id DESC
                    LIMIT ?
                ) WHERE status = 'failed'
                """,
                (int(limit_scan),),
            ).fetchone()
        return int(row["c"] if row else 0)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRun:
        return JobRun(
            id=row["id"],
            plan_id=row["plan_id"],
            account=row["account"],
            task_type=row["task_type"],
            task_ref=row["task_ref"],
            status=JobStatus(row["status"]),
            attempt=row["attempt"] or 1,
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            source=row["source"] or "scheduler",
        )
