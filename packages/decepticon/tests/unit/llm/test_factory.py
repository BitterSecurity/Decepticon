"""Unit tests for decepticon.llm.factory."""

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from decepticon.llm.factory import (
    LLM_MAX_TOKENS_ENV,
    LLMFactory,
    _is_real_key,
    _llamacpp_local_configured,
    _method_is_configured,
    _model_max_output_tokens,
    _oauth_credentials_present,
    _oauth_env_credentials_present,
    _resolve_credentials,
    _resolve_max_tokens,
)
from decepticon_core.types.llm import (
    AuthMethod,
    Credentials,
    LLMModelMapping,
    ModelProfile,
    ProxyConfig,
)


class TestIsRealKey:
    """Vendor-aware API key validation.

    The launcher writes ``your-…-key-here`` placeholders into .env so the
    user can later swap in a real key. The factory needs to reject those
    plus any obvious junk (short strings, ``placeholder``/``not-used``
    markers, vendor-prefix mismatches) so the credentials inventory
    reflects what actually works at request time.
    """

    def test_rejects_empty_and_placeholder_template(self) -> None:
        assert _is_real_key("") is False
        assert _is_real_key("   ") is False
        assert _is_real_key("your-anthropic-key-here") is False
        assert _is_real_key("YOUR-OPENAI-KEY-HERE") is False  # case-insensitive

    def test_rejects_short_strings(self) -> None:
        # Under 24 chars — every vendor-issued key exceeds this.
        assert _is_real_key("sk-tooshort") is False

    def test_rejects_placeholder_tokens_in_value(self) -> None:
        long_enough = "x" * 30
        for token in ("placeholder", "not-used", "dummy", "fake", "example"):
            assert _is_real_key(f"sk-{token}-{long_enough}") is False, token

    def test_accepts_realistic_keys_without_method(self) -> None:
        # Without method context, prefix check is skipped.
        assert _is_real_key("sk-ant-api03-realtokenfortestingauthrouting12345") is True
        assert _is_real_key("AIzaSyDeadBeefDeadBeefDeadBeefDeadBeef0") is True

    def test_rejects_wrong_vendor_prefix(self) -> None:
        # An OpenAI-shaped key in the Anthropic slot must be caught.
        openai_key = "sk-proj-realopenaitokenfortestingauthrouting12345"
        assert _is_real_key(openai_key, AuthMethod.ANTHROPIC_API) is False
        # …and vice versa.
        anthropic_key = "sk-ant-api03-realtokenfortestingauthrouting12345"
        assert _is_real_key(anthropic_key, AuthMethod.GOOGLE_API) is False

    def test_accepts_correct_vendor_prefix(self) -> None:
        anthropic_key = "sk-ant-api03-realtokenfortestingauthrouting12345"
        assert _is_real_key(anthropic_key, AuthMethod.ANTHROPIC_API) is True
        google_key = "AIzaSyDeadBeefDeadBeefDeadBeefDeadBeef0"
        assert _is_real_key(google_key, AuthMethod.GOOGLE_API) is True


