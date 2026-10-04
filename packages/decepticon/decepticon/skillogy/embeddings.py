"""Embedding helper for skillogy hybrid retrieval (ADR-0011).

Used by BOTH boot-ingest (embed each ``:Skill`` once into Neo4j) and query-time
``find_skill`` (embed the query for the vector search). Embeddings go through
the **litellm proxy** the agents already use — the skillogy container must be
given ``DECEPTICON_LLM__PROXY_URL`` + ``DECEPTICON_LLM__PROXY_API_KEY`` (see
ADR-0011 §"Skillogy↔litellm coupling").

Degradation contract: this module **never raises** to its callers. When the
proxy is unconfigured or a request fails, ``embed_text`` returns ``None`` and
``find_skill`` falls back to full-text lexical search — semantic search is an
opt-in upgrade, not a hard dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# OSS default mirrors the KG layer's default (see kg_internal migration V002:
# "OpenAI text-embedding-3-small (1536) as the OSS default"). Override with
# DECEPTICON_SKILLOGY_EMBED_MODEL; set the matching dim if the model differs.
DEFAULT_EMBED_MODEL = "openai/text-embedding-3-small"
_KNOWN_DIMS: dict[str, int] = {
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "voyage/voyage-3": 1024,
    "voyage/voyage-3-lite": 512,
    "voyage-3": 1024,
    "voyage-3-lite": 512,
    "gemini/gemini-embedding-001": 3072,
    "gemini-embedding-001": 3072,
}
_DEFAULT_DIM = 1536

_REQUEST_TIMEOUT = 30.0
# Embedding providers rate-limit aggressively (Voyage's no-payment free tier
# is 3 RPM / 10K TPM), and boot-ingest fires several chunked requests
# back-to-back. Providers also mask the 429 as a 500 through litellm, so the
# retryable set includes 5xx and backoff must be patient enough to ride out a
# ~60s rate window rather than just a brief blip. The exponential is capped so
# a single retry can't stall boot indefinitely; once attempts are exhausted
# the chunk degrades to substring and the next boot re-embeds it (incremental
# by input-sha). Worst-case added boot latency ≈ sum of the backoffs (~60s).
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
# Ingest (boot, batched) can afford to be patient — it runs off the request
# path and a re-embed is cheaper than leaving the corpus unembedded.
_MAX_ATTEMPTS = 6
_BACKOFF_BASE = 1.0
_BACKOFF_CAP = 30.0
# Query time (a live find_skill) must NOT make the agent wait. litellm already
# retries the upstream internally, so a single client attempt with a short
# timeout is enough: if the embedding can't come back in a few seconds we fail
# fast to None → find_skill returns lexical results immediately instead of
# blowing the REST client timeout and leaving the agent empty-handed.
_QUERY_MAX_ATTEMPTS = 1
_QUERY_REQUEST_TIMEOUT = 5.0


def embed_model() -> str:
    return os.environ.get("DECEPTICON_SKILLOGY_EMBED_MODEL", DEFAULT_EMBED_MODEL).strip()


def embed_dim() -> int:
    """Embedding dimension for the configured model (drives the vector index DDL).

    An explicit ``DECEPTICON_SKILLOGY_EMBED_DIM`` wins (for models not in the
    table); otherwise look the model up, else fall back to 1536.
    """
    raw = os.environ.get("DECEPTICON_SKILLOGY_EMBED_DIM", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return _KNOWN_DIMS.get(embed_model(), _DEFAULT_DIM)


def _proxy() -> tuple[str, str] | None:
    """``(base_url, api_key)`` for the litellm proxy, or ``None`` if unconfigured."""
    base = os.environ.get("DECEPTICON_LLM__PROXY_URL", "").strip()
    key = os.environ.get("DECEPTICON_LLM__PROXY_API_KEY", "").strip()
    if not base or not key:
        return None
    return base.rstrip("/"), key


def available() -> bool:
    """True when an embedding model is reachable (proxy configured)."""
    return _proxy() is not None


def _cache_dir() -> Path:
    raw = os.environ.get("DECEPTICON_SKILLOGY_EMBED_CACHE", "").strip()
    path = Path(raw) if raw else Path(tempfile.gettempdir()) / "decepticon-skillogy-embed"
    return path


def _cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def _cache_get(model: str, text: str) -> list[float] | None:
    path = _cache_dir() / f"{_cache_key(model, text)}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(model: str, text: str, vector: list[float]) -> None:
    cache = _cache_dir()
    try:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{_cache_key(model, text)}.json").write_text(json.dumps(vector), encoding="utf-8")
    except OSError:  # cache is best-effort
        pass


def _request_embeddings(
    base_url: str,
    key: str,
    model: str,
    inputs: list[str],
    *,
    max_attempts: int = _MAX_ATTEMPTS,
    timeout: float = _REQUEST_TIMEOUT,
) -> list[list[float]]:
    """POST to the proxy's OpenAI-compatible ``/v1/embeddings``.

    Retries transient HTTP failures (429 / 5xx) up to ``max_attempts`` with
    exponential backoff, honouring a ``Retry-After`` header when the provider
    sends one. Raises on a non-retryable error or once attempts are exhausted —
    the caller (``embed_batch``) turns that into a ``None`` fallback. ``ingest``
    uses the patient default; query time passes a small ``max_attempts`` so a
    live find_skill never blocks on a slow retry.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        resp = httpx.post(
            f"{base_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "input": inputs},
            timeout=timeout,
        )
        if resp.status_code in _RETRY_STATUS and attempt < max_attempts - 1:
            delay = _retry_after(resp) or min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP)
            log.info(
                "skillogy embedding %d; retry %d/%d in %.1fs",
                resp.status_code,
                attempt + 1,
                max_attempts - 1,
                delay,
            )
            time.sleep(delay)
            continue
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            break
        data = resp.json()
        # OpenAI shape: {"data": [{"embedding": [...], "index": 0}, ...]}
        rows = sorted(data["data"], key=lambda d: d.get("index", 0))
        return [list(r["embedding"]) for r in rows]
    raise last_exc or httpx.HTTPError("embedding request failed after retries")


