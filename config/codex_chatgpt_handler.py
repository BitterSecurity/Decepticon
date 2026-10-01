"""LiteLLM custom handler for Codex / ChatGPT OAuth authentication.

Uses the Codex CLI credential store as the source of truth:
``~/.codex/auth.json`` (or ``CODEX_AUTH_PATH`` / ``CODEX_HOME``). Unlike
LiteLLM's native ``chatgpt`` provider — which keeps a parallel store at
``~/.config/litellm/chatgpt/auth.json`` and requires a manual ``codex
login`` re-import — this handler reads and writes the same file Codex CLI
itself uses, so a refresh on either side is visible to both.

Key behaviors:
  - ``FileBackedCache`` watches ``auth.json`` mtime so a host-side
    ``codex login`` is picked up by the running container without restart.
  - Refreshes are written back atomically (temp + rename, 0o600) so a
    racing host reader never sees a partial file.
  - 401 from chatgpt.com triggers a single forced refresh + replay; if the
    second attempt also 401s, raise ``AuthenticationError`` with the
    response body so the user knows to rerun ``codex login``.

Registration: dispatched from ``auth_handler.py`` via the ``auth/gpt-*``
model prefix.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import litellm
from http_client import async_client, sync_client
from http_client import post as _http_post
from litellm import CustomLLM, ModelResponse
from oauth_token_store import (
    DEFAULT_JWT_SKEW_SECONDS,
    FileBackedCache,
    decode_jwt_payload,
    is_jwt_expired,
    oauth_refresh_request,
    read_json_file,
    with_retry_on_401,
    write_json_atomic,
)

CHATGPT_AUTH_BASE = "https://auth.openai.com"
CHATGPT_OAUTH_TOKEN_URL = f"{CHATGPT_AUTH_BASE}/oauth/token"
CHATGPT_API_BASE = "https://chatgpt.com/backend-api/codex"
CHATGPT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_ORIGINATOR = "codex_cli_rs"
DEFAULT_USER_AGENT = "codex_cli_rs/0.0.0 (Unknown 0; unknown) unknown"


def _codex_auth_path() -> Path:
    explicit = os.environ.get("CODEX_AUTH_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"
    return Path(os.path.expanduser("~/.codex/auth.json"))


def _required_token_fields() -> tuple[str, ...]:
    return ("access_token", "refresh_token", "id_token")


def _validate_auth(raw: dict[str, Any], path: Path) -> dict[str, Any]:
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict) or not all(tokens.get(k) for k in _required_token_fields()):
        raise litellm.AuthenticationError(
            message=(
                f"Codex ChatGPT credentials at {path} are missing "
                "tokens.access_token, tokens.refresh_token, or tokens.id_token. "
                "Run 'codex login'."
            ),
            model="auth",
            llm_provider="auth",
        )
    return raw


def _load_codex_auth(path: Path) -> dict[str, Any] | None:
    """Loader for FileBackedCache. Validates structure, returns None if missing.

    The cache layer translates a None return into a re-read on the next
    call. We raise AuthenticationError only when a present file is
    malformed — a missing file is recoverable by the user running
    ``codex login`` while the container keeps running.
    """
    raw = read_json_file(path)
    if raw is None:
        return None
    return _validate_auth(raw, path)


_auth_cache = FileBackedCache(_codex_auth_path(), _load_codex_auth)


def _read_auth() -> dict[str, Any]:
    auth = _auth_cache.get()
    if auth is None:
        path = _codex_auth_path()
        raise litellm.AuthenticationError(
            message=f"Codex ChatGPT credentials not found at {path}. Run 'codex login'.",
            model="auth",
            llm_provider="auth",
        )
    return auth


def _write_auth(auth: dict[str, Any]) -> None:
    """Persist auth back to the Codex CLI store and refresh the cache key."""
    path = _codex_auth_path()
    write_json_atomic(path, auth)
    # Even if the on-disk write failed (read-only mount), keep the
    # in-process cache up to date so the rest of the container session
    # uses the refreshed token.
    _auth_cache.replace(auth)


def _extract_account_id(id_token: str | None, access_token: str | None) -> str | None:
    for token in (id_token, access_token):
        auth_claims = decode_jwt_payload(token).get("https://api.openai.com/auth")
        if isinstance(auth_claims, dict):
            account_id = auth_claims.get("chatgpt_account_id")
            if isinstance(account_id, str) and account_id:
                return account_id
    return None


def _refresh_tokens(auth: dict[str, Any]) -> dict[str, Any]:
    tokens = auth["tokens"]
    data = oauth_refresh_request(
        CHATGPT_OAUTH_TOKEN_URL,
        {
            "client_id": CHATGPT_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "scope": "openid profile email",
        },
        provider_label="auth",
    )
    access_token = data.get("access_token")
    id_token = data.get("id_token")
    if not access_token or not id_token:
        # Never interpolate the raw token response (it carries access /
        # refresh / id tokens). Report only the names of the fields that are
        # expected but absent / empty.
        missing = [name for name in ("access_token", "id_token") if not data.get(name)]
        raise litellm.AuthenticationError(
            message="Codex ChatGPT refresh response missing fields: " + ", ".join(missing),
            model="auth",
            llm_provider="auth",
        )

    tokens["access_token"] = access_token
    tokens["id_token"] = id_token
    tokens["refresh_token"] = data.get("refresh_token", tokens["refresh_token"])
    account_id = _extract_account_id(id_token, access_token)
    if account_id:
        tokens["account_id"] = account_id
    auth["auth_mode"] = "chatgpt"
    auth["last_refresh"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _write_auth(auth)
    return auth


def get_codex_access_token(force_refresh: bool = False) -> tuple[str, str | None]:
    """Return ``(access_token, account_id)`` ready for header injection."""
    auth = _read_auth()
    token = auth["tokens"]["access_token"]
    if force_refresh or is_jwt_expired(token, skew_seconds=DEFAULT_JWT_SKEW_SECONDS):
        auth = _refresh_tokens(auth)
        token = auth["tokens"]["access_token"]
    tokens = auth["tokens"]
    account_id = tokens.get("account_id") or _extract_account_id(tokens.get("id_token"), token)
    return token, account_id


def _headers(access_token: str, account_id: str | None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "content-type": "application/json",
        "accept": "text/event-stream",
        "originator": os.environ.get("CHATGPT_ORIGINATOR", DEFAULT_ORIGINATOR),
        "user-agent": os.environ.get("CHATGPT_USER_AGENT", DEFAULT_USER_AGENT),
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return headers


def _message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _responses_input(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    input_items: list[dict[str, Any]] = []
    instructions: list[str] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            text = _message_text(msg.get("content"))
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id") or "tool_call",
                    "output": _message_text(msg.get("content")),
                }
            )
            continue
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg.get("tool_calls") or []:
                # tool_calls may arrive as OpenAI dicts or as LangGraph
                # objects; normalize both so non-dicts don't blow up with a
                # raw AttributeError on ``tc.get``.
                if isinstance(tc, dict):
                    func = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                    tc_id = tc.get("id")
                    tc_name = tc.get("name")
                    tc_args = tc.get("args", {})
                else:
                    func = {}
                    fn_obj = getattr(tc, "function", None)
                    if fn_obj is not None:
                        func = {
                            "name": getattr(fn_obj, "name", None),
                            "arguments": getattr(fn_obj, "arguments", None),
                        }
                    tc_id = getattr(tc, "id", None)
                    tc_name = getattr(tc, "name", None)
                    tc_args = getattr(tc, "args", {})
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": tc_id or f"call_{func.get('name', 'tool')}",
                        "name": func.get("name") or tc_name or "tool",
                        "arguments": func.get("arguments") or json.dumps(tc_args or {}),
                    }
                )
            continue
        mapped_role = "assistant" if role == "assistant" else "user"
        input_items.append({"role": mapped_role, "content": _message_text(msg.get("content"))})
    return input_items, "\n\n".join(instructions) if instructions else None


def _responses_tools(tools: Any) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    out = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            fn = tool["function"]
            out.append(
                {
                    "type": "function",
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        else:
            out.append(tool)
    return out


def _upstream_model_slug(model: str) -> str:
    """Translate a LiteLLM-side model id back to the chatgpt.com model slug.

    Two routes lead here:

      - ``auth/gpt-*``  — handler dispatched directly via auth_handler.
        Slug is already the upstream name.
      - ``codex-oauth/oauth-gpt-*`` — dynamic-config alias. The ``oauth-``
        sentinel exists only to dodge LiteLLM's
        ``open_ai_chat_completion_models`` short-circuit in main.py:2561,
        which would otherwise route ``gpt-*`` straight to api.openai.com.
        Strip it here so chatgpt.com receives the canonical ``gpt-*``.
    """
    slug = model.split("/", 1)[-1] if "/" in model else model
    if slug.startswith("oauth-"):
        slug = slug.removeprefix("oauth-")
    return slug


def _request_body(
    model: str, messages: list[dict[str, Any]], optional_params: dict[str, Any] | None
) -> dict[str, Any]:
    opts = optional_params or {}
    input_items, instructions = _responses_input(messages)
    # ChatGPT Codex Responses API requires ``instructions`` to be present
    # — sending an empty string or omitting the field both 400 with
    # ``Instructions are required``. Default to a minimal Codex CLI
    # prompt so requests without a system message still go through.
    if not instructions:
        instructions = "You are Codex, a coding assistant for the terminal."
    body: dict[str, Any] = {
        "model": _upstream_model_slug(model),
        "input": input_items,
        "stream": True,
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "instructions": instructions,
    }
    tools = _responses_tools(opts.get("tools"))
    if tools:
        body["tools"] = tools
    if opts.get("tool_choice"):
        body["tool_choice"] = opts["tool_choice"]

    # Reasoning — GPT-5.x are reasoning models (effort none|low|medium|high|
    # xhigh). Caller-supplied ``reasoning`` (Responses shape) wins; else honor
    # the OpenAI-style ``reasoning_effort`` alias; else default to ``medium``
    # (the vendor's balanced starting point) so requests get consistent depth.
    # NOTE: the ChatGPT Codex backend REJECTS ``max_output_tokens`` ("Unsupported
    # parameter", verified 2026-07-13 against gpt-5.5 and gpt-5.6-sol) — do not
    # add it here. Output length is governed by the effort level, not a cap.
    if opts.get("reasoning"):
        body["reasoning"] = opts["reasoning"]
    elif opts.get("reasoning_effort"):
        body["reasoning"] = {"effort": opts["reasoning_effort"]}
    else:
        body["reasoning"] = {"effort": os.environ.get("DECEPTICON_GPT_EFFORT", "medium")}
    return body


def _completed_payload(resp: httpx.Response) -> dict[str, Any]:
    """Walk the SSE stream and return the ``response.completed`` payload.

    The Codex backend often returns a ``response.completed`` event whose
    ``output`` array is empty even when the assistant produced text — the
    actual content is streamed in ``response.output_text.delta`` events
    instead. We aggregate those deltas (and ``function_call_arguments.delta``
    chunks) so the downstream parser sees a normal Responses-API output
    array regardless of which path the backend used.
    """
    error_message: str | None = None
    completed: dict[str, Any] | None = None
    text_parts: list[str] = []
    function_calls: dict[str, dict[str, Any]] = {}

    for raw_line in resp.text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                text_parts.append(delta)
        elif event_type == "response.output_item.added":
            # FIX: capture the function_call name + call_id from the
            # ``output_item.added`` event. The ``function_call_arguments.delta``
            # events that follow only carry ``item_id`` + arguments, NOT the
            # name. Without this, synthesized function_calls have name="" and
            # the model loops because tool_results can't be linked to the
            # original tool_call.
            item = event.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "function_call":
                item_id = item.get("id") or item.get("call_id") or "tool_call"
                entry = function_calls.setdefault(
                    item_id,
                    {
                        "type": "function_call",
                        "call_id": item_id,
                        "name": "",
                        "arguments": "",
                    },
                )
                entry["call_id"] = item.get("call_id") or entry["call_id"]
                entry["name"] = item.get("name") or entry["name"]
                entry["arguments"] = entry["arguments"] or item.get("arguments") or ""
        elif event_type == "response.function_call_arguments.delta":
            item_id = event.get("item_id") or event.get("call_id") or "tool_call"
            entry = function_calls.setdefault(
                item_id,
                {
                    "type": "function_call",
                    "call_id": item_id,
                    "name": event.get("name") or "",
                    "arguments": "",
                },
            )
            delta = event.get("delta")
            if isinstance(delta, str):
                entry["arguments"] += delta
        elif event_type == "response.output_item.done":
            # Backfill name/call_id if the ``output_item.added`` event was
            # missed (defensive — some upstream variants only emit ``done``).
            item = event.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "function_call":
                item_id = item.get("id") or item.get("call_id") or "tool_call"
                entry = function_calls.setdefault(
                    item_id,
                    {
                        "type": "function_call",
                        "call_id": item.get("call_id") or item_id,
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "",
                    },
                )
                if not entry.get("name") and item.get("name"):
                    entry["name"] = item["name"]
                if not entry.get("call_id") and item.get("call_id"):
                    entry["call_id"] = item["call_id"]
        elif event_type == "response.completed" and isinstance(event.get("response"), dict):
            completed = event["response"]
        elif event_type in {"response.failed", "error"}:
            err = event.get("error") or (event.get("response") or {}).get("error")
            if isinstance(err, dict):
                error_message = err.get("message") or json.dumps(err)
            elif err:
                error_message = str(err)

    if completed is not None:
        # Backfill output when the upstream sent an empty ``output`` array
        # but streamed text/tool deltas we aggregated above. Existing
        # entries take precedence so non-empty completed payloads pass
        # through unchanged.
        existing = completed.get("output") or []
        if not existing:
            synthesized: list[dict[str, Any]] = []
            text = "".join(text_parts)
            if text:
                synthesized.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            synthesized.extend(function_calls.values())
            if synthesized:
                completed["output"] = synthesized
        return completed
    raise litellm.APIError(
        status_code=resp.status_code,
        message=(
            error_message
            or resp.text
            or "ChatGPT Codex stream ended without a response.completed event."
        ),
        model="auth",
        llm_provider="auth",
    )


def _raise_for_stream_status(resp: httpx.Response, model: str) -> None:
    """Mirror the buffered path's typed errors for a streaming response."""
    if resp.status_code < 400:
        return
    resp.read()
    _raise_stream_error(resp.status_code, resp.text, model)


