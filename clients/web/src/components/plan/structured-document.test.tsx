import assert from "node:assert/strict";
import { test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { RenderStructuredDocument } from "./structured-document";

test("planning document renders nested fields, arrays, zero and false", () => {
  const html = renderToStaticMarkup(<RenderStructuredDocument data={{
    contact_details: [{ name: "Case owner", phone: "555-0100" }],
    retain_days: 0, enabled: false, absent: null,
  }} />);
  for (const expected of ["Contact Details", "Case owner", "555-0100", "Retain Days", "0", "false", "Not specified"])
    assert.ok(html.includes(expected), expected);
});

test("planning document escapes operator-controlled text", () => {
  const html = renderToStaticMarkup(<RenderStructuredDocument data={{ note: "<script>alert(1)</script>" }} />);
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(!html.includes("<script>"));
});
