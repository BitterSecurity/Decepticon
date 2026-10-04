"""Unit tests for the skillogy embedding helper (ADR-0011).

The helper underpins hybrid retrieval but must degrade silently: with no
litellm proxy configured it returns ``None`` so ``find_skill`` falls back to
full-text lexical search. These tests pin that contract plus the cache and
batch behaviour, with the HTTP layer faked — no network, no real proxy.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from decepticon.skillogy import embeddings


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no proxy and a private, empty disk cache."""
    monkeypatch.delenv("DECEPTICON_LLM__PROXY_URL", raising=False)
    monkeypatch.delenv("DECEPTICON_LLM__PROXY_API_KEY", raising=False)
    monkeypatch.delenv("DECEPTICON_SKILLOGY_EMBED_MODEL", raising=False)
    monkeypatch.delenv("DECEPTICON_SKILLOGY_EMBED_DIM", raising=False)
    monkeypatch.setenv("DECEPTICON_SKILLOGY_EMBED_CACHE", str(tmp_path / "embed-cache"))


def _set_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECEPTICON_LLM__PROXY_URL", "http://litellm:4000/")
    monkeypatch.setenv("DECEPTICON_LLM__PROXY_API_KEY", "sk-test")
    monkeypatch.setenv("DECEPTICON_SKILLOGY_EMBED_DIM", "1")


# --- availability / config -------------------------------------------------


def test_unavailable_without_proxy() -> None:
    assert embeddings.available() is False
    assert embeddings.embed_text("anything") is None


def test_available_with_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy(monkeypatch)
    assert embeddings.available() is True


def test_embed_dim_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    assert embeddings.embed_model() == "openai/text-embedding-3-small"
    assert embeddings.embed_dim() == 1536
    monkeypatch.setenv("DECEPTICON_SKILLOGY_EMBED_MODEL", "text-embedding-3-large")
    assert embeddings.embed_dim() == 3072
    # explicit dim wins for an unknown model
    monkeypatch.setenv("DECEPTICON_SKILLOGY_EMBED_MODEL", "some-private-model")
    monkeypatch.setenv("DECEPTICON_SKILLOGY_EMBED_DIM", "256")
    assert embeddings.embed_dim() == 256
    # unknown model, no explicit dim → fallback
    monkeypatch.delenv("DECEPTICON_SKILLOGY_EMBED_DIM")
    assert embeddings.embed_dim() == 1536


# --- happy path / batching -------------------------------------------------


def _fake_response(
    monkeypatch: pytest.MonkeyPatch, vectors_by_input: dict[str, list[Any]]
) -> list[dict]:
    """Patch the HTTP call to echo deterministic vectors and record calls."""
    calls: list[dict] = []

    def fake_request(
        base_url: str, key: str, model: str, inputs: list[str], **kwargs: Any
    ) -> list[list[Any]]:
        calls.append(
            {"base_url": base_url, "key": key, "model": model, "inputs": list(inputs), **kwargs}
        )
        return [vectors_by_input[text] for text in inputs]

    monkeypatch.setattr(embeddings, "_request_embeddings", fake_request)
    return calls


def test_embed_text_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy(monkeypatch)
    monkeypatch.setenv("DECEPTICON_SKILLOGY_EMBED_DIM", "3")
    calls = _fake_response(monkeypatch, {"hello": [0.1, 0.2, 0.3]})
    assert embeddings.embed_text("hello") == [0.1, 0.2, 0.3]
    assert calls[0]["base_url"] == "http://litellm:4000"  # trailing slash stripped
    assert calls[0]["inputs"] == ["hello"]


def test_embed_batch_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy(monkeypatch)
    _fake_response(monkeypatch, {"a": [1.0], "b": [2.0], "c": [3.0]})
    assert embeddings.embed_batch(["a", "b", "c"]) == [[1.0], [2.0], [3.0]]


def test_embed_batch_empty() -> None:
    assert embeddings.embed_batch([]) == []


# --- cache -----------------------------------------------------------------