def _retry_after(resp: httpx.Response) -> float | None:
    """Parse a ``Retry-After`` delay (seconds form) if present and sane."""
    raw = resp.headers.get("Retry-After", "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    # Clamp so a hostile/huge value can't stall boot-ingest.
    return min(seconds, 30.0) if seconds > 0 else None


def embed_batch(
    texts: list[str],
    *,
    max_attempts: int = _MAX_ATTEMPTS,
    timeout: float = _REQUEST_TIMEOUT,
) -> list[list[float] | None]:
    """Embed many texts. Returns one vector per input, or ``None`` per input
    when embeddings are unavailable / the request fails. Never raises.

    Cached entries are served without a network call; only the cache misses
    are sent to the proxy in one batched request. ``max_attempts`` / ``timeout``
    control retry patience and per-request deadline — ingest uses the patient
    defaults; query time overrides both so a live lookup fails fast to lexical.
    """
    if not texts:
        return []
    model = embed_model()
    results: list[list[float] | None] = [None] * len(texts)

    # Serve cache hits first.
    misses: list[int] = []
    for i, text in enumerate(texts):
        cached = _cache_get(model, text)
        if cached is not None:
            results[i] = cached
        else:
            misses.append(i)
    if not misses:
        return results

    proxy = _proxy()
    if proxy is None:
        log.info("skillogy embeddings unavailable (no litellm proxy env); falling back")
        return results  # misses stay None → caller falls back

    base_url, key = proxy
    try:
        vectors = _request_embeddings(
            base_url,
            key,
            model,
            [texts[i] for i in misses],
            max_attempts=max_attempts,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - degrade, never raise to caller
        log.warning("skillogy embedding request failed (%s); falling back", type(exc).__name__)
        return results
    if len(vectors) != len(misses):
        log.warning("skillogy embedding count mismatch (%d != %d)", len(vectors), len(misses))
        return results
    for idx, vec in zip(misses, vectors, strict=True):
        results[idx] = vec
        _cache_put(model, texts[idx], vec)
    return results


def embed_text(text: str) -> list[float] | None:
    """Embed one text — the **query-time** entry point (find_skill).

    Uses a small retry budget so a live lookup fails fast to ``None`` (→ lexical
    fallback) under a sustained rate limit rather than blocking the agent past
    the REST client timeout. ``None`` when unavailable / on failure; never raises.
    """
    return embed_batch([text], max_attempts=_QUERY_MAX_ATTEMPTS, timeout=_QUERY_REQUEST_TIMEOUT)[0]
