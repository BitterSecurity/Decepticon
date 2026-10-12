import { test } from "node:test";
import assert from "node:assert/strict";
import { directApproval, directRoe, validateDirectTarget } from "./direct-scope";

test("direct Red scope pins a root web target and enforcement", () => {
  const roe = directRoe("test-engagement", "web_url", "https://example.com", "agreement-1");
  assert.deepEqual(roe.in_scope, [{ target: "https://example.com", type: "web_url" }]);
  assert.deepEqual(roe.machine_enforcement, { mode: "enforce", in_scope: ["https://example.com"] });
  assert.match(directApproval(Buffer.from(JSON.stringify(roe))), /^roe:[a-f0-9]{64}\n$/);
});

test("direct Red scope rejects unsupported target shapes", () => {
  for (const [type, target] of [
    ["web_url", "https://example.com/admin"],
    ["web_url", "https://example.com:8443"],
    ["web_url", "https://user:pass@example.com"],
    ["web_url", "file:///etc/passwd"],
    ["ip_range", "10.0.0.1-10.0.0.255"],
    ["ip_range", "10.0.0.0/33"],
  ]) {
    assert.throws(() => validateDirectTarget(type, target));
  }
});
