FROM ghcr.io/berriai/litellm:v1.88.1

# xxhash is required for CCH (Claude Code Hash) request signing.
# The v1.88.x base ships a uv-managed venv at /app/.venv (first on PATH)
# with no pip and no uv on PATH, so a bare `pip install` fails (exit 127).
# Bootstrap pip into that venv via ensurepip, then install with the venv's
# python so xxhash lands where the litellm process imports from.
RUN python -m ensurepip --upgrade && python -m pip install --no-cache-dir xxhash

COPY config/http_client.py /app/http_client.py
COPY config/oauth_token_store.py /app/oauth_token_store.py
COPY config/claude_code_handler.py /app/claude_code_handler.py
COPY config/codex_chatgpt_handler.py /app/codex_chatgpt_handler.py
COPY config/auth_handler.py /app/auth_handler.py
COPY config/gemini_handler.py /app/gemini_handler.py
COPY config/copilot_handler.py /app/copilot_handler.py
COPY config/grok_handler.py /app/grok_handler.py
COPY config/perplexity_handler.py /app/perplexity_handler.py
COPY config/litellm_dynamic_config.py /app/litellm_dynamic_config.py
COPY config/litellm_prod_catalog.json /app/litellm_prod_catalog.json
COPY config/ollama_probe.py /app/ollama_probe.py
COPY config/litellm_startup.py /app/litellm_startup.py
