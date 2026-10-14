import { afterEach, describe, expect, it } from "vitest";
import model from "./model.js";
import { getModelOverride, setModelOverride } from "./modelOverride.js";
import type { CommandContext } from "./types.js";

afterEach(() => setModelOverride(""));

function runModelCommand(args: string): string[] {
  const events: string[] = [];
  const context: CommandContext = {
    addSystemEvent: (content) => events.push(content),
    clearEvents: () => {},
    submit: () => {},
    resume: () => {},
    exit: () => {},
  };
  model.execute(args, context);
  return events;
}

describe("/model current prod catalog", () => {
  it("lists current direct and gateway routes once", () => {
    const [listing] = runModelCommand("");
    expect(listing).toContain("openai/gpt-5.6-sol");
    expect(listing).toContain("runyour/qwen3.8-max");
    expect(listing).toContain("anthropic/claude-sonnet-5");
    expect(listing.split("\n").filter((line) => line.trim() === "openai/gpt-6.1-sol")).toHaveLength(1);
    expect(listing).not.toContain("subscription/gpt-6.1-sol");
  });

  it("accepts a current route without an unknown-model warning", () => {
    const events = runModelCommand("openrouter/anthropic/claude-sonnet-5.5");
    expect(events).toHaveLength(1);
    expect(events[0]).toContain("Model override set");
    expect(getModelOverride()).toBe("openrouter/anthropic/claude-sonnet-5.5");
  });
});

describe("/model ChatGPT OAuth catalog", () => {
  it("lists only supported Codex subscription models", () => {
    const [listing] = runModelCommand("");
    const oauthModels = listing.split("\n").map((line) => line.trim())
      .filter((line) => line.startsWith("auth/gpt-"));
    expect(oauthModels).toEqual([
      "auth/gpt-6-astra", "auth/gpt-6.1-sol", "auth/gpt-6-luna",
      "auth/gpt-5.6-sol", "auth/gpt-5.6-terra", "auth/gpt-5.6-luna",
    ]);
  });

  it.each(["gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"])(
    "selects %s without an unknown-model warning", (slug) => {
      const events = runModelCommand(`auth/${slug}`);
      expect(events).toHaveLength(1);
      expect(getModelOverride()).toBe(`auth/${slug}`);
    },
  );
});
