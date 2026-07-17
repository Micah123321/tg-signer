"""Scheduler-driven sign runs ignore task config sign_at for gating."""

from __future__ import annotations

import inspect

from tg_signer.core import UserSigner
from tg_signer.runtime.runner import JobRunner


def test_run_once_defaults_force_rerun_true():
    """run_once must default force_rerun so schedule plans own timing."""
    sig = inspect.signature(UserSigner.run_once)
    assert sig.parameters["force_rerun"].default is True


def test_job_runner_run_sign_source_documents_force_rerun():
    """Source of JobRunner._run_sign must force_rerun so config.sign_at is not a gate."""
    src = inspect.getsource(JobRunner._run_sign)
    assert "force_rerun=True" in src
    assert "run_once" in src
