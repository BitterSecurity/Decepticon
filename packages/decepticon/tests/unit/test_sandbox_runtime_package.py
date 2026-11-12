"""The sandbox runtime package stands alone and keeps its old import paths."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import warnings


def test_runtime_imports_without_the_management_package() -> None:
    """decepticon_sandbox must not import decepticon (no framework boot)."""
    code = (
        "import sys, decepticon_sandbox.kernel, decepticon_sandbox.server.managed_start, "
        "decepticon_sandbox.web.safety, decepticon_sandbox.browser.provider; "
        "print('decepticon' in sys.modules)"
    )
    env = {k: v for k, v in os.environ.items() if k != "DECEPTICON_SKIP_BOOT"}
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env, check=True
    )
    assert out.stdout.strip() == "False"


def test_legacy_import_paths_alias_the_same_modules() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy_jobs = importlib.import_module("decepticon.sandbox_kernel.jobs")
        legacy_contract = importlib.import_module("decepticon.sandbox_browser.contract")
    canonical_jobs = importlib.import_module("decepticon_sandbox.kernel.jobs")
    assert legacy_jobs is canonical_jobs
    assert legacy_jobs.BackgroundJobTracker is canonical_jobs.BackgroundJobTracker
    assert legacy_contract is importlib.import_module("decepticon_sandbox.browser.contract")