async def _araise_for_stream_status(resp: httpx.Response, model: str) -> None:
    """Async twin of :func:`_raise_for_stream_status`."""
    if resp.status_code < 400:
        return
    await resp.aread()
    _raise_stream_error(resp.status_code, resp.text, model)


def _raise_stream_error(status_code: int, text: str, model: str) -> None:
    if status_code == 401:
        raise litellm.AuthenticationError(
            message=(
                "Codex ChatGPT authentication was rejected. Run 'codex logout' "
                f"and 'codex login'. Underlying: {text}"
            ),
            model=model,
            llm_provider="auth",
        )
    raise litellm.APIError(
        status_code=status_code,
        message=f"ChatGPT Codex API error: {text}",
        model=model,
        llm_provider="auth",
    )


class _CodexSseAccumulator:
    """Responses-API SSE → GenericStreamingChunk translator.

    The buffered path already walks this same event stream (see
    :func:`_completed_payload`) but only after ``resp.text`` has pulled the
    whole body, so the deltas it aggregates have all arrived before the first
    chunk is emitted. This is the same walk driven incrementally: text deltas
    are forwarded as they land, and function-call arguments are still
    reassembled from their fragments before the tool chunk is released.

    Pure state machine over decoded lines — no network — so the wire format is
    unit-testable. Chunk shape matches
    :meth:`CodexChatGPTCustomHandler._response_to_chunks`.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        # item_id → in-flight function_call. The Responses API streams a call's
        # name on `output_item.added` and its arguments as later fragments.
        self._calls: dict[str, dict[str, Any]] = {}
        self._emitted = 0
        self._usage: dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._finished = False
        self._error: str | None = None

    def push(self, line: str) -> list[dict[str, Any]]:
        line = line.strip()
        if not line.startswith("data:"):
            return []
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            return []
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return []
        if not isinstance(event, dict):
            return []
        return self._dispatch(event)

    def close(self) -> list[dict[str, Any]]:
        if self._error is not None:
            raise litellm.APIError(
                status_code=500,
                message=self._error,
                model=self._model,
                llm_provider="auth",
            )
        if self._finished:
            return []
        return [self._final_chunk()]

    # ── internals ────────────────────────────────────────────────────
    def _dispatch(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")

        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if not isinstance(delta, str) or not delta:
                return []
            return [
                {
                    "text": delta,
                    "is_finished": False,
                    "finish_reason": "",
                    "index": 0,
                    "tool_use": None,
                    "usage": None,
                }
            ]

        if kind == "response.output_item.added":
            item = event.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "function_call":
                item_id = item.get("id") or item.get("call_id") or "tool_call"
                entry = self._calls.setdefault(
                    item_id, {"call_id": item_id, "name": "", "arguments": ""}
                )
                entry["call_id"] = item.get("call_id") or entry["call_id"]
                entry["name"] = item.get("name") or entry["name"]
                entry["arguments"] = entry["arguments"] or item.get("arguments") or ""
            return []

        if kind == "response.function_call_arguments.delta":
            item_id = event.get("item_id") or event.get("call_id") or "tool_call"
            entry = self._calls.setdefault(
                item_id,
                {"call_id": item_id, "name": event.get("name") or "", "arguments": ""},
            )
            delta = event.get("delta")
            if isinstance(delta, str):
                entry["arguments"] += delta
            return []

        if kind == "response.output_item.done":
            return self._on_item_done(event)

        if kind == "response.completed":
            payload = event.get("response")
            if isinstance(payload, dict):
                self._absorb_usage(payload.get("usage"))
            self._finished = True
            return [self._final_chunk()]

        if kind in {"response.failed", "error"}:
            err = event.get("error") or (event.get("response") or {}).get("error")
            if isinstance(err, dict):
                self._error = err.get("message") or json.dumps(err)
            elif err:
                self._error = str(err)
            else:
                self._error = "ChatGPT Codex stream reported an error"
            return []

        return []

    def _on_item_done(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        item = event.get("item") or {}
        if not isinstance(item, dict) or item.get("type") != "function_call":
            return []
        item_id = item.get("id") or item.get("call_id") or "tool_call"
        entry = self._calls.pop(
            item_id,
            {"call_id": item.get("call_id") or item_id, "name": "", "arguments": ""},
        )
        # `done` is authoritative. Its explicit ``call_id`` wins over the entry's:
        # a `function_call_arguments.delta` that arrives without one seeds the
        # entry with the ITEM id, and returning that as the tool-call id breaks
        # tool-result linking (the API matches results on ``call_id``).
        name = item.get("name") or entry.get("name") or ""
        call_id = item.get("call_id") or entry.get("call_id") or item_id
        arguments = entry.get("arguments") or item.get("arguments") or "{}"
        chunk = {
            "text": "",
            "is_finished": False,
            "finish_reason": "",
            "index": 0,
            "tool_use": {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
                "index": self._emitted,
            },
            "usage": None,
        }
        self._emitted += 1
        return [chunk]

    def _absorb_usage(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        self._usage = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": usage.get("total_tokens") or (input_tokens + output_tokens),
        }

    def _final_chunk(self) -> dict[str, Any]:
        self._finished = True
        return {
            "text": "",
            "is_finished": True,
            "finish_reason": "tool_calls" if self._emitted else "stop",
            "index": 0,
            "tool_use": None,
            "usage": self._usage,
        }


def _model_response(model: str, payload: dict[str, Any]) -> ModelResponse:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                    text = block.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
        elif item.get("type") == "function_call":
            tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", "{}"),
                    },
                }
            )
    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = payload.get("usage") or {}
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    return ModelResponse(
        id=payload.get("id", f"chatcmpl-{model}"),
        model=model,
        choices=[
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        usage={
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": usage.get("total_tokens", input_tokens + output_tokens),
        },
    )


class CodexChatGPTCustomHandler(CustomLLM):
    """LiteLLM custom handler for ``auth/gpt-*`` routes."""

    def completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        api_base: str | None = None,
        custom_prompt_dict: dict[str, Any] | None = None,
        model_response: ModelResponse | None = None,
        print_verbose: Any = None,
        encoding: Any = None,
        logging_obj: Any = None,
        optional_params: dict[str, Any] | None = None,
        acompletion: bool | None = None,
        timeout: float | None = None,
        litellm_params: dict[str, Any] | None = None,
        logger_fn: Any = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        body = _request_body(model, messages, optional_params)
        api_root = (api_base or os.environ.get("CHATGPT_API_BASE") or CHATGPT_API_BASE).rstrip("/")

        def _send(force_refresh: bool) -> httpx.Response:
            access_token, account_id = get_codex_access_token(force_refresh=force_refresh)
            return _http_post(
                f"{api_root}/responses",
                json=body,
                headers={**_headers(access_token, account_id), **(headers or {})},
                timeout=timeout or 600,
            )

        resp = with_retry_on_401(_send)
        if resp.status_code == 401:
            raise litellm.AuthenticationError(
                message=(
                    "Codex ChatGPT authentication was rejected. Run 'codex logout' "
                    f"and 'codex login'. Underlying: {resp.text}"
                ),
                model=model,
                llm_provider="auth",
            )
        if resp.status_code >= 400:
            raise litellm.APIError(
                status_code=resp.status_code,
                message=f"ChatGPT Codex API error: {resp.text}",
                model=model,
                llm_provider="auth",
            )
        return _model_response(model, _completed_payload(resp))

    async def acompletion(self, *args: Any, **kwargs: Any) -> ModelResponse:
        import asyncio
        import functools

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(self.completion, *args, **kwargs))

    def _response_to_chunks(self, response: ModelResponse) -> list[dict[str, Any]]:
        choice = response.choices[0]
        msg = choice.message if hasattr(choice, "message") else choice.get("message", {})
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        raw_tool_calls = (
            msg.get("tool_calls", []) if isinstance(msg, dict) else getattr(msg, "tool_calls", [])
        )
        usage = {
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        }
        if raw_tool_calls:
            chunks = []
            if content:
                chunks.append(
                    {
                        "text": content,
                        "is_finished": False,
                        "finish_reason": "",
                        "index": 0,
                        "tool_use": None,
                        "usage": None,
                    }
                )
            for index, tool_call in enumerate(raw_tool_calls):
                if not isinstance(tool_call, dict):
                    tool_call = {
                        "id": getattr(tool_call, "id", f"call_{index}"),
                        "type": "function",
                        "function": getattr(tool_call, "function", {}),
                    }
                chunks.append(
                    {
                        "text": "",
                        "is_finished": index == len(raw_tool_calls) - 1,
                        "finish_reason": "tool_calls" if index == len(raw_tool_calls) - 1 else "",
                        "index": 0,
                        "tool_use": {**tool_call, "index": index},
                        "usage": usage if index == len(raw_tool_calls) - 1 else None,
                    }
                )
            return chunks
        return [
            {
                "text": content or "",
                "is_finished": True,
                "finish_reason": choice.get("finish_reason", "stop")
                if isinstance(choice, dict)
                else getattr(choice, "finish_reason", "stop"),
                "index": 0,
                "tool_use": None,
                "usage": usage,
            }
        ]

    def streaming(
        self,
        model: str,
        messages: list[dict[str, Any]],
        api_base: str | None = None,
        custom_prompt_dict: dict[str, Any] | None = None,
        model_response: ModelResponse | None = None,
        print_verbose: Any = None,
        encoding: Any = None,
        logging_obj: Any = None,
        optional_params: dict[str, Any] | None = None,
        acompletion: bool | None = None,
        timeout: float | None = None,
        litellm_params: dict[str, Any] | None = None,
        logger_fn: Any = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        """Sync token streaming off the Responses API SSE."""
        body = _request_body(model, messages, optional_params)
        api_root = (api_base or os.environ.get("CHATGPT_API_BASE") or CHATGPT_API_BASE).rstrip("/")
        with sync_client(timeout=timeout or 600) as client:
            for force_refresh in (False, True):
                access_token, account_id = get_codex_access_token(force_refresh=force_refresh)
                with client.stream(
                    "POST",
                    f"{api_root}/responses",
                    json=body,
                    headers={**_headers(access_token, account_id), **(headers or {})},
                ) as resp:
                    if resp.status_code == 401 and not force_refresh:
                        resp.read()
                        continue
                    _raise_for_stream_status(resp, model)
                    accumulator = _CodexSseAccumulator(model)
                    for line in resp.iter_lines():
                        yield from accumulator.push(line)
                    yield from accumulator.close()
                    return

    async def astreaming(
        self,
        model: str,
        messages: list[dict[str, Any]],
        api_base: str | None = None,
        custom_prompt_dict: dict[str, Any] | None = None,
        model_response: ModelResponse | None = None,
        print_verbose: Any = None,
        encoding: Any = None,
        logging_obj: Any = None,
        optional_params: dict[str, Any] | None = None,
        acompletion: bool | None = None,
        timeout: float | None = None,
        litellm_params: dict[str, Any] | None = None,
        logger_fn: Any = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async token streaming off the Responses API SSE."""
        body = _request_body(model, messages, optional_params)
        api_root = (api_base or os.environ.get("CHATGPT_API_BASE") or CHATGPT_API_BASE).rstrip("/")
        async with async_client(timeout=timeout or 600) as client:
            for force_refresh in (False, True):
                access_token, account_id = get_codex_access_token(force_refresh=force_refresh)
                async with client.stream(
                    "POST",
                    f"{api_root}/responses",
                    json=body,
                    headers={**_headers(access_token, account_id), **(headers or {})},
                ) as resp:
                    if resp.status_code == 401 and not force_refresh:
                        await resp.aread()
                        continue
                    await _araise_for_stream_status(resp, model)
                    accumulator = _CodexSseAccumulator(model)
                    async for line in resp.aiter_lines():
                        for chunk in accumulator.push(line):
                            yield chunk
                    for chunk in accumulator.close():
                        yield chunk
                    return


codex_chatgpt_handler_instance = CodexChatGPTCustomHandler()
