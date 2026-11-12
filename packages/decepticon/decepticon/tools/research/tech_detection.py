"""Dual-engine technology-stack fingerprinting over a single live URL.

The :func:`detect_tech_stack` tool grabs one HTTP response (following
redirects) and runs two independent signature engines against it:

- **Header engine** — banner grabbing + response-header extraction. Every
  ``"<name>: <value>"`` line is matched against :data:`_HEADER_SIGNATURES`,
  which names web servers (nginx, Apache, gunicorn, uvicorn, ...), AI
  inference runtimes / proxies (vLLM, Ollama, LiteLLM, MLflow,
  text-generation-inference, Ray), and language runtimes (PHP, Express,
  ASP.NET). Versions are lifted from the banner where the product emits one.
- **Body engine** — HTML signature matching. The response body is matched
  against :data:`_BODY_SIGNATURES`, covering common JS frameworks (React,
  Vue, Angular, Next.js, Nuxt, Svelte, jQuery, Bootstrap) and AI front-end
  libraries (Gradio, Streamlit, Open WebUI, ComfyUI, Chainlit,
  transformers.js, in-page OpenAI/Anthropic SDK usage).

Egress is gated on the engagement's Rules of Engagement: the target host is
evaluated against ``plan/roe.json:machine_enforcement`` and, in ``enforce``
mode, an out-of-scope / forbidden host is refused *before* any socket is
opened, so the tool is safe when invoked directly (CLI one-shots, tests) as
well as inside an OpenShell-confined sandbox.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import tool

from decepticon_core.types.kg import TechnologyCategory, technology_key
from decepticon_core.types.roe import (
    MachineEnforcement,
    evaluate_target,
)
from decepticon_core.utils.logging import get_logger

log = get_logger("research.tech_detection")

# How a technology was observed — header banner vs. HTML body.
DETECTED_BY_HEADER = "http-header"
DETECTED_BY_BODY = "html-body"

# Wall-clock cap for the single probe. Generous enough for a slow TLS
# handshake + redirect chain, tight enough that a black-hole host fails fast.
DEFAULT_TIMEOUT_SECONDS = 15.0

# Body is regex-scanned; cap the slice so a multi-megabyte SPA bundle can't
# turn signature matching into a latency sink. Front-end markers all live in
# the document head / early body, well inside this window.
MAX_BODY_SCAN_CHARS = 200_000

# Browser-ish UA so banner-cloaking servers return their real fingerprint
# instead of a bot-challenge page.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class _Signature:
    """One technology fingerprint.

    ``marker`` proves the product is present; the optional ``version`` regex
    lifts a version string from the same text (its first capturing group).
    Both are pre-compiled, case-insensitive.
    """

    name: str
    category: TechnologyCategory
    marker: re.Pattern[str]
    version: re.Pattern[str] | None = None


def _sig(
    name: str,
    category: TechnologyCategory,
    marker: str,
    version: str | None = None,
) -> _Signature:
    return _Signature(
        name=name,
        category=category,
        marker=re.compile(marker, re.IGNORECASE),
        version=re.compile(version, re.IGNORECASE) if version else None,
    )


# Token-boundary product name — keeps ``ray`` from firing inside ``array`` and
# ``ollama`` from firing inside ``follama``. Mirrors the banner-token rule in
# ``middleware/kg_internal/ai_surface.py``.
def _tok(word: str) -> str:
    return rf"(?<![a-z0-9]){word}(?![a-z0-9])"


# Header engine — each response header is flattened to ``"<name>: <value>"``
# (lowercased) and matched against these. Covers banner grabbing (Server,
# X-Powered-By, Via) and distinctive vendor header names (x-litellm-*,
# x-prompt-tokens, ...). Version regexes run against the same flattened line.
_HEADER_SIGNATURES: tuple[_Signature, ...] = (
    # ── AI inference runtimes / proxies / frameworks ──
    _sig("vllm", TechnologyCategory.AI_RUNTIME, _tok("vllm"), r"vllm[/ ]v?([\d][\w.\-]*)"),
    _sig("ollama", TechnologyCategory.AI_RUNTIME, _tok("ollama"), r"ollama[/ ]v?([\d][\w.\-]*)"),
    _sig(
        "text-generation-inference",
        TechnologyCategory.AI_RUNTIME,
        r"text-generation-inference|x-prompt-tokens|x-compute-type",
        r"text-generation-inference[/ ]v?([\d][\w.\-]*)",
    ),
    _sig(
        "litellm",
        TechnologyCategory.AI_PROXY,
        r"litellm",
        r"x-litellm-version:\s*v?([\d][\w.\-]*)",
    ),
    _sig("mlflow", TechnologyCategory.AI_FRAMEWORK, _tok("mlflow"), r"mlflow[/ ]v?([\d][\w.\-]*)"),
    # ``-`` is part of ray's boundary so the near-universal Cloudflare ``cf-ray``
    # header (and ``x-ray-id``) can't masquerade as the Ray AI framework.
    _sig(
        "ray",
        TechnologyCategory.AI_FRAMEWORK,
        r"(?<![a-z0-9-])ray(?![a-z0-9-])",
        r"\bray[/ ]v?([\d][\w.\-]*)",
    ),
    # ── Web servers ──
    _sig("nginx", TechnologyCategory.WEB_SERVER, _tok("nginx"), r"nginx/([\d][\w.]*)"),
    _sig("apache", TechnologyCategory.WEB_SERVER, _tok("apache"), r"apache/([\d][\w.]*)"),
    _sig("openresty", TechnologyCategory.WEB_SERVER, _tok("openresty"), r"openresty/([\d][\w.]*)"),
    _sig("caddy", TechnologyCategory.WEB_SERVER, _tok("caddy"), r"caddy/([\d][\w.]*)"),
    _sig("gunicorn", TechnologyCategory.WEB_SERVER, _tok("gunicorn"), r"gunicorn/([\d][\w.]*)"),
    _sig("uvicorn", TechnologyCategory.WEB_SERVER, _tok("uvicorn"), r"uvicorn/([\d][\w.]*)"),
    _sig("werkzeug", TechnologyCategory.WEB_SERVER, _tok("werkzeug"), r"werkzeug/([\d][\w.]*)"),
    _sig(
        "microsoft-iis",
        TechnologyCategory.WEB_SERVER,
        r"microsoft-iis",
        r"microsoft-iis/([\d][\w.]*)",
    ),
    _sig("envoy", TechnologyCategory.WEB_SERVER, _tok("envoy"), r"envoy/([\d][\w.]*)"),
    _sig("cloudflare", TechnologyCategory.WEB_SERVER, _tok("cloudflare")),
    # ── Web frameworks / language runtimes (X-Powered-By, X-AspNet-Version) ──
    _sig("express", TechnologyCategory.WEB_FRAMEWORK, _tok("express")),
    _sig(
        "asp.net",
        TechnologyCategory.WEB_FRAMEWORK,
        r"asp\.net",
        r"x-aspnet-version:\s*([\d][\w.]*)",
    ),
    _sig("php", TechnologyCategory.LANGUAGE_RUNTIME, _tok("php"), r"php/([\d][\w.]*)"),
)


# Body engine — matched against the HTML response body.
_BODY_SIGNATURES: tuple[_Signature, ...] = (
    # ── JS frameworks ──
    _sig(
        "next.js",
        TechnologyCategory.WEB_FRAMEWORK,
        r"/_next/static/|__NEXT_DATA__",
        r'"buildId":"[^"]+","[^}]*?"version":"([\d][\w.\-]*)"',
    ),
    _sig("nuxt.js", TechnologyCategory.WEB_FRAMEWORK, r"__NUXT__|/_nuxt/"),
    _sig(
        "react",
        TechnologyCategory.WEB_FRAMEWORK,
        r"data-reactroot|react-dom|\breact@",
        r"react@([\d][\w.\-]*)",
    ),
    _sig(
        "vue.js",
        TechnologyCategory.WEB_FRAMEWORK,
        r"data-v-app|__vue__|\bvue@",
        r"vue(?:@|\.js v)([\d][\w.\-]*)",
    ),
    _sig(
        "angular",
        TechnologyCategory.WEB_FRAMEWORK,
        r"ng-version|\bng-app\b",
        r'ng-version="([\d][\w.\-]*)"',
    ),
    _sig("svelte", TechnologyCategory.WEB_FRAMEWORK, r"svelte-[0-9a-z]{6}|__sveltekit"),
    _sig(
        "jquery", TechnologyCategory.WEB_FRAMEWORK, r"jquery", r"jquery[.-]?v?([\d]+\.[\d]+\.[\d]+)"
    ),
    _sig(
        "bootstrap",
        TechnologyCategory.WEB_FRAMEWORK,
        r"bootstrap",
        r"bootstrap[.-/]v?([\d]+\.[\d]+\.[\d]+)",
    ),
    # ── AI front-end libraries ──
    _sig("gradio", TechnologyCategory.AI_FRAMEWORK, r"gradio", r"gradio[/-]v?([\d][\w.\-]*)"),
    _sig(
        "streamlit",
        TechnologyCategory.AI_FRAMEWORK,
        r"streamlit",
        r'"streamlitVersion":"([\d][\w.\-]*)"',
    ),
    _sig("open-webui", TechnologyCategory.AI_FRAMEWORK, r"open webui|open-webui"),
    _sig("comfyui", TechnologyCategory.AI_FRAMEWORK, r"comfyui"),
    _sig("chainlit", TechnologyCategory.AI_FRAMEWORK, r"chainlit"),
    _sig(
        "transformers.js",
        TechnologyCategory.AI_SDK_CLIENT,
        r"@xenova/transformers|transformers\.js",
    ),
    _sig(
        "openai-sdk",
        TechnologyCategory.AI_SDK_CLIENT,
        r"api\.openai\.com|cdn\.jsdelivr\.net/npm/openai",
    ),
    _sig(
        "anthropic-sdk", TechnologyCategory.AI_SDK_CLIENT, r"api\.anthropic\.com|@anthropic-ai/sdk"
    ),
)


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _load_roe_rules() -> MachineEnforcement:
    """Load the engagement's machine-enforcement RoE block.

    Resolves the workspace from ``DECEPTICON_WORKSPACE_PATH`` (the same env
    the middleware factory reads). Absent / unreadable RoE degrades to an
    empty (audit-only) ruleset — matching the middleware's fail-open posture
    so a missing file never silently blocks a legitimate probe.
    """
    workspace = os.environ.get("DECEPTICON_WORKSPACE_PATH")
    if not workspace:
        return MachineEnforcement()
    roe_path = Path(workspace) / "plan" / "roe.json"
    if not roe_path.exists():
        return MachineEnforcement()
    try:
        data = json.loads(roe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "tech_detection: failed to read %s: %s; defaulting to audit-only", roe_path, exc
        )
        return MachineEnforcement()
    block = data.get("machine_enforcement") if isinstance(data, dict) else None
    return MachineEnforcement.from_dict(block)


def _detect(
    text: str, signatures: tuple[_Signature, ...], detected_by: str
) -> list[dict[str, Any]]:
    """Run one engine's signatures over ``text`` and return detection dicts."""
    hits: list[dict[str, Any]] = []
    for sig in signatures:
        match = sig.marker.search(text)
        if match is None:
            continue
        version: str | None = None
        if sig.version is not None:
            vmatch = sig.version.search(text)
            if vmatch is not None and vmatch.groups():
                version = vmatch.group(1)
        hits.append(
            {
                "key": technology_key(sig.category, sig.name),
                "name": sig.name,
                "category": sig.category.value,
                "version": version,
                "detected_by": [detected_by],
            }
        )
    return hits


