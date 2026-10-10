/**
 * /model — runtime LLM swap for the current REPL session.
 *
 * Usage:
 *   /model              List the active override + every supported model id
 *   /model <id>         Set the override (e.g. /model groq/llama-3.3-70b-versatile)
 *   /model clear        Drop the override and resume the credentials-priority chain
 *
 * The override travels with each subsequent submit() through
 * config.configurable.model_override and is consumed by the agent's
 * ModelOverrideMiddleware on the server side. No restart, no rebuild —
 * just type the command and the next prompt routes to the new model.
 *
 * The list of supported models is the same matrix
 * decepticon/llm/models.py uses: every (AuthMethod, Tier) pair that
 * resolves to a real LiteLLM model id, grouped by AuthMethod.
 */

import type { Command } from "./types.js";
import { setModelOverride, getModelOverride } from "./modelOverride.js";

/** Static catalog mirroring decepticon/llm/models.py::METHOD_MODELS.
 * Kept here (instead of fetched live) so /model works offline before
 * the agent stack is up and so completion is instant. Edit this list
 * whenever METHOD_MODELS gains entries. */
const SUPPORTED_MODELS: Record<string, string[]> = {
  // Cloud Anthropic
  "Anthropic API": [
    "anthropic/claude-opus-4-7",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-haiku-4-5",
  ],
  "Claude Code OAuth": [
    "auth/claude-opus-4-7",
    "auth/claude-sonnet-4-6",
    "auth/claude-haiku-4-5",
  ],
  // Cloud OpenAI
  "OpenAI API": [
    "openai/gpt-5.5",
    "openai/gpt-5.4",
    "openai/gpt-5-nano",
    "openai/gpt-5.3-codex",
  ],
  "ChatGPT OAuth": [
    "auth/gpt-5.5",
    "auth/gpt-5.4",
    "auth/gpt-5.4-mini",
    "auth/gpt-5.3-codex",
  ],
  // Cloud Google
  "Google API": [
    "gemini/gemini-2.5-pro",
    "gemini/gemini-2.5-flash",
    "gemini/gemini-2.5-flash-lite",
  ],
  "Gemini Advanced (OAuth)": [
    "gemini-sub/gemini-2.5-pro",
    "gemini-sub/gemini-2.5-flash",
  ],
  // Other vendors
  "MiniMax": ["minimax/MiniMax-M3", "minimax/MiniMax-M2.7-highspeed"],
  "DeepSeek": ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash"],
  "xAI Grok API": ["xai/grok-4.3", "xai/grok-4-1-fast-reasoning"],
  "SuperGrok OAuth": ["grok-sub/grok-4.3", "grok-sub/grok-4-1-fast-reasoning"],
  "Mistral": ["mistral/mistral-large-latest", "mistral/codestral-latest"],
  "OpenRouter": [
    "openrouter/anthropic/claude-opus-4-7",
    "openrouter/anthropic/claude-sonnet-4-6",
    "openrouter/anthropic/claude-haiku-4-5",
  ],
  "NVIDIA NIM": [
    "nvidia_nim/meta/llama-3.3-70b-instruct",
    "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia_nim/meta/llama-3.2-3b-instruct",
  ],
  // Subscription OAuth
  "GitHub Copilot Pro (OAuth)": [
    "copilot/gpt-5.5",
    "copilot/claude-sonnet-4-6",
    "copilot/gpt-5.4-mini",
    "copilot/gpt-5.3-codex",
  ],
  "Perplexity Pro (OAuth)": ["pplx-sub/sonar-pro", "pplx-sub/sonar"],
  // Cloud gateways (added in OpenClaude provider migration)
  "AWS Bedrock": [
    "bedrock/anthropic.claude-opus-4-7",
    "bedrock/anthropic.claude-sonnet-4-6",
    "bedrock/anthropic.claude-haiku-4-5-20251001-v1:0",
  ],
  "GCP Vertex AI": [
    "vertex_ai/claude-opus-4-7@latest",
    "vertex_ai/claude-sonnet-4-6@latest",
    "vertex_ai/gemini-2.5-flash",
  ],
  "Azure OpenAI": ["azure/gpt-5.5", "azure/gpt-5.4", "azure/gpt-5-nano"],
  "Groq": [
    "groq/llama-3.3-70b-versatile",
    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
    "groq/llama-3.1-8b-instant",
  ],
  "Together AI": [
    "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "together_ai/mistralai/Mixtral-8x22B-Instruct-v0.1",
    "together_ai/meta-llama/Llama-3.2-3B-Instruct-Turbo",
  ],
  "Fireworks AI": [
    "fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct",
    "fireworks_ai/accounts/fireworks/models/llama-v3p1-70b-instruct",
    "fireworks_ai/accounts/fireworks/models/llama-v3p2-3b-instruct",
  ],
  "Cohere": [
    "cohere_chat/command-a-03-2025",
    "cohere_chat/command-r-plus",
    "cohere_chat/command-r",
  ],
  "Moonshot Kimi K2": [
    "moonshot/kimi-k2-instruct",
    "moonshot/moonshot-v1-128k",
    "moonshot/moonshot-v1-8k",
  ],
  "Z.ai GLM": ["zai/glm-4.5", "zai/glm-4.5-air", "zai/glm-4.5-flash"],
  "Alibaba DashScope (Qwen)": [
    "dashscope/qwen-max",
    "dashscope/qwen-plus",
    "dashscope/qwen-turbo",
  ],
  "GitHub Models": ["github/gpt-5.5", "github/gpt-5.4", "github/gpt-5-nano"],
  // OpenAI-compatible gateways / aggregators (oh-my-pi parity).
  // Routed via openai/ + api_base override; alias keeps the gateway prefix.
  "OpenCode Zen": [
    "opencode/claude-opus-4-6",
    "opencode/gpt-5.4",
    "opencode/glm-5-free",
  ],
  "Vercel AI Gateway": [
    "vercel/anthropic/claude-opus-4.6",
    "vercel/anthropic/claude-sonnet-4.6",
    "vercel/anthropic/claude-haiku-4.5",
  ],
  "Hugging Face Router": [
    "hf/deepseek-ai/DeepSeek-V3.1",
    "hf/meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "hf/openai/gpt-oss-120b",
  ],
  "Venice AI": [
    "venice/claude-opus-4-6",
    "venice/claude-sonnet-4-6",
    "venice/deepseek-v4-flash",
  ],
  "NanoGPT": [
    "nanogpt/anthropic/claude-opus-4.6",
    "nanogpt/anthropic/claude-sonnet-4.6",
    "nanogpt/anthropic/claude-3-5-haiku-20241022",
  ],
  "Synthetic": [
    "synthetic/hf:deepseek-ai/DeepSeek-V3.2",
    "synthetic/hf:meta-llama/Llama-3.3-70B-Instruct",
    "synthetic/hf:openai/gpt-oss-120b",
  ],
  "ZenMux": [
    "zenmux/anthropic/claude-opus-4.6",
    "zenmux/anthropic/claude-sonnet-4.6",
    "zenmux/anthropic/claude-haiku-4.5",
  ],
  "Baidu Qianfan (ERNIE)": [
    "qianfan/ernie-4.5-turbo-128k",
    "qianfan/ernie-4.5-turbo-32k",
    "qianfan/ernie-speed-pro-128k",
  ],
  "Cloudflare AI Gateway": [
    "cfgateway/anthropic/claude-opus-4-6",
    "cfgateway/anthropic/claude-sonnet-4-6",
    "cfgateway/anthropic/claude-haiku-4-5",
  ],
  // Local
  "Ollama (local)": ["ollama_chat/qwen3-coder:30b"],
  "Ollama Cloud": ["ollama_chat/<your OLLAMA_CLOUD_MODEL>"],
  "LM Studio (local)": ["lm_studio/<your LMSTUDIO_MODEL>"],
  "Custom OpenAI Endpoint": ["custom/<your CUSTOM_OPENAI_MODEL>"],
};

