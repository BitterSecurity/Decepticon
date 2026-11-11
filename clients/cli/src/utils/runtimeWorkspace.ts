/** Local file access and server filesystem namespaces may have different mounts. */
export function runtimeWorkspace(): string {
  return process.env.DECEPTICON_RUNTIME_WORKSPACE_PATH ?? process.env.DECEPTICON_WORKSPACE_PATH ?? "/workspace";
}
export function acceptsRuntimeWorkspace(path: string): boolean {
  return path === runtimeWorkspace();
}
