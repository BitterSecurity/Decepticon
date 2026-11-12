"""Regression: ``SandboxBase._normalize_workspace_path`` must reject traversal.

A "." or ".." path component passes the per-segment character class, so without
the ``posixpath.normpath`` guard ``/workspace/../../etc`` was returned verbatim
and escaped the per-engagement workspace subtree (the sibling
EngagementFilesystem layer already guarded this; the sandbox_kernel + bash
callers did not). The sanitizer must fail closed to ``/workspace``.
"""

from __future__ import annotations

import pytest
from decepticon_sandbox.kernel.base import SandboxBase

_norm = SandboxBase._normalize_workspace_path


@pytest.mark.parametrize(
    "path",
    [
        "/workspace",
        "/workspace/eng1",
        "/workspace/a/b",
        "/workspace/ok_dir-1.2",
        "/workspace/UPPER_and-1.2.3",
        "/workspace/trailing/",  # rstrip -> still valid
    ],
)
def test_legit_paths_preserved(path: str) -> None:
    assert _norm(path) == path.rstrip("/")


@pytest.mark.parametrize(
    "path",
    [
        "/workspace/../../etc",
        "/workspace/../etc",
        "/workspace/a/../../etc",
        "/workspace/..",
        "/workspace/./x",
        "/workspace/a/./b",
        "/workspace//x",  # empty component
        "/etc/passwd",
        "/workspaceevil",  # not under the /workspace/ prefix
        "../../etc",
    ],
)
def test_traversal_and_escapes_fail_closed(path: str) -> None:
    assert _norm(path) == "/workspace"


def test_none_and_blank_default_to_workspace() -> None:
    assert _norm(None) == "/workspace"
    assert _norm("   ") == "/workspace"
