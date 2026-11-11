import { afterEach, expect, it, vi } from "vitest";
import mode from "../commands/mode.js";
import model from "../commands/model.js";
import { getAssistantOverride, setAssistantOverride } from "../commands/assistantOverride.js";
import { getModelOverride, setModelOverride } from "../commands/modelOverride.js";
import type { ChoicePickerRequest, CommandContext } from "../commands/types.js";

afterEach(() => { vi.unstubAllEnvs(); setAssistantOverride(""); setModelOverride(""); });

function context(requests: ChoicePickerRequest[]): CommandContext {
  return { addSystemEvent() {}, clearEvents() {}, submit() {}, resume() {}, exit() {},
    openPicker: (request) => { requests.push(request); }, selectAssistant: () => true };
}

it("refuses Blue through the no-argument mode picker without a sensor", async () => {
  // Given a CLI context that presents choices
  const requests: ChoicePickerRequest[] = [];
  await mode.execute("", context(requests));
  const picker = requests[0];
  if (!picker) throw new Error("Mode picker was not opened");
  // When the operator confirms Blue without a running sensor or a target
  await picker.onSelect("blue");
  // Then Blue is refused — the operator stays in the current mode
  expect(getAssistantOverride()).not.toBe("autobot");
});

it("selects a model through the no-argument model picker", async () => {
  // Given a CLI context that presents choices
  vi.stubEnv("DECEPTICON_AUTH_CHATGPT", "true");
  const requests: ChoicePickerRequest[] = [];
  await model.execute("", context(requests));
  const picker = requests[0];
  if (!picker) throw new Error("Model picker was not opened");
  // When the operator confirms a supported model
  expect(picker.options.map((option) => option.value)).toContain("auth/gpt-6-astra");
  await picker.onSelect("auth/gpt-6-astra");
  // Then the override changes for the next message
  expect(getModelOverride()).toBe("auth/gpt-6-astra");
});