function flatten(): string[] {
  return Object.values(SUPPORTED_MODELS).flat();
}

function isKnown(id: string): boolean {
  // Exact match against the catalog. Local placeholders are skipped
  // because they carry "<your ...>" hints, not real ids — users with
  // local models should pass the resolved id (ollama_chat/foo etc.).
  const exact = flatten().filter((m) => !m.includes("<"));
  return exact.includes(id);
}

const model: Command = {
  name: "model",
  description: "Show or change the LLM model for this session",
  argumentHint: "[<model-id> | clear]",
  execute(args, ctx) {
    const arg = args.trim();

    // No arg → list
    if (arg === "") {
      const current = getModelOverride();
      const lines: string[] = [];
      lines.push(
        current
          ? `Active override: ${current}`
          : "No override active — using the credentials-priority chain from .env",
      );
      lines.push("");
      lines.push("Supported models (group → ids):");
      for (const [group, ids] of Object.entries(SUPPORTED_MODELS)) {
        lines.push(`  ${group}:`);
        for (const id of ids) lines.push(`    ${id}`);
      }
      lines.push("");
      lines.push("Usage:");
      lines.push("  /model <id>     Switch the primary model for the next message");
      lines.push("  /model clear    Drop the override, resume the default chain");
      ctx.addSystemEvent(lines.join("\n"));
      return;
    }

    // Clear
    if (arg === "clear" || arg === "off" || arg === "none") {
      setModelOverride("");
      ctx.addSystemEvent("Model override cleared. Default chain resumes on next message.");
      return;
    }

    // Set
    if (!isKnown(arg)) {
      // Allow unknown ids — user may want to test a brand-new model
      // not yet in the catalog. Surface a soft warning instead of
      // refusing so the operator stays in control.
      ctx.addSystemEvent(
        `Note: '${arg}' is not in the built-in catalog. Sending anyway — ` +
          `the model call will fail with a clear 404 if LiteLLM does not know it.`,
      );
    }
    setModelOverride(arg);
    ctx.addSystemEvent(
      `Model override set: ${arg}\n` +
        `Takes effect on the next message. Type /model clear to revert.`,
    );
  },
};

export default model;
