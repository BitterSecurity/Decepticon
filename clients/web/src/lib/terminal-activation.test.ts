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
  JSDOM: new (html: string, options: { url: string }) => {
    window: Window & typeof globalThis;
  };
};

test("Plan does not mount terminals; Live activation persists across tabs and run modes", async () => {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://dashboard.example.test/engagements/owned/plan",
  });
  const priorWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const priorDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "window", { configurable: true, value: dom.window });
  Object.defineProperty(globalThis, "document", { configurable: true, value: dom.window.document });
  const reactEnvironment = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean };
  const priorAct = reactEnvironment.IS_REACT_ACT_ENVIRONMENT;
  reactEnvironment.IS_REACT_ACT_ENVIRONMENT = true;
  let pathname = "/engagements/owned/plan";
  let mounts = 0;
  let unmounts = 0;
  const Terminal = ({ agentId }: { agentId: string }) => {
    React.useEffect(() => {
      mounts++;
      return () => { unmounts++; };
    }, []);
    return React.createElement("div", { "data-agent": agentId });
  };
  const modules: Record<string, unknown> = {
    "next/navigation": { useParams: () => ({ id: "owned" }), usePathname: () => pathname },
    "@/lib/engagement-context": {
      EngagementProvider: ({ children }: { children: React.ReactNode }) => children,
    },
    "@/hooks/useRunObserver": { useRunObserver: () => ({ events: [], isRunning: false, activeRunId: null }) },
    "@/components/terminal/web-terminal": { WebTerminal: Terminal },
    "@/lib/utils": { cn: (...values: string[]) => values.join(" ") },
  };
  const source = readFileSync(new URL("../app/(dashboard)/engagements/[id]/layout.tsx", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} as { default: React.ComponentType<{ children?: React.ReactNode }> } };
  vm.runInNewContext(compiled, {
    module, exports: module.exports,
    require: (name: string) => Object.hasOwn(modules, name) ? modules[name] : require(name),
    window: dom.window, URLSearchParams, console,
    fetch: async (url: string) => ({
      ok: true,
      json: async () => url.endsWith("/plan-docs") ? { redApproved: true }
        : url.endsWith("/threads") ? []
        : { name: "owned", targetType: "web_url", targetValue: "https://rehearsal.invalid", authorizationConfirmed: true },
    }),
  });
  const root = createRoot(dom.window.document.getElementById("root")!);
  try {
    await act(async () => { root.render(React.createElement(module.exports.default)); });
    assert.equal(mounts, 0, "metadata on Plan must not create a terminal");
    pathname = "/engagements/owned/live";
    await act(async () => { root.render(React.createElement(module.exports.default)); });
    assert.equal(mounts, 1);
    const blue = [...dom.window.document.querySelectorAll("button")].find(button => button.textContent === "blue")!;
    await act(async () => { blue.click(); });
    assert.equal(mounts, 2, "the new mode gets its own terminal");
    assert.equal(unmounts, 0, "the previous mode terminal remains mounted");
    pathname = "/engagements/owned/plan";
    await act(async () => { root.render(React.createElement(module.exports.default)); });
    assert.equal(mounts, 2);
    assert.equal(unmounts, 0, "tab navigation preserves activated mode terminals");
  } finally {
    await act(async () => { root.unmount(); });
    dom.window.close();
    if (priorWindow) Object.defineProperty(globalThis, "window", priorWindow);
    else Reflect.deleteProperty(globalThis, "window");
    if (priorDocument) Object.defineProperty(globalThis, "document", priorDocument);
    else Reflect.deleteProperty(globalThis, "document");
    reactEnvironment.IS_REACT_ACT_ENVIRONMENT = priorAct;
  }
});
