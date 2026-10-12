import { expect, test } from "vitest";
import model from "./model";
import { getModelOverride, setModelOverride } from "./modelOverride";
import type { CommandContext } from "./types";

test("listed Ollama model can be selected without unknown-model warning", () => {
  const previous = getModelOverride();
  const messages: string[] = [];
  const context: CommandContext = {
    addSystemEvent: text => messages.push(text),
    clearEvents() {}, submit() {}, resume() {}, exit() {},
  };
  try {
    model.execute("", context);
    const catalog = messages.pop() ?? "";
    const ollama = catalog.split("Ollama (local):\n")[1]?.split("\n")[0].trim() ?? "";
    expect(ollama).not.toBe("");
    model.execute(ollama, context);
    expect(getModelOverride()).toBe("ollama_chat/qwen3-coder:30b");
    expect(messages.some(text => text.includes("not in the built-in catalog"))).toBe(false);
  } finally {
    setModelOverride(previous ?? "");
  }
});