class TestOAuthCredentialsPresent:
    """OAuth detection requires the credential file alongside the boolean.

    Without the file check, ``DECEPTICON_AUTH_CLAUDE_CODE=true`` plus a
    deleted ``~/.claude/.credentials.json`` would still place OAuth in
    every fallback chain and generate one 401 per request.
    """

    def test_returns_false_when_file_missing(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CODE_CREDENTIALS_PATH", str(tmp_path / "absent.json"))
        assert _oauth_credentials_present(AuthMethod.ANTHROPIC_OAUTH) is False

    def test_returns_false_when_file_is_empty(self, monkeypatch, tmp_path) -> None:
        # ``/dev/null``-style mounts read as empty — must fail closed.
        empty = tmp_path / "empty.json"
        empty.write_text("")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CODE_CREDENTIALS_PATH", str(empty))
        assert _oauth_credentials_present(AuthMethod.ANTHROPIC_OAUTH) is False

    def test_returns_false_on_invalid_json(self, monkeypatch, tmp_path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not-json")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CODE_CREDENTIALS_PATH", str(bad))
        assert _oauth_credentials_present(AuthMethod.ANTHROPIC_OAUTH) is False

    def test_returns_true_when_file_is_well_formed(self, monkeypatch, tmp_path) -> None:
        good = tmp_path / "credentials.json"
        good.write_text('{"claudeAiOauth": {"accessToken": "sk-ant-oat01-deadbeef"}}')
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CODE_CREDENTIALS_PATH", str(good))
        assert _oauth_credentials_present(AuthMethod.ANTHROPIC_OAUTH) is True

    def test_codex_path_via_env_override(self, monkeypatch, tmp_path) -> None:
        good = tmp_path / "auth.json"
        good.write_text('{"tokens": {"access_token": "ABC"}}')
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CODEX_AUTH_PATH", str(good))
        assert _oauth_credentials_present(AuthMethod.OPENAI_OAUTH) is True


class TestOAuthEnvCredentials:
    """B12: env-backed OAuth creds count as configured, not just on-disk files.
    Before the fix, ``_method_is_configured`` required a credential *file* even
    though the LiteLLM handlers also accept env-var tokens (GEMINI_ACCESS_TOKEN,
    GROK_SESSION_TOKEN, COPILOT_REFRESH_TOKEN, …). Env-only setups were silently
    dropped from the fallback chain.
    """

    def test_env_present_returns_true_for_each_method(self, monkeypatch) -> None:
        cases = {
            AuthMethod.GOOGLE_OAUTH: "GEMINI_ACCESS_TOKEN",
            AuthMethod.COPILOT_OAUTH: "COPILOT_REFRESH_TOKEN",
            AuthMethod.GROK_OAUTH: "GROK_SESSION_TOKEN",
            AuthMethod.PERPLEXITY_OAUTH: "PERPLEXITY_SESSION_TOKEN",
            AuthMethod.ANTHROPIC_OAUTH: "ANTHROPIC_OAUTH_TOKEN",
        }
        for method, env_var in cases.items():
            monkeypatch.setenv(env_var, "tok-from-env")
            assert _oauth_env_credentials_present(method) is True
            monkeypatch.delenv(env_var, raising=False)

    def test_env_only_method_is_configured_without_file(self, monkeypatch, tmp_path) -> None:
        # Flag true + env token, but the credential file is absent.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("GEMINI_TOKENS_PATH", str(tmp_path / "absent.json"))
        monkeypatch.setenv("DECEPTICON_AUTH_GEMINI", "true")
        monkeypatch.setenv("GEMINI_ACCESS_TOKEN", "ya29.env-token")
        assert _oauth_credentials_present(AuthMethod.GOOGLE_OAUTH) is False
        assert _method_is_configured(AuthMethod.GOOGLE_OAUTH) is True

    def test_method_not_configured_without_file_or_env(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("GEMINI_TOKENS_PATH", str(tmp_path / "absent.json"))
        monkeypatch.setenv("DECEPTICON_AUTH_GEMINI", "true")
        monkeypatch.delenv("GEMINI_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("GEMINI_SESSION_COOKIES", raising=False)
        assert _method_is_configured(AuthMethod.GOOGLE_OAUTH) is False

    def test_env_creds_still_require_intent_flag(self, monkeypatch, tmp_path) -> None:
        # Env token present but the boolean intent flag is off → not configured.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("GEMINI_TOKENS_PATH", str(tmp_path / "absent.json"))
        monkeypatch.delenv("DECEPTICON_AUTH_GEMINI", raising=False)
        monkeypatch.setenv("GEMINI_ACCESS_TOKEN", "ya29.env-token")
        assert _method_is_configured(AuthMethod.GOOGLE_OAUTH) is False

    def test_codex_has_no_env_token_override(self, monkeypatch) -> None:
        # Codex authenticates only from its file; no env-token override exists.
        monkeypatch.setenv("CODEX_ACCESS_TOKEN", "should-not-count")
        assert _oauth_env_credentials_present(AuthMethod.OPENAI_OAUTH) is False


class TestMaxTokensResolution:
    """Per-model output-token ceiling (fixes write_file truncation)."""

    def test_opus_and_sonnet_get_128k(self) -> None:
        assert _model_max_output_tokens("anthropic/claude-opus-4-8") == 128000
        assert _model_max_output_tokens("auth/claude-opus-4-8") == 128000
        assert _model_max_output_tokens("openrouter/anthropic/claude-sonnet-4-6") == 128000

    def test_haiku_gets_64k(self) -> None:
        assert _model_max_output_tokens("anthropic/claude-haiku-4-5") == 64000

    def test_unknown_model_falls_back_to_safe_default(self) -> None:
        assert _model_max_output_tokens("openai/gpt-5-nano") == 64000

    def test_env_override_wins(self, monkeypatch) -> None:
        monkeypatch.setenv(LLM_MAX_TOKENS_ENV, "8192")
        # Override wins even for a model whose own ceiling is higher.
        assert _resolve_max_tokens("anthropic/claude-opus-4-8") == 8192

    def test_invalid_env_falls_back_to_per_model(self, monkeypatch) -> None:
        monkeypatch.setenv(LLM_MAX_TOKENS_ENV, "not-a-number")
        assert _resolve_max_tokens("anthropic/claude-opus-4-8") == 128000

    def test_no_env_uses_per_model(self, monkeypatch) -> None:
        monkeypatch.delenv(LLM_MAX_TOKENS_ENV, raising=False)
        assert _resolve_max_tokens("anthropic/claude-haiku-4-5") == 64000


class TestLLMFactory:
    def setup_method(self):
        self.proxy = ProxyConfig(url="http://localhost:4000", api_key="test-key")
        # Build an explicit mapping so the test doesn't depend on env vars.
        creds = Credentials.all_api_methods()
        self.mapping = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.ECO)
        self.factory = LLMFactory(self.proxy, self.mapping)

    def test_ollama_keeps_text_streaming_but_buffers_tool_calls(self, monkeypatch) -> None:
        monkeypatch.delenv("DECEPTICON_LLM_DISABLE_STREAMING", raising=False)

        model = self.factory._create_chat_model("ollama_chat/mistral:7b-instruct", 0)

        assert model.streaming is True
        assert model.disable_streaming == "tool_calling"

    def test_gpt6_agent_calls_use_responses_api(self) -> None:
        for model_id in (
            "openai/gpt-6-astra",
            "openai/gpt-6.1-sol",
            "openai/gpt-6-sol",
            "openai/gpt-6-luna",
        ):
            model = self.factory._create_chat_model(model_id, 0.2)
            assert model.use_responses_api is True
            assert model.disabled_params == {"temperature": None}
            assert model.max_tokens == 128000
        previous = self.factory._create_chat_model("openai/gpt-5.5", 0.2)
        assert previous.use_responses_api is None

    def test_gemini_3_8_omits_deprecated_sampling_params(self) -> None:
        model = self.factory._create_chat_model("gemini/gemini-3.8-flash", 0.2)
        assert model.temperature is None
        assert model.disabled_params == {
            "temperature": None,
            "top_p": None,
            "top_k": None,
        }

    @pytest.mark.parametrize(
        "model_id",
        [
            "anthropic/claude-fable-5-1",
            "anthropic/claude-opus-5-5",
            "anthropic/claude-sonnet-5-5",
        ],
    )
    def test_claude_tool_turn_preserves_thinking_blocks(self, monkeypatch, model_id) -> None:
        from decepticon.llm.factory import _ClaudeThinkingChatOpenAI

        requests: list[dict] = []
        blocks = [
            {"type": "thinking", "thinking": "", "signature": "opaque-signature"},
            {"type": "redacted_thinking", "data": "opaque-redacted-data"},
        ]

        class ProxyHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                assert self.path == "/v1/chat/completions"
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                if len(requests) == 1:
                    message = {
                        "role": "assistant",
                        "content": None,
                        "thinking_blocks": blocks,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "ping", "arguments": "{}"},
                            }
                        ],
                    }
                    finish_reason = "tool_calls"
                else:
                    message = {"role": "assistant", "content": "done"}
                    finish_reason = "stop"
                response = json.dumps(
                    {
                        "id": "chatcmpl_test",
                        "object": "chat.completion",
                        "created": 1,
                        "model": model_id,
                        "choices": [
                            {"index": 0, "message": message, "finish_reason": finish_reason}
                        ],
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            factory = LLMFactory(
                ProxyConfig(url=f"http://127.0.0.1:{server.server_port}/v1", api_key="test-key")
            )
            model = factory._create_chat_model(model_id, 0.2)
            assert isinstance(model, _ClaudeThinkingChatOpenAI)
            assert model.disable_streaming == "tool_calling"
            assert model.disabled_params == {
                "temperature": None,
                "top_p": None,
                "top_k": None,
            }
            bound = model.bind_tools(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "ping",
                            "description": "Test tool",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ]
            )
            first = bound.invoke([HumanMessage(content="ping")])
            second = bound.invoke(
                [
                    HumanMessage(content="ping"),
                    first,
                    ToolMessage(content="pong", tool_call_id="call_1"),
                ]
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        assert first.additional_kwargs["thinking_blocks"] == blocks
        assert second.content == "done"
        assert len(requests) == 2
        assert requests[1]["messages"][1]["thinking_blocks"] == blocks
        assert requests[1]["messages"][2]["tool_call_id"] == "call_1"
        assert all("temperature" not in request for request in requests)

    def test_gpt6_tool_call_posts_to_proxy_responses_endpoint(self, monkeypatch) -> None:
        requests: list[tuple[str, dict]] = []

        class ProxyHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, payload))
                response = json.dumps(
                    {
                        "id": "resp_test",
                        "object": "response",
                        "created_at": 1,
                        "status": "completed",
                        "model": payload["model"],
                        "output": [
                            {
                                "type": "function_call",
                                "id": "fc_1",
                                "call_id": "call_1",
                                "name": "ping",
                                "arguments": '{"value":"ok"}',
                                "status": "completed",
                            }
                        ],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format: str, *args: object) -> None:
                pass

        monkeypatch.setenv("DECEPTICON_LLM_DISABLE_STREAMING", "true")
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            factory = LLMFactory(
                ProxyConfig(url=f"http://127.0.0.1:{server.server_port}/v1", api_key="test-key")
            )
            model = factory._create_chat_model("openai/gpt-6.1-sol", 0.2)
            reply = model.bind_tools(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "ping",
                            "description": "Test tool",
                            "parameters": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                            },
                        },
                    }
                ]
            ).invoke([HumanMessage(content="ping")])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        assert len(requests) == 1
        path, payload = requests[0]
        assert path == "/v1/responses"
        assert payload["model"] == "openai/gpt-6.1-sol"
        assert payload["tools"][0]["name"] == "ping"
        assert "temperature" not in payload
        assert reply.tool_calls == [
            {"name": "ping", "args": {"value": "ok"}, "id": "call_1", "type": "tool_call"}
        ]

    def test_factory_initializes(self):
        assert self.factory.proxy_url == "http://localhost:4000"

    def test_get_model_returns_chat_model(self):
        model = self.factory.get_model("recon")
        assert model is not None
        assert model.model_name == "anthropic/claude-haiku-4-5"

    def test_get_model_caches_instances(self):
        m1 = self.factory.get_model("recon")
        m2 = self.factory.get_model("recon")
        assert m1 is m2

    def test_get_model_different_roles_different_models(self):
        recon = self.factory.get_model("recon")
        decepticon = self.factory.get_model("decepticon")
        assert recon is not decepticon
        assert recon.model_name != decepticon.model_name

    def test_get_model_unknown_role_raises(self):
        with pytest.raises(KeyError, match="No model assignment"):
            self.factory.get_model("nonexistent")

    def test_router_accessible(self):
        assert self.factory.router is not None

    def test_get_fallback_models_full_chain(self):
        models = self.factory.get_fallback_models("recon")
        names = [m.model_name for m in models]
        assert names == [
            "openai/gpt-5-nano",
            "gemini/gemini-2.5-flash-lite",
            "deepseek/deepseek-v4-flash",
            "openrouter/anthropic/claude-haiku-4-5",
            "nvidia_nim/meta/llama-3.2-3b-instruct",
        ]

    def test_get_fallback_models_high_tier_includes_all_methods(self):
        models = self.factory.get_fallback_models("decepticon")
        names = [m.model_name for m in models]
        assert names == [
            "openai/gpt-5.5",
            "gemini/gemini-2.5-pro",
            "minimax/MiniMax-M3",
            "deepseek/deepseek-v4-pro",
            "xai/grok-4.3",
            "mistral/mistral-large-latest",
            "openrouter/anthropic/claude-opus-4-8",
            "nvidia_nim/meta/llama-3.3-70b-instruct",
        ]

    def test_get_fallback_models_without_fallback(self):
        # Single-credential mapping → no fallback.
        creds = Credentials(methods=[AuthMethod.OPENAI_API])
        mapping = LLMModelMapping.from_credentials_and_profile(creds, ModelProfile.ECO)
        factory = LLMFactory(self.proxy, mapping)
        assert factory.get_fallback_models("recon") == []

    def test_explicit_credentials_param(self):
        # Constructor accepts a Credentials object instead of a full mapping.
        creds = Credentials(methods=[AuthMethod.OPENAI_API])
        factory = LLMFactory(self.proxy, credentials=creds, profile=ModelProfile.ECO)
        assert factory.get_model("decepticon").model_name == "openai/gpt-5.5"


