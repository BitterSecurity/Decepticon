"""Unit tests for the engine CLI (scope_check wiring, fetch/search envelopes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decepticon.sandbox_web import __main__ as cli
from decepticon.sandbox_web.fetch_chain import FetchResult
from decepticon.sandbox_web.validators import Verdict


def _write_roe(tmp_path: Path, machine_enforcement: dict) -> Path:
    plan = tmp_path / "plan"
    plan.mkdir(parents=True, exist_ok=True)
    (plan / "roe.json").write_text(json.dumps({"machine_enforcement": machine_enforcement}))
    return tmp_path


def test_scope_check_none_without_workspace() -> None:
    assert cli._build_scope_check(None) is None


def test_scope_check_enforces_in_scope(tmp_path: Path) -> None:
    ws = _write_roe(
        tmp_path,
        {"mode": "enforce", "in_scope": ["*.target.test"]},
    )
    check = cli._build_scope_check(str(ws))
    assert check is not None
    assert check("https://app.target.test/login") is True
    assert check("https://example.com/x") is False  # not in scope → refused


def test_scope_check_accepts_signed_root_url(tmp_path: Path) -> None:
    ws = _write_roe(
        tmp_path,
        {"mode": "enforce", "in_scope": [{"target": "https://decepticon.red/", "type": "auto"}]},
    )
    check = cli._build_scope_check(str(ws))

    assert check is not None
    assert check("https://decepticon.red/") is True
    assert check("https://decepticon.red.evil.test/") is False


def test_fetch_command_emits_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "fetch",
        lambda *a, **k: FetchResult(ok=True, content="hello", verdict=Verdict.WEAK_OK.value),
    )
    rc = cli.main(["fetch", "https://example.com/x", "--no-playwright"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["content"] == "hello"
    assert out["content_truncated"] is False


def test_fetch_offloads_large_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    big = "z" * 20_000
    monkeypatch.setattr(
        cli, "fetch", lambda *a, **k: FetchResult(ok=True, content=big, verdict="weak_ok")
    )
    rc = cli.main(
        ["fetch", "https://example.com/x", "--workspace", str(tmp_path), "--no-playwright"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["content_truncated"] is True
    assert out["content_path"] is not None
    assert Path(out["content_path"]).read_text() == big
    assert len(out["content"]) == 15_000


def test_search_rejects_disallowed_provider(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["search", "hello", "--provider", "google"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert "allowlist" in out["error"]


def test_search_happy_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ddg_html = (
        '<a class="result__a" href="https://example.com/a">Hit One</a>'
        '<a class="result__snippet" href="x">snip</a>'
    )
    monkeypatch.setattr(
        cli, "fetch", lambda *a, **k: FetchResult(ok=True, content=ddg_html, verdict="weak_ok")
    )
    rc = cli.main(["search", "claude code", "--no-playwright"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["provider"] == "duckduckgo"
    assert out["count"] == 1
    assert out["hits"][0]["url"] == "https://example.com/a"


def test_search_provider_fetch_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "fetch", lambda *a, **k: FetchResult(ok=False, summary="all challenged")
    )
    rc = cli.main(["search", "x", "--no-playwright"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert "provider fetch failed" in out["error"]
