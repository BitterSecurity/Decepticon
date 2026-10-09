"""AI infrastructure attack-surface discovery engine.

Discovers AI/ML services, endpoints, and leaked keys across a target's
network surface. Three agent-facing tools are exposed:

- :func:`ai_surface_scan` — full port + endpoint + key scan of a host.
- :func:`ai_endpoint_classify` — classify a list of URLs as AI endpoints.
- :func:`ai_key_scan` — scan arbitrary content for leaked AI API keys.

Port probes are TCP connect-only; HTTP fingerprinting uses a single GET per
open port. All egress is gated on the engagement's Rules of Engagement via
the same ``plan/roe.json:machine_enforcement`` mechanism used by
:mod:`decepticon.tools.research.tech_detection`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import tool

from decepticon_core.types.roe import (
    EnforcementMode,
    MachineEnforcement,
    evaluate_target,
)
from decepticon_core.utils.logging import get_logger

log = get_logger("research.ai_surface")

# ── Constants ───────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SECONDS = 8.0
CONNECT_TIMEOUT_SECONDS = 3.0
MAX_BODY_SCAN_CHARS = 200_000

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _probe_verify() -> bool | str:
    """TLS verification for active probes.

    Controlled via ``DECEPTICON_PROBE_VERIFY_TLS``. Defaults to
    ``true``. Set to ``false`` when scanning targets with self-signed
    or expired certificates.
    """
    val = os.environ.get("DECEPTICON_PROBE_VERIFY_TLS", "true").strip().lower()
    if val in ("0", "false", "no", ""):
        return False  # noqa: S501 — operator explicitly disabled via env var
    if val in ("1", "true", "yes"):
        return True
    return val


def _json_out(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


# ── AI Port Catalog ─────────────────────────────────────────────────────

AI_PORT_CATALOG: dict[int, dict[str, Any]] = {
    11434: {"service": "Ollama", "proto": "http", "category": "inference"},
    6333: {"service": "Qdrant", "proto": "http", "category": "vector_db"},
    19530: {"service": "Milvus", "proto": "grpc", "category": "vector_db"},
    8188: {"service": "ComfyUI", "proto": "http", "category": "ai_frontend"},
    3000: {"service": "Open WebUI", "proto": "http", "category": "ai_frontend"},
    8080: {"service": "vLLM", "proto": "http", "category": "inference"},
    7860: {"service": "Gradio", "proto": "http", "category": "ai_frontend"},
    8501: {"service": "Streamlit", "proto": "http", "category": "ai_frontend"},
    5000: {"service": "MLflow", "proto": "http", "category": "ml_ops"},
    9090: {"service": "Prometheus", "proto": "http", "category": "monitoring"},
    6379: {"service": "Redis", "proto": "tcp", "category": "vector_cache"},
    8265: {"service": "Ray Dashboard", "proto": "http", "category": "ml_ops"},
    8000: {"service": "FastAPI/LiteLLM/TGI", "proto": "http", "category": "inference"},
    50051: {"service": "Triton Inference Server", "proto": "grpc", "category": "inference"},
    8888: {"service": "Jupyter Notebook", "proto": "http", "category": "ml_ops"},
    6006: {"service": "TensorBoard", "proto": "http", "category": "monitoring"},
}

# ── AI Endpoint Classifier ──────────────────────────────────────────────

AI_ENDPOINT_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"/v1/chat/completions"),
        "openai_compatible_chat",
        "OpenAI-compatible chat completions endpoint",
    ),
    (
        re.compile(r"/v1/completions"),
        "openai_compatible_completion",
        "OpenAI-compatible text completions endpoint",
    ),
    (
        re.compile(r"/v1/embeddings"),
        "openai_compatible_embeddings",
        "OpenAI-compatible embeddings endpoint",
    ),
    (re.compile(r"/v1/models"), "openai_compatible_models", "OpenAI-compatible model listing"),
    (re.compile(r"/api/generate"), "ollama_generate", "Ollama text generation endpoint"),
    (re.compile(r"/api/chat"), "ollama_chat", "Ollama chat endpoint"),
    (re.compile(r"/api/tags"), "ollama_tags", "Ollama model tags listing"),
    (re.compile(r"/api/embeddings"), "ollama_embeddings", "Ollama embeddings endpoint"),
    (re.compile(r"/api/predict"), "ml_prediction", "Generic ML prediction API"),
    (re.compile(r"/invocations"), "sagemaker_inference", "AWS SageMaker inference endpoint"),
    (re.compile(r"/(mcp|sse)(?:/|$)"), "mcp_server", "Model Context Protocol / SSE server"),
    (re.compile(r"/v2/models"), "triton_inference", "NVIDIA Triton Inference Server"),
    (re.compile(r"/fapi/"), "gradio_api", "Gradio FastAPI backend"),
]

# ── AI Frontend Key Patterns ────────────────────────────────────────────

AI_KEY_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"), "openai_project_key", "OpenAI"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "anthropic_api_key", "Anthropic"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "aws_ai_key", "AWS (AI)"),
    (re.compile(r"co-[A-Za-z0-9]{40,}"), "cohere_api_key", "Cohere"),
    (re.compile(r"hf_[A-Za-z0-9]{34}"), "huggingface_token", "HuggingFace"),
    (re.compile(r"gsk_[A-Za-z0-9]{52}"), "groq_api_key", "Groq"),
    (re.compile(r"xai-[A-Za-z0-9]{20,}"), "xai_grok_key", "xAI/Grok"),
    (re.compile(r"pplx-[A-Za-z0-9]{48}"), "perplexity_key", "Perplexity"),
    (re.compile(r"r8_[A-Za-z0-9]{40}"), "replicate_token", "Replicate"),
]

# ── RoE gating ──────────────────────────────────────────────────────────


def _load_roe_rules() -> MachineEnforcement:
    """Load engagement RoE machine-enforcement block."""
    workspace = os.getenv("DECEPTICON_WORKSPACE_PATH", str(Path.cwd()))
    roe_path = Path(workspace) / "plan" / "roe.json"
    if not roe_path.exists():
        return MachineEnforcement.from_dict({})
    try:
        with open(roe_path) as fh:
            data = json.load(fh)
        block = data.get("machine_enforcement", {})
    except (json.JSONDecodeError, OSError):
        return MachineEnforcement.from_dict({})
    return MachineEnforcement.from_dict(block)


def _check_roe(host: str) -> dict[str, Any] | None:
    """Return an error dict if the host is refused, else None."""
    rules = _load_roe_rules()
    decision = evaluate_target(host, rules)
    if not decision.allow and rules.mode == EnforcementMode.ENFORCE:
        log.warning("ai_surface: RoE refused %s (%s)", host, decision.reason_code)
        return {
            "scope": "refused",
            "reason_code": decision.reason_code,
            "reason_detail": decision.reason_detail,
            "error": f"RoE refused egress to {host!r}: {decision.reason_detail}",
        }
    return None


# ── Helpers ─────────────────────────────────────────────────────────────


async def _tcp_connect(host: str, port: int, timeout: float = CONNECT_TIMEOUT_SECONDS) -> bool:
    """Attempt a TCP connect to ``host:port``. Returns True if open."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await asyncio.wait_for(
            loop.sock_connect(sock, (host, port)),
            timeout=timeout,
        )
        return True
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        sock.close()