class TestLLMFactoryEmptyChainValidation:
    """A credentials inventory whose AuthMethods lack a (method, tier) entry
    for some required tier resolves to an EMPTY model chain at that tier.
    ``LLMModelMapping.from_credentials_and_profile`` silently skips such
    roles, so the failure surfaces much later — either as a confusing
    ``KeyError: No model assignment for role: ...`` at ``get_model`` time
    or, when a caller routes through middleware, as a primary-less
    ``ModelFallbackMiddleware([])``. Validate at construction so the
    operator gets one actionable error naming the empty tier(s) and the
    configured credentials inventory."""

    def setup_method(self) -> None:
        self.proxy = ProxyConfig(url="http://localhost:4000", api_key="test-key")

    def test_empty_tier_raises_at_init_with_inventory_listed(self) -> None:
        # MINIMAX_API has only HIGH+MID entries in METHOD_MODELS — no LOW.
        # ECO routes ``recon`` (and other LOW roles) to Tier.LOW, so the
        # LOW chain resolves to []. The factory must refuse to construct.
        creds = Credentials(methods=[AuthMethod.MINIMAX_API])
        with pytest.raises(ValueError) as exc_info:
            LLMFactory(self.proxy, credentials=creds, profile=ModelProfile.ECO)
        msg = str(exc_info.value)
        # Names the empty tier so the operator knows what to fix.
        assert "low" in msg.lower()
        # Echoes the configured credentials so the operator can audit
        # what was actually detected (vs. what they think they set).
        assert AuthMethod.MINIMAX_API.value in msg

    def test_test_profile_empty_high_chain_raises(self) -> None:
        # MINIMAX_API covers HIGH+MID but not LOW; TEST profile forces every
        # role to LOW → empty chain across the board.
        creds = Credentials(methods=[AuthMethod.MINIMAX_API])
        with pytest.raises(ValueError, match="(?i)low"):
            LLMFactory(self.proxy, credentials=creds, profile=ModelProfile.TEST)

    def test_valid_inventory_still_constructs(self) -> None:
        # OPENAI_API covers all three tiers — no empty chain, no regression.
        creds = Credentials(methods=[AuthMethod.OPENAI_API])
        factory = LLMFactory(self.proxy, credentials=creds, profile=ModelProfile.ECO)
        assert factory.get_model("recon").model_name == "openai/gpt-5-nano"
        assert factory.get_model("decepticon").model_name == "openai/gpt-5.5"

    def test_explicit_mapping_bypasses_validation(self) -> None:
        # When the caller supplies a mapping directly, they own its shape —
        # the credentials-based validation does not apply (existing tests
        # build empty/partial mappings explicitly).
        factory = LLMFactory(self.proxy, mapping=LLMModelMapping())
        assert factory.proxy_url == "http://localhost:4000"


class TestLLMFactoryHealthCheck:
    def test_health_check_returns_false_when_no_proxy(self):
        proxy = ProxyConfig(url="http://localhost:19999")
        factory = LLMFactory(proxy, mapping=LLMModelMapping())
        assert asyncio.run(factory.health_check()) is False