def test_cache_avoids_second_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy(monkeypatch)
    calls = _fake_response(monkeypatch, {"x": [9.0]})
    assert embeddings.embed_text("x") == [9.0]
    assert embeddings.embed_text("x") == [9.0]
    assert len(calls) == 1  # second call served from disk cache


def test_cache_only_misses_hit_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy(monkeypatch)
    calls = _fake_response(monkeypatch, {"a": [1.0], "b": [2.0]})
    embeddings.embed_text("a")  # warm cache for "a"
    calls.clear()
    assert embeddings.embed_batch(["a", "b"]) == [[1.0], [2.0]]
    assert calls[0]["inputs"] == ["b"]  # only the miss is requested


@pytest.mark.parametrize(
    "cached",
    [[0.0], [float("nan")], [float("inf")], [1.0, 2.0], [True], ["1.0"], {"value": [1.0]}],
)
def test_invalid_cache_is_refetched(monkeypatch: pytest.MonkeyPatch, cached: object) -> None:
    _set_proxy(monkeypatch)
    cache = embeddings._cache_dir()
    cache.mkdir(parents=True)
    path = cache / f"{embeddings._cache_key(embeddings.embed_model(), 'x')}.json"
    path.write_text(json.dumps(cached), encoding="utf-8")
    calls = _fake_response(monkeypatch, {"x": [9.0]})

    assert embeddings.embed_text("x") == [9.0]
    assert len(calls) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == [9.0]


def test_invalid_cache_without_proxy_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = embeddings._cache_dir()
    cache.mkdir(parents=True)
    path = cache / f"{embeddings._cache_key(embeddings.embed_model(), 'x')}.json"
    path.write_text("[NaN]", encoding="utf-8")
    assert embeddings.embed_text("x") is None