async def _http_probe(
    client: httpx.AsyncClient, url: str
) -> tuple[int, dict[str, str], str] | None:
    """GET ``url`` and return (status, headers, body). None on failure."""
    try:
        resp = await client.get(url)
        headers = {k.lower(): v for k, v in resp.headers.items()}
        body = resp.text[:MAX_BODY_SCAN_CHARS]
        return resp.status_code, headers, body
    except httpx.HTTPError:
        return None


def _classify_url(url: str) -> list[dict[str, Any]]:
    """Match a URL path against AI_ENDPOINT_PATTERNS."""
    path = urlsplit(url).path or "/"
    matches = []
    for pattern, etype, desc in AI_ENDPOINT_PATTERNS:
        if pattern.search(path):
            matches.append(
                {
                    "url": url,
                    "type": etype,
                    "description": desc,
                }
            )
    return matches


def _scan_keys(content: str) -> list[dict[str, Any]]:
    """Scan content for AI API keys. Returns finding dicts."""
    findings: list[dict[str, Any]] = []
    lines = content.splitlines()
    for line_no, line in enumerate(lines, 1):
        for pattern, pattern_name, provider in AI_KEY_PATTERNS:
            for match in pattern.finditer(line):
                secret = match.group(0)
                # For AWS keys near AI services, require context
                if pattern_name == "aws_ai_key":
                    ctx = content[max(0, match.start() - 200) : match.end() + 200].lower()
                    if not any(kw in ctx for kw in ("bedrock", "sagemaker", "ai", "ml")):
                        continue
                # Redact: show first 8 and last 4 chars
                if len(secret) > 16:
                    redacted = secret[:8] + "…" + secret[-4:]
                else:
                    redacted = secret[:4] + "…" + secret[-2:]
                findings.append(
                    {
                        "key_redacted": redacted,
                        "pattern": pattern_name,
                        "line": line_no,
                        "provider": provider,
                    }
                )
    return findings