class TestResolveCredentials:
    def test_real_keys_only(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-realtokenfortestingauthrouting12345")
        monkeypatch.setenv("OPENAI_API_KEY", "your-openai-key-here")  # placeholder
        for k in (
            "GEMINI_API_KEY",
            "MINIMAX_API_KEY",
            "DEEPSEEK_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "OLLAMA_API_BASE",
            "OLLAMA_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("DECEPTICON_AUTH_PRIORITY", raising=False)
        monkeypatch.delenv("DECEPTICON_AUTH_CLAUDE_CODE", raising=False)
        for flag in (
            "DECEPTICON_AUTH_CHATGPT",
            "DECEPTICON_AUTH_COPILOT",
            "DECEPTICON_AUTH_GEMINI",
            "DECEPTICON_AUTH_GROK",
            "DECEPTICON_AUTH_PERPLEXITY",
        ):
            monkeypatch.delenv(flag, raising=False)
        creds = _resolve_credentials()
        assert creds.methods == [AuthMethod.ANTHROPIC_API]

    def test_oauth_only(self, monkeypatch, tmp_path):
        # OAuth detection requires the credential FILE alongside the
        # boolean — point Claude Code at a temp credentials file so the
        # test runs deterministically regardless of host state.
        cred_file = tmp_path / "credentials.json"
        cred_file.write_text('{"claudeAiOauth": {"accessToken": "sk-ant-oat01-deadbeefdeadbeef"}}')
        monkeypatch.setenv("CLAUDE_CODE_CREDENTIALS_PATH", str(cred_file))
        for k in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "MINIMAX_API_KEY",
            "DEEPSEEK_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "OLLAMA_API_BASE",
            "OLLAMA_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("DECEPTICON_AUTH_CLAUDE_CODE", "true")
        monkeypatch.delenv("DECEPTICON_AUTH_PRIORITY", raising=False)
        for flag in (
            "DECEPTICON_AUTH_CHATGPT",
            "DECEPTICON_AUTH_COPILOT",
            "DECEPTICON_AUTH_GEMINI",
            "DECEPTICON_AUTH_GROK",
            "DECEPTICON_AUTH_PERPLEXITY",
        ):
            monkeypatch.delenv(flag, raising=False)
        creds = _resolve_credentials()
        assert creds.methods == [AuthMethod.ANTHROPIC_OAUTH]

    def test_oauth_flag_without_credential_file_is_dropped(self, monkeypatch, tmp_path):
        """Stale ``DECEPTICON_AUTH_CLAUDE_CODE=true`` after ``codex logout``
        (or after the user deleted ``~/.claude/.credentials.json``) must
        not place the OAuth method into the chain — otherwise every
        request 401s before falling back to the next provider.
        """
        # Point both the primary and legacy fallback paths at tmp_path so
        # any ``~/.claude/.credentials.json`` or
        # ``~/.config/anthropic/q/tokens.json`` on the dev host doesn't
        # accidentally satisfy the file-presence check.
        monkeypatch.setenv("HOME", str(tmp_path))
        missing = tmp_path / "missing.json"  # never created
        monkeypatch.setenv("CLAUDE_CODE_CREDENTIALS_PATH", str(missing))
        monkeypatch.setenv("DECEPTICON_AUTH_CLAUDE_CODE", "true")
        for k in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "MINIMAX_API_KEY",
            "OLLAMA_API_BASE",
            "OLLAMA_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        for flag in (
            "DECEPTICON_AUTH_CHATGPT",
            "DECEPTICON_AUTH_COPILOT",
            "DECEPTICON_AUTH_GEMINI",
            "DECEPTICON_AUTH_GROK",
            "DECEPTICON_AUTH_PERPLEXITY",
        ):
            monkeypatch.delenv(flag, raising=False)
        monkeypatch.delenv("DECEPTICON_AUTH_PRIORITY", raising=False)
        creds = _resolve_credentials()
        assert AuthMethod.ANTHROPIC_OAUTH not in creds.methods

    def test_oauth_plus_api_priority_default(self, monkeypatch, tmp_path):
        # Default priority is anthropic_oauth > anthropic_api > openai_api ...
        cred_file = tmp_path / "credentials.json"
        cred_file.write_text('{"claudeAiOauth": {"accessToken": "sk-ant-oat01-deadbeefdeadbeef"}}')
        monkeypatch.setenv("CLAUDE_CODE_CREDENTIALS_PATH", str(cred_file))
        monkeypatch.setenv("DECEPTICON_AUTH_CLAUDE_CODE", "true")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-realtokenfortestingauthrouting12345")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-realopenaitokenfortestingauthrouting12345")
        for k in (
            "GEMINI_API_KEY",
            "MINIMAX_API_KEY",
            "DEEPSEEK_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "OLLAMA_API_BASE",
            "OLLAMA_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("DECEPTICON_AUTH_PRIORITY", raising=False)
        for flag in (
            "DECEPTICON_AUTH_CHATGPT",
            "DECEPTICON_AUTH_COPILOT",
            "DECEPTICON_AUTH_GEMINI",
            "DECEPTICON_AUTH_GROK",
            "DECEPTICON_AUTH_PERPLEXITY",
        ):
            monkeypatch.delenv(flag, raising=False)
        creds = _resolve_credentials()
        assert creds.methods == [
            AuthMethod.ANTHROPIC_OAUTH,
            AuthMethod.ANTHROPIC_API,
            AuthMethod.OPENAI_API,
        ]

    def test_explicit_priority_override(self, monkeypatch):
        monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "openai_api,anthropic_api")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-realtokenfortestingauthrouting12345")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-realopenaitokenfortestingauthrouting12345")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.delenv("DECEPTICON_AUTH_CLAUDE_CODE", raising=False)
        creds = _resolve_credentials()
        assert creds.methods == [AuthMethod.OPENAI_API, AuthMethod.ANTHROPIC_API]

    def test_placeholder_falls_back_to_all_api_methods(self, monkeypatch):
        """When every detected method is a placeholder/missing, the resolver
        falls back to the all-API-methods inventory so module-level agent
        constructors stay importable in CI / dev shells without keys."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "your-anthropic-key-here")
        for k in (
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "MINIMAX_API_KEY",
            "DEEPSEEK_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "OLLAMA_API_BASE",
            "OLLAMA_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("DECEPTICON_AUTH_PRIORITY", raising=False)
        monkeypatch.delenv("DECEPTICON_AUTH_CLAUDE_CODE", raising=False)
        creds = _resolve_credentials()
        assert creds.methods == [
            AuthMethod.ANTHROPIC_API,
            AuthMethod.OPENAI_API,
            AuthMethod.GOOGLE_API,
            AuthMethod.MINIMAX_API,
            AuthMethod.DEEPSEEK_API,
            AuthMethod.XAI_API,
            AuthMethod.MISTRAL_API,
            AuthMethod.OPENROUTER_API,
            AuthMethod.NVIDIA_API,
        ]

    def test_explicit_priority_no_creds_logs_error(self, monkeypatch, tmp_path, caplog):
        """An explicit ``DECEPTICON_AUTH_PRIORITY`` whose every listed
        method fails detection (typical of a broken OAuth credentials
        mount — e.g. ``CLAUDE_CREDENTIALS_VOLUME`` unset and the
        compose file bound ``/dev/null`` into the container) must
        surface the root cause at ERROR level.

        Otherwise the silent fallback to ``all_api_methods()`` runs
        through nine unrelated providers and the operator only sees a
        downstream 401-cascade — confusingly masked as a 429 once the
        routed-to provider (e.g. NVIDIA NIM) cools down. Backward
        compat is preserved: the resolver still returns
        ``all_api_methods()`` so module imports remain green; the
        real model call surfaces a separate, actionable error via
        ``_reraise_with_actionable_message``.
        """
        # Point the OAuth credential path at a file we never create so
        # detection fails the same way ``/dev/null`` does at runtime.
        missing = tmp_path / "missing.json"
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CODE_CREDENTIALS_PATH", str(missing))
        monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "anthropic_oauth")
        monkeypatch.setenv("DECEPTICON_AUTH_CLAUDE_CODE", "true")
        for k in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "MINIMAX_API_KEY",
            "DEEPSEEK_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "OLLAMA_API_BASE",
            "OLLAMA_MODEL",
            "OLLAMA_CLOUD_API_BASE",
            "OLLAMA_CLOUD_MODEL",
            "LMSTUDIO_API_BASE",
            "LMSTUDIO_MODEL",
            "CUSTOM_OPENAI_API_BASE",
            "CUSTOM_OPENAI_API_KEY",
        ):
            monkeypatch.delenv(k, raising=False)
        for flag in (
            "DECEPTICON_AUTH_CHATGPT",
            "DECEPTICON_AUTH_COPILOT",
            "DECEPTICON_AUTH_GEMINI",
            "DECEPTICON_AUTH_GROK",
            "DECEPTICON_AUTH_PERPLEXITY",
        ):
            monkeypatch.delenv(flag, raising=False)

        # ``decepticon_core.utils.logging`` sets the parent decepticon logger
        # to ``propagate=False`` so library output doesn't double up in
        # apps that own their root handler. caplog hooks the root logger,
        # so without re-enabling propagation our ERROR record never
        # reaches pytest's capture buffer. Re-enable for the duration of
        # the call and let monkeypatch restore on teardown.
        decepticon_log = logging.getLogger("decepticon")
        monkeypatch.setattr(decepticon_log, "propagate", True)

        with caplog.at_level(logging.ERROR):
            creds = _resolve_credentials()

        # Backward compat: still returns the all-API-methods fallback.
        assert creds.methods == Credentials.all_api_methods().methods

        # ERROR diagnostic must mention the env var name so an operator
        # scanning ``decepticon logs langgraph`` knows where to look.
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert any("DECEPTICON_AUTH_PRIORITY" in r.getMessage() for r in error_records), (
            f"No ERROR log mentioned DECEPTICON_AUTH_PRIORITY. "
            f"Got: {[r.getMessage() for r in error_records]}"
        )

    def test_implicit_priority_no_creds_stays_info_level(self, monkeypatch, caplog):
        """Implicit priority (no DECEPTICON_AUTH_PRIORITY set) with no
        detectable credentials is the import-friendly CI/test path:
        we keep the existing INFO log and ``all_api_methods()`` return
        so module imports work without keys present. Only **explicit**
        priority with no matches gets escalated to ERROR.
        """
        for k in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "MINIMAX_API_KEY",
            "DEEPSEEK_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "OLLAMA_API_BASE",
            "OLLAMA_MODEL",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("DECEPTICON_AUTH_PRIORITY", raising=False)
        monkeypatch.delenv("DECEPTICON_AUTH_CLAUDE_CODE", raising=False)

        # Match the explicit-priority test's pattern so caplog sees the
        # records — the decepticon parent logger defaults to
        # ``propagate=False`` per ``decepticon_core.utils.logging``.
        decepticon_log = logging.getLogger("decepticon")
        monkeypatch.setattr(decepticon_log, "propagate", True)

        with caplog.at_level(logging.DEBUG):
            creds = _resolve_credentials()

        assert creds.methods == Credentials.all_api_methods().methods
        # No ERROR records — this case stays informational.
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert not error_records, (
            f"Unexpected ERROR logs in implicit-priority path: "
            f"{[r.getMessage() for r in error_records]}"
        )

    def test_ollama_local_only_returns_ollama_chain(self, monkeypatch):
        """Issue #106: a user with only OLLAMA_API_BASE / OLLAMA_MODEL set
        (no API keys, no OAuth) must get a chain of one — Ollama only.
        Falling back to all-API-methods would produce 401 errors on every
        provider the user doesn't have."""
        for k in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "MINIMAX_API_KEY",
            "DEEPSEEK_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "DECEPTICON_AUTH_PRIORITY",
            "DECEPTICON_AUTH_CLAUDE_CODE",
            "DECEPTICON_AUTH_CHATGPT",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen3-coder:30b")
        creds = _resolve_credentials()
        assert creds.methods == [AuthMethod.OLLAMA_LOCAL]

    def test_explicit_priority_with_ollama_local(self, monkeypatch):
        """User opts into Ollama via explicit priority — the resolver
        recognizes it as configured when OLLAMA_API_BASE is set."""
        monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "ollama_local,anthropic_api")
        monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen3-coder:30b")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-realtokenfortestingauthrouting12345")
        for k in (
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "MINIMAX_API_KEY",
            "DEEPSEEK_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "DECEPTICON_AUTH_CLAUDE_CODE",
            "DECEPTICON_AUTH_CHATGPT",
        ):
            monkeypatch.delenv(k, raising=False)
        creds = _resolve_credentials()
        assert creds.methods == [AuthMethod.OLLAMA_LOCAL, AuthMethod.ANTHROPIC_API]


