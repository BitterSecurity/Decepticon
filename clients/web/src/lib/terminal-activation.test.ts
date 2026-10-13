import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ts from "typescript";

const require = createRequire(import.meta.url);
const { JSDOM } = require("jsdom") as {
  JSDOM: new (html: string, options: { url: string }) => { window: Window & typeof globalThis };
};

test("Live activation is lazy, survives tab history, and retains only visited modes", async () => {
  const dom = new JSDOM("<div id='root'></div>", { url: "https://example.test/engagements/first/plan" });
  const descriptors = new Map(["window", "document", "IS_REACT_ACT_ENVIRONMENT"].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperty(globalThis, "window", { configurable: true, value: dom.window });
  Object.defineProperty(globalThis, "document", { configurable: true, value: dom.window.document });
  Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", { configurable: true, value: true, writable: true });
  let pathname = dom.window.location.pathname;
  let engagementId = "first";
  let approved = true;
  let sensorAvailable = true;
  let running = false;
  const mounted: string[] = [];
  const disposed: string[] = [];
  const Terminal = ({ agentId }: { agentId: string }): React.ReactNode => {
    React.useEffect(() => {
      const identity = `${engagementId}:${agentId}`;
      mounted.push(identity);
      return () => { disposed.push(identity); };
    }, [agentId]);
    return React.createElement("div", { "data-agent": agentId });
  };
  dom.window.confirm = () => false;
  const fixtures: Record<string, unknown> = {
    "next/navigation": { useParams: () => ({ id: engagementId }), usePathname: () => pathname },
    "@/lib/engagement-context": { EngagementProvider: ({ children }: { children: React.ReactNode }) => children },
    "@/hooks/useRunObserver": { useRunObserver: () => ({ events: [], isRunning: running, activeRunId: null }) },
    "@/components/terminal/web-terminal": { WebTerminal: Terminal },
    "@/lib/utils": { cn: (...values: string[]) => values.join(" ") },
  };
  const source = readFileSync(new URL("../app/(dashboard)/engagements/[id]/layout.tsx", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const layoutModule = { exports: {} as { default: React.ComponentType<{ children?: React.ReactNode }> } };
  vm.runInNewContext(compiled, {
    module: layoutModule, exports: layoutModule.exports,
    require: (name: string) => Object.hasOwn(fixtures, name) ? fixtures[name] : require(name),
    window: dom.window, URLSearchParams, console,
    fetch: async (url: string) => ({
      ok: url === "/api/blue/status" ? sensorAvailable : true,
      json: async () => url.endsWith("/plan-docs") ? { redApproved: approved }
        : url.endsWith("/threads") ? []
        : { name: engagementId, targetType: "web_url", targetValue: "https://example.invalid", authorizationConfirmed: true },
    }),
  });
  const root = createRoot(dom.window.document.getElementById("root")!);
  const render = async (): Promise<void> => { await act(async () => { root.render(React.createElement(layoutModule.exports.default, { key: engagementId })); }); };
  const navigate = async (page: string): Promise<void> => {
    pathname = `/engagements/${engagementId}/${page}`;
    dom.window.history.pushState(null, "", pathname);
    await render();
  };
  const switchMode = async (mode: string): Promise<void> => {
    const button = [...dom.window.document.querySelectorAll("button")].find(button => button.textContent === mode);
    assert.ok(button);
    await act(async () => { button.click(); });
  };
  try {
    await render();
    assert.deepEqual(mounted, [], "Plan metadata must not activate terminals");
    await navigate("live");
    assert.deepEqual(mounted, ["first:decepticon"]);
    await navigate("plan");
    await navigate("live");
    for (const direction of ["back", "forward"] as const) {
      const navigation = new Promise(resolve => dom.window.addEventListener("popstate", resolve, { once: true }));
      dom.window.history[direction]();
      await navigation;
      pathname = dom.window.location.pathname;
      await render();
    }
    assert.equal(mounted.length, 1, "tab and history navigation must reuse the terminal");
    await switchMode("blue");
    await switchMode("red");
    await switchMode("blue");
    assert.deepEqual(mounted, ["first:decepticon", "first:blue_cell"]);
    assert.deepEqual(disposed, [], "mode changes retain earlier terminals");
    engagementId = "second";
    await navigate("plan");
    assert.equal(mounted.length, 2, "a new engagement starts inactive");
    assert.equal(disposed.length, 2);
    engagementId = "direct";
    approved = false;
    sensorAvailable = false;
    await navigate("live");
    assert.deepEqual(mounted, ["first:decepticon", "first:blue_cell", "direct:soundwave"], "direct Live activates the permitted initial mode once");
    await switchMode("red");
    await switchMode("blue");
    assert.equal(mounted.length, 3, "denied approval and unavailable sensor retain their gates");
    running = true;
    sensorAvailable = true;
    await render();
    await switchMode("blue");
    assert.equal(mounted.length, 3, "an active run still prevents switching modes");
  } finally {
    await act(async () => { root.unmount(); });
    dom.window.close();
    for (const [key, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
  assert.deepEqual(disposed, mounted, "unmount disposes every activated terminal exactly once");
});