_AI_HEADER_MARKERS: list[tuple[str, str]] = [
    ("x-ollama", "Ollama"),
    ("x-litellm", "LiteLLM"),
    ("x-prompt-tokens", "LLM Proxy"),
    ("x-completion-tokens", "LLM Proxy"),
    ("x-model-", "LLM Proxy"),
    ("mlflow", "MLflow"),
    ("triton", "Triton"),
    ("text-generation", "TGI"),
    ("x-inference", "Inference Server"),
    ("gradio", "Gradio"),
    ("streamlit", "Streamlit"),
]


def _detect_ai_headers(headers: dict[str, str]) -> list[dict[str, str]]:
    """Check response headers for AI service markers."""
    hits: list[dict[str, str]] = []
    blob = "\n".join(f"{k}: {v}" for k, v in headers.items()).lower()
    for marker, service in _AI_HEADER_MARKERS:
        if marker in blob:
            hits.append({"marker": marker, "service": service, "source": "http-header"})
    return hits


def _compute_risk_score(
    open_ports: list[dict[str, Any]],
    endpoints: list[dict[str, Any]],
    keys: list[dict[str, Any]],
) -> float:
    """Compute a 0-10 risk score based on discovered AI surface."""
    score = 0.0
    # Open AI ports contribute base risk
    score += min(len(open_ports) * 1.5, 5.0)
    # Confirmed AI endpoints amplify
    confirmed = [e for e in endpoints if e.get("confirmed")]
    score += min(len(confirmed) * 1.0, 3.0)
    # Leaked keys are critical
    score += min(len(keys) * 2.0, 4.0)
    return min(round(score, 1), 10.0)


# ── Tools ───────────────────────────────────────────────────────────────


@tool
async def ai_surface_scan(target: str) -> str:
    """Full AI infrastructure surface scan of a target host.

    WHEN TO USE: During recon to discover exposed AI/ML services, inference
    endpoints, model-serving infrastructure, and leaked AI API keys on a
    target. Performs TCP connect probes on known AI ports, HTTP fingerprinting
    on open ports, AI endpoint pattern matching, and JS/HTML key scanning.

    Egress is gated on the engagement RoE — an out-of-scope host is refused
    before any probe when the RoE is in ``enforce`` mode.

    Args:
        target: IP address or hostname to scan (e.g. ``10.0.0.1`` or
                ``target.example.com``).

    Returns:
        JSON string with ``open_ai_ports``, ``detected_services``,
        ``ai_endpoints``, ``leaked_keys``, and ``risk_score``.
    """
    host = target.strip()
    if not host:
        return _json_out({"error": "empty target", "target": target})

    # RoE gate
    roe_err = _check_roe(host)
    if roe_err is not None:
        return _json_out({"target": target, **roe_err})

    # Phase 1: TCP port scan
    port_tasks = {port: _tcp_connect(host, port) for port in AI_PORT_CATALOG}
    results = await asyncio.gather(*port_tasks.values())
    open_ports: list[dict[str, Any]] = []
    for (port, _), is_open in zip(port_tasks.items(), results):
        if is_open:
            info = AI_PORT_CATALOG[port]
            open_ports.append(
                {
                    "port": port,
                    "service": info["service"],
                    "proto": info["proto"],
                    "category": info["category"],
                }
            )

    # Phase 2: HTTP probe open ports for AI endpoints + headers + keys
    detected_services: list[dict[str, Any]] = []
    ai_endpoints: list[dict[str, Any]] = []
    leaked_keys: list[dict[str, Any]] = []

    http_ports = [p for p in open_ports if p["proto"] in ("http", "grpc")]
    if http_ports:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"User-Agent": _USER_AGENT},
            verify=_probe_verify(),
        ) as client:
            for port_info in http_ports:
                port = port_info["port"]
                scheme = "https" if port in {443, 8443} else "http"
                base = f"{scheme}://{host}:{port}"

                # Probe root
                root_result = await _http_probe(client, base + "/")
                if root_result is not None:
                    status, headers, body = root_result
                    port_info["http_status"] = status

                    # Header fingerprinting
                    header_hits = _detect_ai_headers(headers)
                    for hit in header_hits:
                        hit["port"] = port
                        detected_services.append(hit)

                    # Key scanning on body
                    body_keys = _scan_keys(body)
                    for k in body_keys:
                        k["port"] = port
                        k["source_url"] = base + "/"
                    leaked_keys.extend(body_keys)

                # Probe known AI endpoint paths
                probe_paths = [
                    "/v1/models",
                    "/v1/chat/completions",
                    "/api/tags",
                    "/api/generate",
                    "/v2/models",
                    "/invocations",
                    "/fapi/",
                    "/mcp",
                ]
                for ep_path in probe_paths:
                    ep_url = base + ep_path
                    ep_result = await _http_probe(client, ep_url)
                    if ep_result is not None:
                        ep_status, ep_headers, _ = ep_result
                        classifications = _classify_url(ep_url)
                        for cls in classifications:
                            cls["confirmed"] = ep_status < 404
                            cls["status_code"] = ep_status
                            cls["port"] = port
                            ai_endpoints.append(cls)

    risk_score = _compute_risk_score(open_ports, ai_endpoints, leaked_keys)

    return _json_out(
        {
            "target": target,
            "scope": "allowed",
            "open_ai_ports": open_ports,
            "detected_services": detected_services,
            "ai_endpoints": ai_endpoints,
            "leaked_keys": leaked_keys,
            "risk_score": risk_score,
        }
    )