# ── Temperature handling (issue #107) ───────────────────────────────────


class TestTemperatureDrop:
    """Claude Opus 4.7 rejects ``temperature`` regardless of route. The
    factory must drop it on every Opus 4 surface (anthropic/, auth/,
    openrouter/anthropic/) and keep it for everyone else."""

    def setup_method(self):
        from decepticon.llm.factory import _model_drops_temperature

        self._drops = _model_drops_temperature

    def test_anthropic_opus_drops_temperature(self):
        assert self._drops("anthropic/claude-opus-4-7") is True

    def test_oauth_opus_drops_temperature(self):
        assert self._drops("auth/claude-opus-4-7") is True

    def test_openrouter_opus_drops_temperature(self):
        assert self._drops("openrouter/anthropic/claude-opus-4-7") is True

    def test_sonnet_keeps_temperature(self):
        assert self._drops("anthropic/claude-sonnet-4-6") is False

    def test_haiku_keeps_temperature(self):
        assert self._drops("anthropic/claude-haiku-4-5") is False

    def test_openai_keeps_temperature(self):
        assert self._drops("openai/gpt-5.5") is False

    def test_current_claude_models_drop_temperature(self):
        assert self._drops("anthropic/claude-fable-5-1") is True
        assert self._drops("anthropic/claude-opus-5-5") is True
        assert self._drops("anthropic/claude-sonnet-5-5") is True

    def test_ollama_keeps_temperature(self):
        assert self._drops("ollama_chat/qwen3-coder:30b") is False

    # Kimi for Coding (api.kimi.com/coding) rejects temperature != 1 on
    # every model. Verified against all four ids, 2026-07.

    def test_kimi_k3_drops_temperature(self):
        assert self._drops("kimi/k3") is True

    def test_kimi_k3_256k_drops_temperature(self):
        assert self._drops("kimi/k3-256k") is True

    def test_kimi_for_coding_drops_temperature(self):
        assert self._drops("kimi/kimi-for-coding") is True

    def test_kimi_for_coding_highspeed_drops_temperature(self):
        assert self._drops("kimi/kimi-for-coding-highspeed") is True

    def test_kimi_via_custom_prefix_drops_temperature(self):
        # Operators routing api.kimi.com through custom/ still get the drop.
        assert self._drops("custom/kimi-for-coding") is True


# ── Actionable error translation (issue #107 + community feedback) ──────


class TestActionableErrorTranslation:
    """The OSS user complaint: every upstream failure surfaces as 'An
    internal error occurred', stripping the message that would tell them
    what to fix. Each branch below verifies one class of error gets
    rewritten with a remediation hint and the model id that hit the
    failure."""

    def setup_method(self):
        from decepticon.llm.factory import _reraise_with_actionable_message

        self._translate = _reraise_with_actionable_message

    def test_no_fallback_model_group_branch(self):
        exc = Exception(
            "litellm.BadRequestError: ... No fallback model group found "
            "for original model_group=anthropic/claude-opus-4-7."
        )
        with pytest.raises(RuntimeError) as info:
            self._translate(exc, "anthropic/claude-opus-4-7")
        msg = str(info.value)
        assert "no provider fallback" in msg
        assert "anthropic/claude-opus-4-7" in msg
        assert "DECEPTICON_AUTH_PRIORITY" in msg

    def test_400_bad_request_branch(self):
        # openai.BadRequestError carries 'Error code: 400' in repr.
        exc = Exception("Error code: 400 - {'error': {'message': 'temperature is deprecated'}}")
        type(exc).__name__  # noqa: B018 — sanity, ensures Exception default name
        with pytest.raises(RuntimeError) as info:
            self._translate(exc, "anthropic/claude-opus-4-7")
        assert "rejected the request (400)" in str(info.value)

    def test_401_authentication_branch(self):
        exc = type("AuthenticationError", (Exception,), {})("Error code: 401 - invalid_api_key")
        with pytest.raises(RuntimeError) as info:
            self._translate(exc, "openai/gpt-5.5")
        msg = str(info.value)
        assert "credentials (401)" in msg
        assert "decepticon onboard --reset" in msg

    def test_429_ratelimit_branch(self):
        exc = type("RateLimitError", (Exception,), {})("Error code: 429")
        with pytest.raises(RuntimeError) as info:
            self._translate(exc, "anthropic/claude-opus-4-7")
        msg = str(info.value)
        assert "rate limit (429)" in msg
        assert "DECEPTICON_AUTH_PRIORITY" in msg

    def test_404_notfound_with_ollama_hint(self):
        exc = type("NotFoundError", (Exception,), {})("Error code: 404 - model not found")
        with pytest.raises(RuntimeError) as info:
            self._translate(exc, "ollama_chat/nonexistent")
        msg = str(info.value)
        assert "404" in msg
        assert "OLLAMA_MODEL" in msg

    def test_unmatched_error_passes_through(self):
        # Anything we don't recognize must NOT raise — the caller's
        # ``raise`` follows and re-raises the original exception with
        # full traceback.
        exc = ValueError("something completely unrelated")
        # Should not raise from the helper.
        self._translate(exc, "anthropic/claude-opus-4-7")

    def test_401_redacts_bearer_token_keeps_guidance(self):
        exc = type("AuthenticationError", (Exception,), {})(
            "Error code: 401 - {'error': 'invalid_api_key'} "
            "Authorization: Bearer sk-ant-SECRET123FAKEPLACEHOLDERTOKEN"
        )
        with pytest.raises(RuntimeError) as info:
            self._translate(exc, "anthropic/claude-opus-4-7")
        msg = str(info.value)
        assert "sk-ant-SECRET123FAKEPLACEHOLDERTOKEN" not in msg
        assert "SECRET123FAKEPLACEHOLDERTOKEN" not in msg
        assert "[REDACTED]" in msg
        assert "credentials (401)" in msg
        assert "anthropic/claude-opus-4-7" in msg

    def test_400_redacts_api_key_kwarg_keeps_guidance(self):
        exc = Exception(
            "Error code: 400 - bad request from provider with api_key=sk-SECRETfakeplaceholdervalue99"
        )
        with pytest.raises(RuntimeError) as info:
            self._translate(exc, "openai/gpt-5.5")
        msg = str(info.value)
        assert "sk-SECRETfakeplaceholdervalue99" not in msg
        assert "SECRETfakeplaceholdervalue99" not in msg
        assert "[REDACTED]" in msg
        assert "rejected the request (400)" in msg

    def test_redact_secrets_preserves_nonsecret_text(self):
        from decepticon.llm.factory import _redact_secrets

        text = "No fallback model group found for model_group=anthropic/claude-opus-4-7."
        assert _redact_secrets(text) == text


