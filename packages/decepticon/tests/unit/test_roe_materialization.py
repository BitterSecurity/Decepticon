"""Tests for materialize_engagement_roe — creation-time enforced RoE gate."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from decepticon.roe_materialization import materialize_engagement_roe


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT_WORKSPACE", str(tmp_path))
    return tmp_path


def _roe_path(workspace: Path) -> Path:
    return workspace / "plan" / "roe.json"


def _no_discovery(url: str) -> str:
    return "[]"


def _static_ct(hosts: list[str]) -> Callable[[str], str]:
    body = json.dumps([{"name_value": "\n".join(hosts)}])

    def fetch(url: str) -> str:
        return body

    return fetch


def test_writes_always_enforced_roe(workspace: Path) -> None:
    materialize_engagement_roe("eng1", ["elice.io"], fetch=_no_discovery)
    roe = json.loads(_roe_path(workspace).read_text())
    assert roe["engagement_name"] == "eng1"
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io"]


def test_no_in_scope_target_fails_closed(workspace: Path) -> None:
    with pytest.raises(ValueError, match="in-scope"):
        materialize_engagement_roe("eng2", ["", "   "], fetch=_no_discovery)
    assert not _roe_path(workspace).exists()


def test_never_overwrites_existing_roe(workspace: Path) -> None:
    destination = _roe_path(workspace)
    destination.parent.mkdir(parents=True)
    destination.write_text('{"reviewed": true}')
    materialize_engagement_roe("eng3", ["elice.io"], fetch=_no_discovery)
    assert destination.read_text() == '{"reviewed": true}'


def test_passive_discovery_enriches_in_scope(workspace: Path) -> None:
    materialize_engagement_roe("eng4", ["elice.io"], fetch=_static_ct(["app.elice.io", "evil.com"]))
    roe = json.loads(_roe_path(workspace).read_text())
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io", "app.elice.io"]


def test_wildcard_target_keeps_only_one_label_discovery(workspace: Path) -> None:
    materialize_engagement_roe(
        "eng5", ["*.elice.io"], fetch=_static_ct(["app.elice.io", "deep.app.elice.io", "elice.io"])
    )
    roe = json.loads(_roe_path(workspace).read_text())
    assert roe["machine_enforcement"]["in_scope"] == [
        "*.elice.io",
        "app.elice.io",
    ]


def test_path_target_is_rejected_by_core_normalization(workspace: Path) -> None:
    """The SAST entry was removed (#1059): a filesystem path is just a
    fragment, and a path-only engagement fails closed — no empty in-scope
    RoE is ever written."""
    scan_root = workspace / "repo"
    scan_root.mkdir()
    (scan_root / "app.py").write_text("x")
    with pytest.raises(ValueError, match="in-scope"):
        materialize_engagement_roe("eng6", [str(scan_root)], fetch=_no_discovery)
    assert not _roe_path(workspace).exists()


def test_mixed_path_and_host_targets_keep_network_scope(workspace: Path) -> None:
    scan_root = workspace / "repo-mixed"
    scan_root.mkdir()
    materialize_engagement_roe("eng7", ["elice.io", str(scan_root)], fetch=_no_discovery)
    roe = json.loads(_roe_path(workspace).read_text())
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io"]


def test_dead_network_targets_refuse_even_alongside_paths(workspace: Path) -> None:
    scan_root = workspace / "repo-dead"
    scan_root.mkdir()
    with pytest.raises(ValueError, match="in-scope"):
        materialize_engagement_roe("eng8", ["*.", str(scan_root)], fetch=_no_discovery)
    assert not _roe_path(workspace).exists()


def test_nonexistent_absolute_path_is_not_a_path_target(workspace: Path) -> None:
    with pytest.raises(ValueError, match="in-scope"):
        materialize_engagement_roe("eng9", ["/nonexistent/scan/dir"], fetch=_no_discovery)
    assert not _roe_path(workspace).exists()


def test_relative_dot_target_fails_closed(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    with pytest.raises(ValueError, match="in-scope"):
        materialize_engagement_roe("eng10", ["./"], fetch=_no_discovery)
    assert not _roe_path(workspace).exists()


def test_bare_existing_fragment_is_a_host_name(workspace: Path) -> None:
    """A bare label is no longer classified as a path (no path targets since
    #1059): it becomes an ordinary host entry like any other target."""
    materialize_engagement_roe("eng11", ["src"], fetch=_no_discovery)
    roe = json.loads(_roe_path(workspace).read_text())
    assert roe["machine_enforcement"]["in_scope"] == ["src"]


def test_nonexistent_hostname_stays_network_target(workspace: Path) -> None:
    materialize_engagement_roe("eng12", ["unregistered-host.example"], fetch=_no_discovery)
    roe = json.loads(_roe_path(workspace).read_text())
    assert roe["machine_enforcement"]["in_scope"] == ["unregistered-host.example"]
