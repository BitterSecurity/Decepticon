"""Typed agent browser tools: sandbox dispatch, gating and surface ownership."""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from decepticon.agents import build
from decepticon.middleware.untrusted_output import UNTRUSTED_TOOL_NAMES
from decepticon.tools.browser import agent_tools
from decepticon.tools.browser.names import AGENT_BROWSER_TOOL_NAMES

_PKG_ROOT = Path(agent_tools.__file__).resolve().parents[3]


@dataclass
class _ExecResp:
    output: str
    exit_code: int = 0


class _ShellSandbox:
    """Executes the runner command for real (local subprocess)."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str, *, timeout: int | None = None) -> _ExecResp:
        self.commands.append(command)
        argv = shlex.split(command)
        assert argv[:3] == ["python3", "-I", agent_tools.RUNNER_SCRIPT]
        local_script = _PKG_ROOT / "decepticon" / "sandbox_browser" / "__main__.py"
        proc = subprocess.run(
            [sys.executable, "-I", str(local_script), *argv[3:]],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return _ExecResp(output=proc.stdout + proc.stderr, exit_code=proc.returncode)


def _install(monkeypatch: pytest.MonkeyPatch) -> _ShellSandbox:
    sandbox = _ShellSandbox()
    monkeypatch.setattr(agent_tools, "get_sandbox", lambda _config=None: sandbox)
    monkeypatch.setattr(agent_tools, "_workspace_path_from_config", lambda _c: "/workspace/eng1")
    return sandbox


def _decoded_request(command: str) -> dict:
    argv = shlex.split(command)
    return json.loads(base64.urlsafe_b64decode(argv[argv.index("--request-b64") + 1]))


def _tool(name: str):
    return next(t for t in agent_tools.AGENT_BROWSER_TOOLS if t.name == name)


async def test_capabilities_round_trip_through_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _install(monkeypatch)
    out = await _tool("browser_capabilities").ainvoke({})
    assert out.startswith("[browser capabilities via obscura] OK")
    assert "navigate" in out
    argv = shlex.split(sandbox.commands[0])
    assert argv[argv.index("--workspace") + 1] == "/workspace/eng1"
    assert argv[argv.index("--provider") + 1] == "obscura"


async def test_typed_args_become_contract_request(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _install(monkeypatch)
    monkeypatch.setenv(agent_tools.PROVIDER_ENV, "no-such-browser")
    out = await _tool("browser_fill_form").ainvoke(
        {"fields": [{"selector": "#u", "value": "alice"}], "submit_ref": "e3"}
    )
    assert "ERROR provider_unavailable" in out
    assert _decoded_request(sandbox.commands[0]) == {
        "action": "fill_form",
        "args": {"fields": [{"selector": "#u", "value": "alice"}], "submit_ref": "e3"},
    }


async def test_runner_without_json_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken:
        def execute(self, command: str, *, timeout: int | None = None) -> _ExecResp:
            return _ExecResp(output="Traceback: boom", exit_code=1)

    monkeypatch.setattr(agent_tools, "get_sandbox", lambda _config=None: _Broken())
    monkeypatch.setattr(agent_tools, "_workspace_path_from_config", lambda _c: "/workspace/eng1")
    out = await _tool("browser_back").ainvoke({})
    assert "ERROR runner" in out and "boom" in out


def test_schemas_forbid_unknown_arguments() -> None:
    schema = _tool("browser_navigate").args_schema
    assert isinstance(schema, type)
    with pytest.raises(Exception):
        schema(url="https://a.example", headers={"x": "y"})


def test_names_registry_matches_tools() -> None:
    assert {t.name for t in agent_tools.AGENT_BROWSER_TOOLS} == AGENT_BROWSER_TOOL_NAMES


def test_provider_none_removes_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(agent_tools.PROVIDER_ENV, "none")
    assert agent_tools.agent_browser_tools() == []
    monkeypatch.setenv(agent_tools.PROVIDER_ENV, "obscura")
    assert len(agent_tools.agent_browser_tools()) == len(AGENT_BROWSER_TOOL_NAMES)


def test_browser_output_is_quarantined() -> None:
    assert AGENT_BROWSER_TOOL_NAMES <= UNTRUSTED_TOOL_NAMES


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


def test_plugin_browser_tools_take_over_the_surface() -> None:
    base = {t.name: t for t in agent_tools.AGENT_BROWSER_TOOLS}
    base["bash"] = _Named("bash")
    kept = build._yield_browser_surface(base, [_Named("browser_navigate"), _Named("browser_hover")])
    assert set(kept) == {"bash"}
    untouched = build._yield_browser_surface(base, [_Named("mitre_lookup")])
    assert untouched is base


@pytest.mark.parametrize(
    "role_module",
    [
        "decepticon.agents.standard.recon",
        "decepticon.agents.standard.osint_operator",
        "decepticon.agents.standard.exploit",
        "decepticon.agents.plugins.verifier",
    ],
)
def test_browser_capable_roles_bind_the_tools(role_module: str) -> None:
    module = __import__(role_module, fromlist=["_STANDARD_TOOLS"])
    assert AGENT_BROWSER_TOOL_NAMES <= set(module._STANDARD_TOOLS)