# ── DeepSeek V4 Pro reasoning_content passthrough ────────────────────────


class TestDeepSeekReasoningContent:
    """Verify reasoning_content survives both the non-streaming and streaming
    paths so it can be sent back on subsequent API turns."""

    def test_create_chat_result_extracts_reasoning_content(self):
        """Non-streaming: _create_chat_result captures reasoning_content
        from the response dict's choice message."""
        from unittest.mock import patch

        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        # Simulate the OpenAI response dict with reasoning_content
        response_dict = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Four",
                        "reasoning_content": "I think therefore I am",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {},
        }

        # Mock the parent's _create_chat_result to return a ChatResult without reasoning_content
        msg = AIMessage(content="Four", additional_kwargs={})
        parent_result = ChatResult(generations=[ChatGeneration(message=msg)])

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_create_chat_result",
            return_value=parent_result,
        ):
            instance = object.__new__(_DeepSeekThinkingChatOpenAI)
            result = instance._create_chat_result(response_dict)

        assert (
            result.generations[0].message.additional_kwargs["reasoning_content"]
            == "I think therefore I am"
        )

    def test_create_chat_result_skips_when_absent(self):
        """Non-streaming: no crash when reasoning_content is missing."""
        from unittest.mock import patch

        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        response_dict = {
            "choices": [
                {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
            ],
            "usage": {},
        }

        msg = AIMessage(content="hi", additional_kwargs={})
        parent_result = ChatResult(generations=[ChatGeneration(message=msg)])

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_create_chat_result",
            return_value=parent_result,
        ):
            instance = object.__new__(_DeepSeekThinkingChatOpenAI)
            result = instance._create_chat_result(response_dict)

        assert "reasoning_content" not in result.generations[0].message.additional_kwargs

    def test_convert_chunk_injects_reasoning_content(self):
        """Streaming: _convert_chunk_to_generation_chunk captures
        reasoning_content from the raw delta and injects it into
        AIMessageChunk.additional_kwargs."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        # Build a fake raw SSE chunk dict with reasoning_content in the delta
        raw_chunk = {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "Let me think...",
                    },
                    "finish_reason": None,
                }
            ],
        }

        # The parent's _convert_chunk_to_generation_chunk builds the
        # ChatGenerationChunk but ignores reasoning_content. We mock it
        # to return a chunk without reasoning_content, then verify our
        # override injects it.
        base_msg = AIMessageChunk(content="", additional_kwargs={})
        base_chunk = ChatGenerationChunk(message=base_msg)

        instance = MagicMock(spec=_DeepSeekThinkingChatOpenAI)
        # Bind the real method to our mock instance
        method = _DeepSeekThinkingChatOpenAI._convert_chunk_to_generation_chunk

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_convert_chunk_to_generation_chunk",
            return_value=base_chunk,
        ):
            result = method(instance, raw_chunk, AIMessageChunk, None)

        assert result is not None
        assert result.message.additional_kwargs["reasoning_content"] == "Let me think..."

    def test_convert_chunk_no_reasoning_leaves_kwargs_clean(self):
        """Streaming: when delta has no reasoning_content, additional_kwargs
        is not polluted."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        raw_chunk = {
            "choices": [
                {
                    "delta": {"role": "assistant", "content": "hi"},
                    "finish_reason": None,
                }
            ],
        }

        base_msg = AIMessageChunk(content="hi", additional_kwargs={})
        base_chunk = ChatGenerationChunk(message=base_msg)

        instance = MagicMock(spec=_DeepSeekThinkingChatOpenAI)
        method = _DeepSeekThinkingChatOpenAI._convert_chunk_to_generation_chunk

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_convert_chunk_to_generation_chunk",
            return_value=base_chunk,
        ):
            result = method(instance, raw_chunk, AIMessageChunk, None)

        assert result is not None
        assert "reasoning_content" not in result.message.additional_kwargs

    def test_reasoning_content_accumulates_across_chunks(self):
        """Streaming: reasoning_content from multiple chunks concatenates
        via AIMessageChunk.__add__ (merge_dicts)."""
        from langchain_core.messages import AIMessageChunk

        c1 = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "Let me "})
        c2 = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "think..."})
        c3 = AIMessageChunk(content="Hello!", additional_kwargs={})

        merged = c1 + c2 + c3
        assert merged.additional_kwargs["reasoning_content"] == "Let me think..."
        assert merged.content == "Hello!"

    def test_get_request_payload_injects_reasoning_content(self):
        """Outbound: _get_request_payload injects reasoning_content from
        AIMessage.additional_kwargs into serialized assistant message dicts."""
        from unittest.mock import patch

        from langchain_core.messages import AIMessage, HumanMessage

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        messages = [
            HumanMessage(content="hi"),
            AIMessage(
                content="hello",
                additional_kwargs={"reasoning_content": "thinking..."},
            ),
        ]

        # Mock super()._get_request_payload to return a payload without reasoning_content
        mock_payload = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            "model": "deepseek/deepseek-v4-pro",
        }

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_get_request_payload",
            return_value=mock_payload,
        ):
            instance = object.__new__(_DeepSeekThinkingChatOpenAI)
            payload = instance._get_request_payload(messages)

        assert payload["messages"][1]["reasoning_content"] == "thinking..."
        assert "reasoning_content" not in payload["messages"][0]
        assert payload["extra_body"]["thinking"] == {"type": "enabled"}
        assert payload["reasoning_effort"] == "high"

    def test_convert_chunk_empty_reasoning_content_ignored(self):
        """Streaming: empty string reasoning_content in delta is ignored."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        raw_chunk = {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "hi",
                        "reasoning_content": "",
                    },
                    "finish_reason": None,
                }
            ],
        }

        base_msg = AIMessageChunk(content="hi", additional_kwargs={})
        base_chunk = ChatGenerationChunk(message=base_msg)
        instance = MagicMock(spec=_DeepSeekThinkingChatOpenAI)
        method = _DeepSeekThinkingChatOpenAI._convert_chunk_to_generation_chunk

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_convert_chunk_to_generation_chunk",
            return_value=base_chunk,
        ):
            result = method(instance, raw_chunk, AIMessageChunk, None)

        # Empty string is falsy — should not pollute additional_kwargs
        assert "reasoning_content" not in result.message.additional_kwargs

    def test_convert_chunk_none_delta_no_crash(self):
        """Streaming: None delta in choices does not crash."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        raw_chunk = {
            "choices": [
                {
                    "delta": None,
                    "finish_reason": "stop",
                }
            ],
        }

        base_msg = AIMessageChunk(content="", additional_kwargs={})
        base_chunk = ChatGenerationChunk(message=base_msg)
        instance = MagicMock(spec=_DeepSeekThinkingChatOpenAI)
        method = _DeepSeekThinkingChatOpenAI._convert_chunk_to_generation_chunk

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_convert_chunk_to_generation_chunk",
            return_value=base_chunk,
        ):
            result = method(instance, raw_chunk, AIMessageChunk, None)

        assert result is not None
        assert "reasoning_content" not in result.message.additional_kwargs

    def test_convert_chunk_empty_choices_no_crash(self):
        """Streaming: empty choices array does not crash."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        raw_chunk = {"choices": []}

        base_msg = AIMessageChunk(content="", additional_kwargs={})
        base_chunk = ChatGenerationChunk(message=base_msg)
        instance = MagicMock(spec=_DeepSeekThinkingChatOpenAI)
        method = _DeepSeekThinkingChatOpenAI._convert_chunk_to_generation_chunk

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_convert_chunk_to_generation_chunk",
            return_value=base_chunk,
        ):
            result = method(instance, raw_chunk, AIMessageChunk, None)

        assert result is not None
        assert "reasoning_content" not in result.message.additional_kwargs

    def test_convert_chunk_parent_returns_none(self):
        """Streaming: when parent returns None, we return None too."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessageChunk

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        raw_chunk = {"type": "content.delta"}  # parent returns None for these
        instance = MagicMock(spec=_DeepSeekThinkingChatOpenAI)
        method = _DeepSeekThinkingChatOpenAI._convert_chunk_to_generation_chunk

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_convert_chunk_to_generation_chunk",
            return_value=None,
        ):
            result = method(instance, raw_chunk, AIMessageChunk, None)

        assert result is None

    def test_convert_chunk_beta_stream_format(self):
        """Streaming: handles beta.chat.completions.stream nested chunk format."""
        from unittest.mock import MagicMock, patch

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        # Some LangChain versions nest under "chunk" key
        raw_chunk = {
            "chunk": {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "Thinking via beta format...",
                        },
                        "finish_reason": None,
                    }
                ],
            },
        }

        base_msg = AIMessageChunk(content="", additional_kwargs={})
        base_chunk = ChatGenerationChunk(message=base_msg)
        instance = MagicMock(spec=_DeepSeekThinkingChatOpenAI)
        method = _DeepSeekThinkingChatOpenAI._convert_chunk_to_generation_chunk

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_convert_chunk_to_generation_chunk",
            return_value=base_chunk,
        ):
            result = method(instance, raw_chunk, AIMessageChunk, None)

        assert (
            result.message.additional_kwargs["reasoning_content"] == "Thinking via beta format..."
        )

    def test_get_request_payload_multiple_assistant_messages(self):
        """Outbound: each assistant message gets its own reasoning_content."""
        from unittest.mock import patch

        from langchain_core.messages import AIMessage, HumanMessage

        from decepticon.llm.factory import _DeepSeekThinkingChatOpenAI

        messages = [
            HumanMessage(content="q1"),
            AIMessage(content="a1", additional_kwargs={"reasoning_content": "thought1"}),
            HumanMessage(content="q2"),
            AIMessage(content="a2", additional_kwargs={"reasoning_content": "thought2"}),
        ]

        mock_payload = {
            "messages": [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
                {"role": "assistant", "content": "a2"},
            ],
            "model": "deepseek/deepseek-v4-pro",
        }

        with patch.object(
            _DeepSeekThinkingChatOpenAI.__bases__[0],
            "_get_request_payload",
            return_value=mock_payload,
        ):
            instance = object.__new__(_DeepSeekThinkingChatOpenAI)
            payload = instance._get_request_payload(messages)

        assert payload["messages"][1]["reasoning_content"] == "thought1"
        assert payload["messages"][3]["reasoning_content"] == "thought2"
        assert "reasoning_content" not in payload["messages"][0]
        assert "reasoning_content" not in payload["messages"][2]

    def test_model_detection(self):
        """Factory routes DeepSeek V4 (pro + flash) and legacy reasoner.

        Per DeepSeek's API docs ``deepseek-reasoner`` is the deprecated
        alias for ``deepseek-v4-flash`` thinking mode, so v4-flash also
        returns ``reasoning_content`` and the API rejects subsequent
        tool turns when the field is omitted. Closes #201, #220.
        """
        from decepticon.llm.factory import _model_is_deepseek_thinking

        assert _model_is_deepseek_thinking("deepseek/deepseek-v4-pro") is True
        assert _model_is_deepseek_thinking("deepseek/deepseek-v4-flash") is True
        assert _model_is_deepseek_thinking("deepseek/deepseek-reasoner") is True
        assert _model_is_deepseek_thinking("deepseek/deepseek-chat") is False
        assert _model_is_deepseek_thinking("openai/gpt-5.5") is False