def test_cache_reembeds_after_dimension_change(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy(monkeypatch)
    _fake_response(monkeypatch, {"x": [1.0]})
    assert embeddings.embed_text("x") == [1.0]

    monkeypatch.setenv("DECEPTICON_SKILLOGY_EMBED_DIM", "2")
    calls = _fake_response(monkeypatch, {"x": [1.0, 2.0]})
    assert embeddings.embed_text("x") == [1.0, 2.0]
    assert len(calls) == 1


# --- degradation -----------------------------------------------------------


def test_request_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy(monkeypatch)

    def boom(*_a: Any, **_k: Any) -> list[list[float]]:
        raise RuntimeError("proxy down")

    monkeypatch.setattr(embeddings, "_request_embeddings", boom)
    assert embeddings.embed_text("y") is None
    assert embeddings.embed_batch(["y", "z"]) == [None, None]


def test_count_mismatch_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy(monkeypatch)

    def short(
        base_url: str, key: str, model: str, inputs: list[str], **kwargs: Any
    ) -> list[list[float]]:
        return [[1.0]]  # one vector for two inputs

    monkeypatch.setattr(embeddings, "_request_embeddings", short)
    assert embeddings.embed_batch(["one", "two"]) == [None, None]


@pytest.mark.parametrize(
    "invalid",
    [[0.0], [float("nan")], [float("-inf")], [1.0, 2.0], [True], ["1.0"]],
)
def test_invalid_provider_vector_does_not_poison_cache(
    monkeypatch: pytest.MonkeyPatch, invalid: list[object]
) -> None:
    _set_proxy(monkeypatch)
    calls = _fake_response(monkeypatch, {"good": [1.0], "bad": invalid})
    assert embeddings.embed_batch(["good", "bad"]) == [[1.0], None]
    assert embeddings.embed_batch(["good", "bad"]) == [[1.0], None]
    assert calls[1]["inputs"] == ["bad"]


def test_no_proxy_batch_all_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # proxy intentionally unset by the autouse fixture
    assert embeddings.embed_batch(["p", "q"]) == [None, None]


# --- response parsing ------------------------------------------------------


def test_request_parses_openai_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_request_embeddings`` sorts by ``index`` and unwraps embeddings."""
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None: ...

        def json(self) -> dict:
            return {
                "data": [
                    {"embedding": [2.0], "index": 1},
                    {"embedding": [1.0], "index": 0},
                ]
            }

    def fake_post(url: str, **kwargs: Any) -> _Resp:
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["headers"] = kwargs["headers"]
        return _Resp()

    monkeypatch.setattr(embeddings.httpx, "post", fake_post)
    out = embeddings._request_embeddings("http://litellm:4000", "sk-test", "m", ["a", "b"])
    assert out == [[1.0], [2.0]]  # reordered by index
    assert captured["url"] == "http://litellm:4000/v1/embeddings"
    assert captured["json"] == {"model": "m", "input": ["a", "b"]}
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


# --- retry / backoff -------------------------------------------------------


class _FakeResp:
    def __init__(
        self, status_code: int, *, body: dict | None = None, retry_after: str | None = None
    ):
        self.status_code = status_code
        self._body = body or {"data": [{"embedding": [1.0], "index": 0}]}
        self.headers = {"Retry-After": retry_after} if retry_after else {}
        self.request = httpx.Request("POST", "http://litellm:4000/v1/embeddings")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=self.request, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._body


def test_retry_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: sleeps.append(s))
    responses = iter([_FakeResp(429), _FakeResp(503), _FakeResp(200)])
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: next(responses))
    out = embeddings._request_embeddings("http://litellm:4000", "sk", "m", ["x"])
    assert out == [[1.0]]
    assert sleeps == [1.0, 2.0]  # exponential backoff before each retry


def test_retry_honours_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: sleeps.append(s))
    responses = iter([_FakeResp(429, retry_after="5"), _FakeResp(200)])
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: next(responses))
    embeddings._request_embeddings("http://litellm:4000", "sk", "m", ["x"])
    assert sleeps == [5.0]


def test_retry_exhausted_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings.time, "sleep", lambda _s: None)
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: _FakeResp(429))
    with pytest.raises(httpx.HTTPStatusError):
        embeddings._request_embeddings("http://litellm:4000", "sk", "m", ["x"])


def test_backoff_is_capped_and_rides_full_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistent 429s back off exponentially but capped, spanning ~a rate
    window before giving up — and a 429 masked as 500 is still retried."""
    sleeps: list[float] = []
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: _FakeResp(500))
    with pytest.raises(httpx.HTTPStatusError):
        embeddings._request_embeddings("http://litellm:4000", "sk", "m", ["x"])
    # _MAX_ATTEMPTS-1 sleeps, exponential then capped at _BACKOFF_CAP
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert all(s <= embeddings._BACKOFF_CAP for s in sleeps)
    assert sum(sleeps) >= 30.0  # patient enough for a rate window


def test_non_retryable_4xx_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def post(*_a: Any, **_k: Any) -> _FakeResp:
        calls.append(1)
        return _FakeResp(401)

    monkeypatch.setattr(embeddings.httpx, "post", post)
    with pytest.raises(httpx.HTTPStatusError):
        embeddings._request_embeddings("http://litellm:4000", "sk", "m", ["x"])
    assert len(calls) == 1  # 401 is not retried


def test_batch_degrades_on_retry_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy(monkeypatch)
    monkeypatch.setattr(embeddings.time, "sleep", lambda _s: None)
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: _FakeResp(429))
    assert embeddings.embed_batch(["a", "b"]) == [None, None]


def test_query_embed_text_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Query-time embed_text must NOT use the patient ingest retry budget — a
    sustained rate limit fails fast (≤ _QUERY_MAX_ATTEMPTS-1 sleeps) → None →
    find_skill falls back to lexical without blocking the agent."""
    _set_proxy(monkeypatch)
    sleeps: list[float] = []
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: _FakeResp(429))
    assert embeddings.embed_text("a live query") is None
    assert len(sleeps) == embeddings._QUERY_MAX_ATTEMPTS - 1  # far fewer than ingest
    assert len(sleeps) < embeddings._MAX_ATTEMPTS - 1
