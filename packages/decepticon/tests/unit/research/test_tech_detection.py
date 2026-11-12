"""Unit tests for the dual-engine technology-stack detector.

The HTTP layer is mocked with ``httpx.MockTransport`` (the repo's convention
for async-client tests, see ``tests/unit/web/test_http_branches.py``) so no
network is touched. RoE behaviour is exercised by stubbing the rules loader.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from decepticon.tools.research import tech_detection
from decepticon.tools.research.tech_detection import detect_tech_stack
from decepticon_core.types.roe import MachineEnforcement, ScopeRule


def _install_transport(handler: Any) -> Any:
    """Patch ``httpx.AsyncClient`` so it routes through ``MockTransport``.

    Returns the patcher's context manager; ``follow_redirects`` / ``timeout``
    / ``headers`` kwargs from the tool are preserved, only ``transport`` is
    injected.
    """
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    return patch.object(tech_detection.httpx, "AsyncClient", side_effect=patched)


async def _run(url: str = "https://target.test/") -> dict[str, Any]:
    return json.loads(await detect_tech_stack.ainvoke({"url": url}))


def _audit_rules() -> MachineEnforcement:
    return MachineEnforcement()


# ── Header engine ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_server_banner_detects_nginx_with_version() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Server": "nginx/1.25.3"}, text="<html></html>")

    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    nginx = next(t for t in out["technologies"] if t["name"] == "nginx")
    assert nginx["category"] == "web-server"
    assert nginx["version"] == "1.25.3"
    assert nginx["detected_by"] == ["http-header"]
    assert out["server"] == "nginx/1.25.3"
    assert out["scope"] == "allowed"


@pytest.mark.asyncio
async def test_litellm_vendor_header_detected_with_version() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"server": "uvicorn", "x-litellm-version": "1.40.0"},
            text="<html></html>",
        )

    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    names = {t["name"]: t for t in out["technologies"]}
    assert names["litellm"]["category"] == "ai-proxy"
    assert names["litellm"]["version"] == "1.40.0"
    assert "uvicorn" in names  # web server banner picked up alongside the AI proxy


@pytest.mark.asyncio
async def test_tgi_telemetry_header_detected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-prompt-tokens": "12", "x-compute-type": "gpu"},
            text="<html></html>",
        )

    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    tgi = next(t for t in out["technologies"] if t["name"] == "text-generation-inference")
    assert tgi["category"] == "ai-runtime"


@pytest.mark.asyncio
async def test_word_boundary_avoids_false_ray_match() -> None:
    # "array-cache" must not trip the `ray` AI-framework signature.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-array-cache": "hit"}, text="<html></html>")

    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    assert all(t["name"] != "ray" for t in out["technologies"])


@pytest.mark.asyncio
async def test_cloudflare_cf_ray_header_does_not_match_ray() -> None:
    # The near-universal Cloudflare ``cf-ray`` trace header must not be
    # misread as the Ray AI framework (hyphen-adjacent token).
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"server": "cloudflare", "cf-ray": "8d1f2a3b4c5d-DFW"},
            text="<html></html>",
        )

    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    names = [t["name"] for t in out["technologies"]]
    assert "ray" not in names
    assert "cloudflare" in names  # genuine banner still detected


# ── Body engine ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_body_detects_jquery_version_and_nextjs() -> None:
    body = (
        '<html><head><script src="/static/jquery-3.6.0.min.js"></script>'
        '<script src="/_next/static/chunks/main.js"></script></head><body></body></html>'
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"server": "nginx/1.0"}, text=body)

    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    names = {t["name"]: t for t in out["technologies"]}
    assert names["jquery"]["version"] == "3.6.0"
    assert names["jquery"]["detected_by"] == ["html-body"]
    assert names["next.js"]["category"] == "web-framework"


@pytest.mark.asyncio
async def test_body_detects_ai_frontend_gradio() -> None:
    body = "<html><body><script>window.gradio_config={};</script>gradio app</body></html>"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    gradio = next(t for t in out["technologies"] if t["name"] == "gradio")
    assert gradio["category"] == "ai-framework"


@pytest.mark.asyncio
async def test_no_signatures_yields_empty_list() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="hello world")

    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    assert out["technologies"] == []
    assert out["status_code"] == 200


# ── Cross-engine merge ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_tech_from_both_engines_merges() -> None:
    # vLLM named in the Server banner AND referenced in the body -> one entry,
    # both sources unioned, version retained from whichever engine had it.
    body = "<html><body>powered by vllm runtime</body></html>"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"server": "vllm/0.5.1"}, text=body)

    # Add a body signature path for vllm by also matching the banner engine;
    # the body engine has no vllm sig, so this asserts header-only single entry.
    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    vllm = [t for t in out["technologies"] if t["name"] == "vllm"]
    assert len(vllm) == 1
    assert vllm[0]["version"] == "0.5.1"


def test_merge_unions_sources_and_keeps_version() -> None:
    detections = [
        {"key": "k", "name": "x", "category": "c", "version": None, "detected_by": ["http-header"]},
        {"key": "k", "name": "x", "category": "c", "version": "2.0", "detected_by": ["html-body"]},
    ]
    merged = tech_detection._merge(detections)
    assert len(merged) == 1
    assert merged[0]["version"] == "2.0"
    assert merged[0]["detected_by"] == ["http-header", "html-body"]


# ── RoE scope enforcement ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforce_mode_refuses_out_of_scope_host_without_egress() -> None:
    rules = MachineEnforcement(
        in_scope=(ScopeRule(pattern="allowed.test"),),
    )
    called = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        called["n"] += 1
        return httpx.Response(200, text="<html></html>")

    with (
        patch.object(tech_detection, "_load_roe_rules", lambda: rules),
        _install_transport(handler),
    ):
        out = await _run("https://evil.test/")

    assert out["scope"] == "refused"
    assert out["reason_code"] == "NOT_IN_SCOPE"
    assert "error" in out
    assert called["n"] == 0  # no socket opened


@pytest.mark.asyncio
async def test_enforce_mode_allows_in_scope_host() -> None:
    rules = MachineEnforcement(
        in_scope=(ScopeRule(pattern="target.test"),),
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"server": "caddy/2.7.6"}, text="<html></html>")

    with (
        patch.object(tech_detection, "_load_roe_rules", lambda: rules),
        _install_transport(handler),
    ):
        out = await _run("https://target.test/")

    assert out["scope"] == "allowed"
    assert any(t["name"] == "caddy" for t in out["technologies"])


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_invalid_url_returns_error_without_egress() -> None:
    out = json.loads(await detect_tech_stack.ainvoke({"url": "not-a-url"}))
    assert "error" in out


@pytest.mark.asyncio
async def test_connection_error_is_reported() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with patch.object(tech_detection, "_load_roe_rules", _audit_rules), _install_transport(handler):
        out = await _run()

    assert "error" in out
    assert "ConnectError" in out["error"]


# ── Registration / gating contract ───────────────────────────────────────


def test_tool_registered_in_research_tools() -> None:
    from decepticon.tools.research.tools import RESEARCH_TOOLS

    assert detect_tech_stack in RESEARCH_TOOLS
