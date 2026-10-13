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
  JSDOM: new (html: string) => { window: Window & typeof globalThis };
};

test("unmount during xterm loading does not allocate abandoned terminal resources", async () => {
  const dom = new JSDOM("<div id='root'></div>");
  const priorWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const priorDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "window", { configurable: true, value: dom.window });
  Object.defineProperty(globalThis, "document", { configurable: true, value: dom.window.document });
  const environment = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean };
  const priorAct = environment.IS_REACT_ACT_ENVIRONMENT;
  environment.IS_REACT_ACT_ENVIRONMENT = true;
  let terminalCount = 0;
  let observerCount = 0;
  let socketCount = 0;
  const errors: unknown[][] = [];
  class Terminal {
    constructor() { terminalCount++; }
    loadAddon(): void {}
    open(): void {}
    dispose(): void {}
  }
  let finishImport!: (module: { Terminal: typeof Terminal }) => void;
  const pendingImport = new Promise<{ Terminal: typeof Terminal }>((resolve) => { finishImport = resolve; });
  const modules: Record<string, unknown> = {
    "@/lib/browser-config": require("./browser-config.ts"),
    "@/lib/browser-config-context": { useBrowserConfig: () => ({ terminalWsUrl: "ws://localhost:3003" }) },
    "@/lib/utils": { cn: (...values: string[]) => values.join(" ") },
    "xterm": pendingImport,
    "@xterm/addon-fit": { FitAddon: class {} },
    "xterm/css/xterm.css": {},
  };
  const source = readFileSync(new URL("../components/terminal/web-terminal.tsx", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const terminalModule = { exports: {} as { WebTerminal: React.ComponentType<Record<string, unknown>> } };
  vm.runInNewContext(compiled, {
    module: terminalModule, exports: terminalModule.exports,
    require: (name: string) => Object.hasOwn(modules, name) ? modules[name] : require(name),
    window: dom.window, document: dom.window.document, process: { env: {} },
    console: { ...console, error: (...args: unknown[]) => { errors.push(args); } },
    setTimeout, clearTimeout, setInterval, clearInterval,
    ResizeObserver: class {
      constructor() { observerCount++; }
      observe(): void {}
      disconnect(): void {}
    },
    WebSocket: class { constructor() { socketCount++; } },
  });
  const root = createRoot(dom.window.document.getElementById("root")!);
  try {
    await act(async () => {
      root.render(React.createElement(terminalModule.exports.WebTerminal, {
        engagementId: "owned", engagementSlug: "owned", targetType: "web_url",
        targetValue: "https://example.invalid", authorizationConfirmed: true,
      }));
    });
    assert.equal(terminalCount, 0, "the import is still pending");
    await act(async () => { root.unmount(); });
    await act(async () => { finishImport({ Terminal }); });
    assert.deepEqual(errors, [], "initialization must not hide an error");
    assert.equal(terminalCount, 0, "unmounted terminals must not allocate xterm");
    assert.equal(observerCount, 0, "unmounted terminals must not install observers");
    assert.equal(socketCount, 0, "unmounted terminals must not connect");
  } finally {
    await act(async () => { root.unmount(); });
    dom.window.close();
    if (priorWindow) Object.defineProperty(globalThis, "window", priorWindow);
    else Reflect.deleteProperty(globalThis, "window");
    if (priorDocument) Object.defineProperty(globalThis, "document", priorDocument);
    else Reflect.deleteProperty(globalThis, "document");
    environment.IS_REACT_ACT_ENVIRONMENT = priorAct;
  }
});