@tool
async def ai_endpoint_classify(urls_json: str) -> str:
    """Classify a list of URLs as AI endpoints and confirm with HTTP probes.

    WHEN TO USE: When you have a list of discovered URLs and want to identify
    which ones serve AI inference, model-serving, or ML pipeline endpoints.

    Matches each URL against known AI endpoint patterns and optionally probes
    to confirm. Egress is gated on the engagement RoE.

    Args:
        urls_json: JSON array of URL strings to classify, e.g.
                   ``'["http://10.0.0.1:8080/v1/models"]'``.

    Returns:
        JSON string with ``endpoints`` list of
        ``{url, type, confirmed, service, version}``.
    """
    try:
        urls = json.loads(urls_json)
    except (json.JSONDecodeError, TypeError):
        return _json_out({"error": f"invalid JSON: {urls_json!r}"})

    if not isinstance(urls, list):
        return _json_out({"error": "expected a JSON array of URL strings"})

    endpoints: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        headers={"User-Agent": _USER_AGENT},
        verify=_probe_verify(),
    ) as client:
        for url in urls:
            if not isinstance(url, str):
                continue

            # RoE gate per host
            url_host = urlsplit(url).hostname or ""
            if url_host:
                roe_err = _check_roe(url_host)
                if roe_err is not None:
                    endpoints.append({"url": url, "type": "roe_refused", **roe_err})
                    continue

            # Pattern classification
            classifications = _classify_url(url)
            if not classifications:
                endpoints.append(
                    {
                        "url": url,
                        "type": "unknown",
                        "confirmed": False,
                        "service": None,
                        "version": None,
                    }
                )
                continue

            # HTTP probe to confirm
            probe = await _http_probe(client, url)
            for cls in classifications:
                confirmed = False
                service = None
                version = None
                if probe is not None:
                    status, headers, _ = probe
                    confirmed = status < 404
                    # Extract service/version from headers
                    server = headers.get("server", "")
                    if server:
                        service = server
                    ai_hits = _detect_ai_headers(headers)
                    if ai_hits:
                        service = ai_hits[0]["service"]
                    # Version from common headers
                    for hdr in ("x-version", "x-api-version", "server"):
                        val = headers.get(hdr, "")
                        if val and re.search(r"\d+\.\d+", val):
                            version = val
                            break

                endpoints.append(
                    {
                        "url": cls["url"],
                        "type": cls["type"],
                        "confirmed": confirmed,
                        "service": service,
                        "version": version,
                    }
                )

    return _json_out({"endpoints": endpoints})


@tool
async def ai_key_scan(content: str) -> str:
    """Scan arbitrary content for leaked AI API keys.

    WHEN TO USE: When you have HTML, JavaScript, configuration files, or
    other text content and want to find exposed AI platform credentials
    (OpenAI, Anthropic, HuggingFace, Groq, Cohere, xAI, Perplexity,
    Replicate, AWS Bedrock/SageMaker keys).

    Args:
        content: Text content to scan (HTML, JS, config, etc.).

    Returns:
        JSON string with ``count`` and ``keys`` list of
        ``{key_redacted, pattern, line, provider}``.
    """
    if not content or not content.strip():
        return _json_out({"count": 0, "keys": []})

    findings = _scan_keys(content)
    return _json_out({"count": len(findings), "keys": findings})


# ── Public tool list ────────────────────────────────────────────────────

AI_SURFACE_TOOLS = [ai_surface_scan, ai_endpoint_classify, ai_key_scan]
