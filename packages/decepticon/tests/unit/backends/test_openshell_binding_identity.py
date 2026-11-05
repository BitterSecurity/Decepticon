"""Workspace collision regressions; lifecycle fixture never executes commands."""

from __future__ import annotations

import hashlib

import pytest

from decepticon.backends import openshell_local
from decepticon.backends.daemon_client import SandboxError
from decepticon.middleware.policy_compiler import sandbox_name

from .test_openshell_local import _FakeClient, _patch_client, _workspace

pytest.importorskip("openshell")


@pytest.fixture(autouse=True)
def isolated_bindings(monkeypatch):
    monkeypatch.setattr(openshell_local, "_backends", {})
    monkeypatch.setenv("DECEPTICON_OPENSHELL_WORKSPACE", "default")


def test_known_legacy_workspace_prefix_collision_gets_distinct_names():
    first = "/workspace/owned-identity-fixture-159"
    second = "/workspace/owned-identity-fixture-226"
    assert (
        hashlib.sha256(first.encode()).hexdigest()[:4]
        == hashlib.sha256(second.encode()).hexdigest()[:4]
    )
    assert sandbox_name(first, "a" * 64) != sandbox_name(second, "a" * 64)


def test_policy_tail_is_part_of_identity_not_only_first_eight_characters():
    assert sandbox_name("/workspace/owned", "a" * 8 + "0" * 56) != sandbox_name(
        "/workspace/owned", "a" * 8 + "f" * 56
    )


def test_same_policy_legacy_workspace_collision_does_not_reuse_runtime(tmp_path, monkeypatch):
    prefixes = {}
    pair = None
    for index in range(65537):
        path = tmp_path / f"owned-{index}"
        prefix = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:4]
        if prefix in prefixes:
            pair = prefixes[prefix], path
            break
        prefixes[prefix] = path
    assert pair is not None
    first, second = [_workspace(path) for path in pair]
    client = _FakeClient()
    _patch_client(monkeypatch, client)
    a = openshell_local.resolve_local_binding({"configurable": {"workspace_path": str(first)}})
    b = openshell_local.resolve_local_binding({"configurable": {"workspace_path": str(second)}})
    assert a.current_admission().sandbox_id != b.current_admission().sandbox_id
    assert len(client._sandboxes) == 2


def test_even_forced_name_collision_cannot_attach_another_binding(tmp_path, monkeypatch):
    first, second = [_workspace(tmp_path / name) for name in ("first", "second")]
    client = _FakeClient()
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(
        openshell_local, "sandbox_name", lambda *args, **kwargs: "dcpt-forced-fixture"
    )
    original = openshell_local.resolve_local_binding(
        {"configurable": {"workspace_path": str(first)}}
    )
    calls = len(client.sessions[0].calls)
    with pytest.raises(SandboxError, match="binding"):
        openshell_local.resolve_local_binding({"configurable": {"workspace_path": str(second)}})
    assert len(client._sandboxes) == 1
    assert len(client.sessions) == 1
    assert len(client.sessions[0].calls) == calls
    original.check_admission()


def test_cached_binding_label_revocation_refuses_before_exec(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    client = _FakeClient()
    _patch_client(monkeypatch, client)
    backend = openshell_local.resolve_local_binding(
        {"configurable": {"workspace_path": str(workspace)}}
    )
    before = len(client.sessions[0].calls)
    client.ref.labels.clear()
    with pytest.raises(SandboxError, match="admission|unavailable"):
        backend.execute("must-not-execute")
    assert len(client.sessions[0].calls) == before


def test_changed_approved_bytes_with_same_compiled_policy_get_new_runtime(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    client = _FakeClient()
    _patch_client(monkeypatch, client)
    config = {"configurable": {"workspace_path": str(workspace)}}
    first = openshell_local.resolve_local_binding(config)
    original_id = first.current_admission().sandbox_id
    roe = workspace / "plan" / "roe.json"
    roe.write_bytes(roe.read_bytes() + b"\n")
    second = openshell_local.resolve_local_binding(config)
    with pytest.raises(SandboxError, match="unavailable"):
        first.check_admission()
    assert original_id != second.current_admission().sandbox_id
    assert len(client._sandboxes) == 2


def test_reconnect_same_full_binding_reuses_only_its_existing_runtime(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    client = _FakeClient()
    _patch_client(monkeypatch, client)
    config = {"configurable": {"workspace_path": str(workspace)}}
    first = openshell_local.resolve_local_binding(config).current_admission().sandbox_id
    openshell_local._backends.clear()
    second = openshell_local.resolve_local_binding(config).current_admission().sandbox_id
    assert first == second
    assert len(client._sandboxes) == 1
    label = client.ref.labels[openshell_local._BINDING_LABEL]
    assert len(label) == 52 and all(char in "abcdefghijklmnopqrstuvwxyz234567" for char in label)


def test_unlabeled_existing_runtime_is_preserved_without_adoption_or_exec(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    client = _FakeClient()
    _patch_client(monkeypatch, client)
    config = {"configurable": {"workspace_path": str(workspace)}}
    original = openshell_local.resolve_local_binding(config).current_admission().sandbox_id
    calls = len(client.sessions[0].calls)
    openshell_local._backends.clear()
    client.ref.labels.clear()
    with pytest.raises(SandboxError, match="binding"):
        openshell_local.resolve_local_binding(config)
    assert client.ref.id == original
    assert len(client._sandboxes) == len(client.sessions) == 1
    assert len(client.sessions[0].calls) == calls
    assert client.ref.labels == {}


@pytest.mark.parametrize("digest", ["", "a" * 8, "a" * 63, "A" * 64, "g" * 64])
def test_binding_rejects_partial_or_noncanonical_policy_identity(digest):
    with pytest.raises(ValueError, match="Full canonical"):
        sandbox_name("/workspace/owned", digest)
