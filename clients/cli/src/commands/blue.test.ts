import { describe, expect, it } from "vitest";
import { parseLocalUpstream } from "./blue.js";

describe("Blue local target boundary", () => {
  it.each([
    ["127.0.0.1:3000", "http://127.0.0.1:3000"],
    ["http://localhost:8080", "http://localhost:8080"],
    ["https://[::1]:8443", "https://[::1]:8443"],
  ])("accepts a local listener at %s", (input, expected) => {
    expect(parseLocalUpstream(input)).toBe(expected);
  });

  it.each([
    "https://example.com:443",
    "http://192.168.1.5:8080",
    "http://127.0.0.1:3000/path",
    "http://user:pass@localhost:3000",
    "file://localhost:3000",
    "localhost",
  ])("rejects a non-local listener or ambiguous target at %s", (input) => {
    expect(() => parseLocalUpstream(input)).toThrow();
  });
});
