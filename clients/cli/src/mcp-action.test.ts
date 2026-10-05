import { describe, expect, it } from "vitest";
import { runMcpAction } from "./mcp-action.js";

describe("MCP CLI action adapter", () => {
  it("returns the web URL without a Docker dependency", async () => {
    await expect(runMcpAction(["web", "url"])).resolves.toContain("Web dashboard URL:");
  });

  it("rejects unsupported actions instead of silently starting web", async () => {
    await expect(runMcpAction(["web", "restart"])).rejects.toThrow("Web action must be");
    await expect(runMcpAction(["blue", "analyze"])).rejects.toThrow("Blue action must be");
  });

  it("propagates invalid local target errors before running Docker", async () => {
    await expect(runMcpAction(["blue", "up", "https://public.example:3000"])).rejects.toThrow("local HTTP service");
    await expect(runMcpAction(["blue", "up", "127.0.0.1:3000", "--logs", "relative"])).rejects.toThrow("absolute directory");
  });

  it("rejects surplus arguments on read operations", async () => {
    await expect(runMcpAction(["blue", "status", "unexpected"])).rejects.toThrow("takes no arguments");
  });
});
