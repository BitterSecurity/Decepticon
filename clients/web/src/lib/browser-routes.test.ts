import assert from "node:assert/strict";
import { test } from "node:test";
import { langGraphApiUrl, terminalWebSocketUrl } from "./browser-routes";
const location = { protocol: "https:", host: "dashboard.example.test" };
test("relative LangGraph endpoint follows the dashboard origin", () => {
  assert.equal(langGraphApiUrl(location, "/langgraph"), "https://dashboard.example.test/langgraph");
});
test("relative terminal endpoint uses encrypted websocket on HTTPS", () => {
  assert.equal(terminalWebSocketUrl(location, "/terminal").toString(), "wss://dashboard.example.test/terminal");
});
test("direct localhost endpoints remain supported", () => {
  assert.equal(langGraphApiUrl(location, "http://localhost:2024"), "http://localhost:2024");
  assert.equal(terminalWebSocketUrl(location, "ws://localhost:3003").toString(), "ws://localhost:3003/");
});
test("terminal URL retains configured query parameters", () => {
  assert.equal(terminalWebSocketUrl(location, "/terminal?deployment=demo").searchParams.get("deployment"), "demo");
});
test("HTTP dashboard uses plain websocket for relative routes", () => {
  assert.equal(terminalWebSocketUrl({ protocol: "http:", host: "localhost:3000" }, "/terminal").protocol, "ws:");
});
test("unsupported endpoint protocols fail explicitly", () => {
  assert.throws(() => langGraphApiUrl(location, "file:///etc/passwd"), /HTTP/);
  assert.throws(() => terminalWebSocketUrl(location, "file:///etc/passwd"), /WS/);
});
