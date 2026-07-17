"""Runtime domain models for accounts, schedule plans and job runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

TaskType = Literal["sign", "automation", "monitor"]


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


@dataclass
class AccountMeta:
    name: str
    proxy: Optional[str] = None
    enabled: bool = True
    labels: str = ""
    updated_at: Optional[str] = None
    session_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SchedulePlan:
    id: Optional[int]
    account: str
    task_type: TaskType
    task_ref: str
    schedule_expr: str
    timezone: Optional[str] = None
    random_seconds: int = 0
    enabled: bool = True
    max_retries: int = 1
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SchedulePlan":
        return cls(
            id=data.get("id"),
            account=str(data["account"]),
            task_type=data.get("task_type") or "sign",
            task_ref=str(data["task_ref"]),
            schedule_expr=str(data["schedule_expr"]),
            timezone=data.get("timezone"),
            random_seconds=int(data.get("random_seconds") or 0),
            enabled=bool(data.get("enabled", True)),
            max_retries=int(data.get("max_retries") or 1),
            next_run_at=data.get("next_run_at"),
            last_run_at=data.get("last_run_at"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


@dataclass
class JobRun:
    id: Optional[int]
    plan_id: Optional[int]
    account: str
    task_type: TaskType
    task_ref: str
    status: JobStatus
    attempt: int = 1
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    source: str = "scheduler"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = (
            self.status.value if isinstance(self.status, JobStatus) else self.status
        )
        return d


@dataclass
class RuntimeStats:
    scheduled: int = 0
    running: int = 0
    failed: int = 0
    total_plans: int = 0
    last_tick_at: Optional[str] = None
    scheduler_running: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_iso(dt: str | datetime | None) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt
    text = str(dt).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def isoformat(dt: datetime | None) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()