def _merge(detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse detections of the same technology across both engines.

    Same ``key`` -> one entry whose ``detected_by`` unions the sources and
    whose ``version`` keeps the first concrete value seen.
    """
    merged: dict[str, dict[str, Any]] = {}
    for hit in detections:
        existing = merged.get(hit["key"])
        if existing is None:
            merged[hit["key"]] = dict(hit)
            continue
        for source in hit["detected_by"]:
            if source not in existing["detected_by"]:
                existing["detected_by"].append(source)
        if existing["version"] is None and hit["version"] is not None:
            existing["version"] = hit["version"]
    return list(merged.values())


@tool
async def detect_tech_stack(url: str) -> str:
    """Fingerprint a live URL's technology stack via headers and HTML body.

    WHEN TO USE: During recon, to identify the web server, web framework,
    language runtime, and — especially — any exposed AI inference runtime
    (vLLM, Ollama, LiteLLM, MLflow, text-generation-inference) or AI
    front-end (Gradio, Streamlit, Open WebUI, ComfyUI) on a target.

    Performs ONE GET (following redirects) and runs two signature engines:
    a header/banner engine and an HTML-body engine. Egress is gated on the
    engagement RoE — an out-of-scope or forbidden host is refused before any
    request is sent when the RoE is in ``enforce`` mode.

    Args:
        url: Absolute target URL (e.g. ``https://target.example/``).

    Returns:
        JSON string with the final URL, status code, response headers, and a
        ``technologies`` list of ``{name, category, version, detected_by}``.
    """
    host = urlsplit(url).hostname or ""
    if not host:
        return _json({"error": f"invalid url: {url!r}", "url": url})

    rules = _load_roe_rules()
    decision = evaluate_target(host, rules)
    if not decision.allow:
        log.warning("tech_detection: RoE refused %s (%s)", host, decision.reason_code)
        return _json(
            {
                "url": url,
                "scope": "refused",
                "reason_code": decision.reason_code,
                "reason_detail": decision.reason_detail,
                "error": f"RoE refused egress to {host!r}: {decision.reason_detail}",
            }
        )

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        return _json({"url": url, "error": f"{type(exc).__name__}: {exc}"})

    headers = {k.lower(): v for k, v in resp.headers.items()}
    header_blob = "\n".join(f"{k}: {v}" for k, v in headers.items())
    body = resp.text[:MAX_BODY_SCAN_CHARS]

    detections = _detect(header_blob, _HEADER_SIGNATURES, DETECTED_BY_HEADER) + _detect(
        body, _BODY_SIGNATURES, DETECTED_BY_BODY
    )
    technologies = _merge(detections)

    return _json(
        {
            "url": str(resp.url),
            "scope": "allowed",
            "status_code": resp.status_code,
            "server": headers.get("server"),
            "headers": headers,
            "technologies": technologies,
        }
    )