# ── LLAMACPP_LOCAL credential detection (issue #151) ────────────────────


_LLAMACPP_RELATED_ENV = (
    "LLAMACPP_API_BASE",
    "LLAMACPP_MODEL",
    "LLAMACPP_API_KEY",
)


def _scrub_other_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete any env that would be detected as another credential.

    The credential-detection tests below need to assert "only LLAMACPP
    is detected"; without this scrub, a developer's exported
    ``ANTHROPIC_API_KEY`` would creep in and the assertion would fail
    locally only.
    """
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "MINIMAX_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "MISTRAL_API_KEY",
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "FIREWORKS_API_KEY",
        "COHERE_API_KEY",
        "MOONSHOT_API_KEY",
        "ZAI_API_KEY",
        "DASHSCOPE_API_KEY",
        "GITHUB_API_KEY",
        "OLLAMA_API_BASE",
        "OLLAMA_MODEL",
        "OLLAMA_CLOUD_API_BASE",
        "OLLAMA_CLOUD_MODEL",
        "LMSTUDIO_API_BASE",
        "LMSTUDIO_MODEL",
        "CUSTOM_OPENAI_API_BASE",
        "CUSTOM_OPENAI_API_KEY",
        "DECEPTICON_AUTH_PRIORITY",
    ):
        monkeypatch.delenv(var, raising=False)
    for flag in (
        "DECEPTICON_AUTH_CLAUDE_CODE",
        "DECEPTICON_AUTH_CHATGPT",
        "DECEPTICON_AUTH_COPILOT",
        "DECEPTICON_AUTH_GEMINI",
        "DECEPTICON_AUTH_GROK",
        "DECEPTICON_AUTH_PERPLEXITY",
    ):
        monkeypatch.delenv(flag, raising=False)


class TestLlamacppLocalConfigured:
    """``_llamacpp_local_configured`` is the gate for adding
    ``LLAMACPP_LOCAL`` to the credentials chain. Either env var being
    set is enough — neither requires the other, mirroring the
    LM Studio / Ollama detection contract.
    """

    def test_returns_false_when_neither_env_set(self, monkeypatch):
        for var in _LLAMACPP_RELATED_ENV:
            monkeypatch.delenv(var, raising=False)
        assert _llamacpp_local_configured() is False

    def test_returns_true_when_only_base_set(self, monkeypatch):
        for var in _LLAMACPP_RELATED_ENV:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("LLAMACPP_API_BASE", "http://localhost:8080/v1")
        assert _llamacpp_local_configured() is True

    def test_returns_true_when_only_model_set(self, monkeypatch):
        for var in _LLAMACPP_RELATED_ENV:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("LLAMACPP_MODEL", "qwen2.5-coder-7b-instruct-q4_k_m")
        assert _llamacpp_local_configured() is True

    def test_whitespace_does_not_count_as_set(self, monkeypatch):
        """Stray ``LLAMACPP_API_BASE=`` lines in .env must not silently
        opt the user in — same fail-soft as the Ollama detector."""
        monkeypatch.setenv("LLAMACPP_API_BASE", "   ")
        monkeypatch.setenv("LLAMACPP_MODEL", "")
        assert _llamacpp_local_configured() is False


class TestResolveCredentialsForLlamacpp:
    """End-to-end check that ``_resolve_credentials`` picks up the
    user's llama.cpp config — both the explicit-priority path and the
    "only LLAMACPP_* detected" auto-fallback at the bottom of
    ``_resolve_credentials``.
    """

    def test_only_llamacpp_detected_returns_llamacpp_only_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _scrub_other_credentials(monkeypatch)
        monkeypatch.setenv("LLAMACPP_API_BASE", "http://localhost:8080/v1")
        monkeypatch.setenv("LLAMACPP_MODEL", "qwen2.5-coder-7b-instruct-q4_k_m")

        creds = _resolve_credentials()
        assert creds.methods == [AuthMethod.LLAMACPP_LOCAL]

    def test_priority_list_with_llamacpp_picks_it_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """User wrote an explicit DECEPTICON_AUTH_PRIORITY listing
        llamacpp_local — the resolver must respect that ordering."""
        _scrub_other_credentials(monkeypatch)
        monkeypatch.setenv("LLAMACPP_API_BASE", "http://localhost:8080/v1")
        monkeypatch.setenv("LLAMACPP_MODEL", "qwen-coder")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-realtokenfortestingauthrouting12345")
        monkeypatch.setenv("DECEPTICON_AUTH_PRIORITY", "llamacpp_local,anthropic_api")

        creds = _resolve_credentials()
        assert creds.methods == [AuthMethod.LLAMACPP_LOCAL, AuthMethod.ANTHROPIC_API]


class TestLLMTimeout:
    """Whole-coroutine LLM request timeout (DECEPTICON_LLM_TIMEOUT_SECONDS)."""

    @pytest.fixture(autouse=True)
    def _isolate_timeout_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DECEPTICON_LLM_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("DECEPTICON_LLM__TIMEOUT", raising=False)

    def test_call_with_timeout_raises_typed_exception(self) -> None:
        from decepticon.llm.factory import LLMTimeoutError, call_with_timeout

        async def slow_call() -> str:
            await asyncio.sleep(10)
            return "done"

        with pytest.raises(LLMTimeoutError, match="timed out after 0.01 seconds"):
            asyncio.run(call_with_timeout(slow_call(), 0.01))

    def test_call_with_timeout_preserves_original_timeout_as_cause(self) -> None:
        from decepticon.llm.factory import LLMTimeoutError, call_with_timeout

        async def slow_call() -> None:
            await asyncio.sleep(10)

        try:
            asyncio.run(call_with_timeout(slow_call(), 0.01))
        except LLMTimeoutError as exc:
            assert isinstance(exc.__cause__, asyncio.TimeoutError)
        else:
            raise AssertionError("expected LLMTimeoutError")

    def test_call_with_timeout_returns_successful_result(self) -> None:
        from decepticon.llm.factory import call_with_timeout

        async def fast_call() -> str:
            await asyncio.sleep(0)
            return "ok"

        assert asyncio.run(call_with_timeout(fast_call(), 1)) == "ok"

    def test_call_with_timeout_propagates_non_timeout_exceptions(self) -> None:
        from decepticon.llm.factory import LLMTimeoutError, call_with_timeout

        async def failing_call() -> None:
            raise RuntimeError("upstream 500")

        with pytest.raises(RuntimeError, match="upstream 500"):
            asyncio.run(call_with_timeout(failing_call(), 1))
        # And not as a timeout error
        try:
            asyncio.run(call_with_timeout(failing_call(), 1))
        except LLMTimeoutError:
            raise AssertionError("non-timeout exception was retyped")
        except RuntimeError:
            pass

    def test_env_override_takes_effect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from decepticon.llm.factory import _resolve_llm_timeout_seconds

        monkeypatch.setenv("DECEPTICON_LLM_TIMEOUT_SECONDS", "3")
        assert _resolve_llm_timeout_seconds() == 3.0

    def test_default_timeout_is_600_when_env_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from decepticon.llm.factory import _resolve_llm_timeout_seconds

        monkeypatch.delenv("DECEPTICON_LLM_TIMEOUT_SECONDS", raising=False)
        assert _resolve_llm_timeout_seconds() == 600.0

    def test_blank_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from decepticon.llm.factory import _resolve_llm_timeout_seconds

        monkeypatch.setenv("DECEPTICON_LLM_TIMEOUT_SECONDS", "   ")
        assert _resolve_llm_timeout_seconds() == 600.0

    def test_invalid_env_raises_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from decepticon.llm.factory import _resolve_llm_timeout_seconds

        monkeypatch.setenv("DECEPTICON_LLM_TIMEOUT_SECONDS", "not-a-number")
        with pytest.raises(ValueError):
            _resolve_llm_timeout_seconds()

    def test_non_positive_env_raises_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from decepticon.llm.factory import _resolve_llm_timeout_seconds

        monkeypatch.setenv("DECEPTICON_LLM_TIMEOUT_SECONDS", "0")
        with pytest.raises(ValueError, match="greater than 0"):
            _resolve_llm_timeout_seconds()

        monkeypatch.setenv("DECEPTICON_LLM_TIMEOUT_SECONDS", "-5")
        with pytest.raises(ValueError, match="greater than 0"):
            _resolve_llm_timeout_seconds()

    def test_pydantic_nested_alias_takes_effect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from decepticon.llm.factory import _resolve_llm_timeout_seconds

        monkeypatch.setenv("DECEPTICON_LLM__TIMEOUT", "900")
        assert _resolve_llm_timeout_seconds() == 900.0

    def test_explicit_env_wins_over_pydantic_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from decepticon.llm.factory import _resolve_llm_timeout_seconds

        monkeypatch.setenv("DECEPTICON_LLM_TIMEOUT_SECONDS", "120")
        monkeypatch.setenv("DECEPTICON_LLM__TIMEOUT", "900")
        assert _resolve_llm_timeout_seconds() == 120.0

    def test_alias_invalid_value_attributes_error_to_alias(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from decepticon.llm.factory import _resolve_llm_timeout_seconds

        monkeypatch.setenv("DECEPTICON_LLM__TIMEOUT", "nonsense")
        with pytest.raises(ValueError, match="DECEPTICON_LLM__TIMEOUT"):
            _resolve_llm_timeout_seconds()

    def test_alias_non_positive_attributes_error_to_alias(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from decepticon.llm.factory import _resolve_llm_timeout_seconds

        monkeypatch.setenv("DECEPTICON_LLM__TIMEOUT", "0")
        with pytest.raises(ValueError, match="DECEPTICON_LLM__TIMEOUT.*greater than 0"):
            _resolve_llm_timeout_seconds()

    def test_ainvoke_resolves_timeout_before_creating_request_coroutine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A misconfigured timeout env must fail loudly *without* creating
        (and leaking un-awaited) the upstream request coroutine. Regression:
        the timeout was previously resolved as the second argument to
        ``call_with_timeout``, so ``super().ainvoke(...)`` was already
        evaluated — a ValueError then left that coroutine never awaited."""
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        from decepticon.llm.factory import _ProxiedChatOpenAI

        called = False

        async def _tracking_ainvoke(self, *args, **kwargs):  # noqa: ANN001
            nonlocal called
            called = True
            return "should-not-happen"

        monkeypatch.setattr(ChatOpenAI, "ainvoke", _tracking_ainvoke)
        monkeypatch.setenv("DECEPTICON_LLM_TIMEOUT_SECONDS", "-5")

        model = _ProxiedChatOpenAI(
            model="openai/gpt-5.5",
            base_url="http://localhost:4000",
            api_key=SecretStr("test-key"),
        )
        with pytest.raises(ValueError, match="greater than 0"):
            asyncio.run(model.ainvoke("hi"))
        assert called is False, "upstream request coroutine must not be created on bad timeout"
