/**
 * Per-process orchestrator override store.
 *
 * The /agent slash command writes here; useAgent reads here on every
 * submit() / resume() so user-driven switches take effect on the next
 * message.
 *
 * Empty string == no override (default useAgent behaviour resumes).
 */

let _override = "";

export function setAssistantOverride(id: string): void {
  _override = id.trim();
}

export function getAssistantOverride(): string {
  return _override;
}
