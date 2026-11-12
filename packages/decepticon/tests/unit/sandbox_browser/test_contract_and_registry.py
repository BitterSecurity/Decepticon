"""Browser provider contract, registry and runner CLI."""

from __future__ import annotations

import base64
import json
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from decepticon_sandbox.browser import provider as registry
from decepticon_sandbox.browser.__main__ import main
from decepticon_sandbox.browser.contract import (
    ACTIONS,
    BrowserContractError,
    BrowserRequest,
    BrowserResult,
)
from decepticon_sandbox.browser.providers.obscura import CAPABILITIES, ObscuraProvider


def _b64(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()


def test_request_roundtrip_and_validation() -> None:
    req = BrowserRequest.from_json('{"action":"navigate","args":{"url":"https://a.example/x"}}')
    assert req.action == "navigate"
    assert BrowserRequest.from_json(req.to_json()) == req


@pytest.mark.parametrize(
    "raw",
    [
        '{"action":"teleport"}',
        '{"action":"navigate","args":{"url":"file:///etc/passwd"}}',
        '{"action":"tab_new","args":{"url":"javascript:alert(1)"}}',
        '{"action":"click","args":[]}',
        "not json",
    ],
)
def test_request_rejects_invalid(raw: str) -> None:
    with pytest.raises(BrowserContractError):
        BrowserRequest.from_json(raw)


def test_result_truncates_text() -> None:
    data = json.loads(
        BrowserResult(ok=True, action="markdown", provider="p", text="x" * 50_000).to_json()
    )
    assert data["truncated"] is True
    assert len(data["text"]) < 50_000


def test_obscura_declares_only_contract_actions() -> None:
    assert CAPABILITIES <= ACTIONS
    assert CAPABILITIES == ACTIONS  # obscura implements the whole contract today


def test_builtin_provider_loads(tmp_path: Path) -> None:
    provider = registry.load_provider("obscura", tmp_path)
    assert isinstance(provider, ObscuraProvider)
    assert "obscura" in registry.available_providers()


def test_unknown_provider_is_reported(tmp_path: Path) -> None:
    with pytest.raises(registry.UnknownProviderError):
        registry.load_provider("no-such-browser", tmp_path)


class _EchoProvider:
    name = "echo"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def capabilities(self) -> frozenset[str]:
        return frozenset({"navigate"})

    def invoke(self, request: BrowserRequest) -> BrowserResult:
        return BrowserResult(
            ok=True, action=request.action, provider=self.name, text=request.args["url"]
        )

    def shutdown(self) -> None:
        pass


def _entry_points_with_echo(*, group: str):
    assert group == registry.ENTRY_POINT_GROUP
    ep = EntryPoint(name="echo", value=f"{__name__}:_EchoProvider", group=group)
    return [ep]


def test_entry_point_provider_and_capability_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(registry, "entry_points", _entry_points_with_echo)
    assert "echo" in registry.available_providers()

    rc = main(
        [
            "call",
            "--workspace",
            "/workspace/eng",
            "--provider",
            "echo",
            "--request-b64",
            _b64({"action": "navigate", "args": {"url": "https://a.example/"}}),
        ]
    )
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0 and out["ok"] and out["text"] == "https://a.example/"

    rc = main(
        [
            "call",
            "--workspace",
            "/workspace/eng",
            "--provider",
            "echo",
            "--request-b64",
            _b64({"action": "screenshot", "args": {}}),
        ]
    )
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 1 and out["error_code"] == "unsupported_action"


@pytest.mark.parametrize("workspace", ["/tmp/eng", "/workspace/../etc", "/workspacex/eng"])
def test_runner_requires_engagement_workspace(
    workspace: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["call", "--workspace", workspace, "--request-b64", _b64({"action": "capabilities"})])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 1 and out["error_code"] == "invalid_request"


def test_runner_rejects_non_base64(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["call", "--workspace", "/workspace/eng", "--request-b64", "%%%"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 1 and out["error_code"] == "invalid_request"


def test_obscura_missing_binary_is_provider_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECEPTICON_OBSCURA_BIN", str(tmp_path / "absent"))
    result = ObscuraProvider(tmp_path).invoke(
        BrowserRequest("navigate", {"url": "https://a.example/"})
    )
    assert not result.ok and result.error_code == "provider_unavailable"


@pytest.mark.parametrize("name", ["../x", "a/b", "x" * 65])
def test_obscura_storage_name_is_confined(tmp_path: Path, name: str) -> None:
    from decepticon_sandbox.browser.providers.obscura import ObscuraError

    with pytest.raises(ObscuraError):
        ObscuraProvider(tmp_path)._storage_path({"name": name})
