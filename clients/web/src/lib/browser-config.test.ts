import assert from "node:assert/strict";
import { test } from "node:test";
import { browserConfig, langGraphApiUrl, terminalWebSocketUrl } from "./browser-config";

const location = { protocol: "https:", host: "dashboard.example.test:8443" };

test("public config reads current runtime values and excludes private variables", () => {
  const env = {
    NEXT_PUBLIC_LANGGRAPH_API_URL: "/langgraph",
    NEXT_PUBLIC_TERMINAL_WS_URL: "/terminal?deployment=blue",
    DATABASE_URL: "private-database",
    LANGGRAPH_API_URL: "http://internal:2024",
  };
  assert.deepEqual(browserConfig(env), {
    langGraphApiUrl: "/langgraph",
    terminalWsUrl: "/terminal?deployment=blue",
  });
  env.NEXT_PUBLIC_LANGGRAPH_API_URL = "/other-langgraph";
  assert.equal(browserConfig(env).langGraphApiUrl, "/other-langgraph");
});

test("unset public config retains direct localhost defaults", () => {
  assert.deepEqual(browserConfig({}), {
    langGraphApiUrl: "http://localhost:2024",
    terminalWsUrl: "ws://localhost:3003",
  });
});

test("relative API and terminal paths follow the HTTPS dashboard origin", () => {
  assert.equal(langGraphApiUrl(location, "/langgraph"), "https://dashboard.example.test:8443/langgraph");
  assert.equal(terminalWebSocketUrl(location, "/terminal").href, "wss://dashboard.example.test:8443/terminal");
});

test("terminal query parameters survive engagement metadata and protocol conversion", () => {
  const url = terminalWebSocketUrl(location, "/terminal?deployment=blue");
  url.searchParams.set("engagementId", "test & engagement");
  assert.equal(url.searchParams.get("deployment"), "blue");
  assert.equal(url.searchParams.get("engagementId"), "test & engagement");
  assert.equal(terminalWebSocketUrl({ protocol: "http:", host: "localhost:3000" }, "/terminal").protocol, "ws:");
});

test("direct endpoints preserve their configured protocol and API path", () => {
  assert.equal(langGraphApiUrl(location, "http://localhost:2024"), "http://localhost:2024");
  assert.equal(terminalWebSocketUrl(location, "ws://localhost:3003").href, "ws://localhost:3003/");
  assert.equal(terminalWebSocketUrl(location, "https://dashboard.example.test/terminal").protocol, "wss:");
  assert.equal(langGraphApiUrl(location, "/langgraph?tenant=blue"), "https://dashboard.example.test:8443/langgraph?tenant=blue");
});

test("non-network endpoint protocols are rejected before connection", () => {
  assert.throws(() => langGraphApiUrl(location, "file:///etc/passwd"), /HTTP/);
  assert.throws(() => langGraphApiUrl(location, "javascript:alert(1)"), /HTTP/);
  assert.throws(() => terminalWebSocketUrl(location, "file:///etc/passwd"), /WS/);
});

test("runtime config is available to client consumers during server rendering", async () => {
  const { createElement } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { BrowserConfigProvider, useBrowserConfig } = await import("./browser-config-context");
  function Consumer() {
    return createElement("span", null, useBrowserConfig().langGraphApiUrl);
  }
  const config = browserConfig({ NEXT_PUBLIC_LANGGRAPH_API_URL: "/runtime-api" });
  assert.equal(renderToStaticMarkup(createElement(BrowserConfigProvider, {
    config,
    children: createElement(Consumer),
  })), "<span>/runtime-api</span>");
});
