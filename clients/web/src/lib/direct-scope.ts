import { createHash } from "node:crypto";
import { isIP } from "node:net";

export function validateDirectTarget(type: string, value: string): string {
  const target = value.trim();
  if (type === "web_url") {
    let url: URL;
    try { url = new URL(target); } catch { throw new Error("Enter a root HTTP or HTTPS URL"); }
    if (
      !["http:", "https:"].includes(url.protocol) || !url.hostname ||
      url.username || url.password || url.pathname !== "/" || url.search || url.hash ||
      (url.port && !((url.protocol === "http:" && url.port === "80") || (url.protocol === "https:" && url.port === "443")))
    ) throw new Error("Direct Red scope requires a root HTTP or HTTPS URL on its default port");
    return target;
  }
  if (type === "ip_range") {
    if (isIP(target)) return target;
    const parts = target.split("/");
    if (parts.length === 2 && isIP(parts[0]) && /^\d+$/.test(parts[1])) {
      const max = isIP(parts[0]) === 4 ? 32 : 128;
      if (Number(parts[1]) <= max) return target;
    }
    throw new Error("Direct Red scope requires one IP address or CIDR range");
  }
  throw new Error("Unsupported target type");
}

export function directRoe(name: string, type: string, value: string, reference: string) {
  const target = validateDirectTarget(type, value);
  if (!reference.trim()) throw new Error("Authorization reference is required");
  const today = new Date().toISOString().slice(0, 10);
  return {
    engagement_name: name,
    client: "Operator-attested direct scope",
    start_date: today,
    end_date: today,
    engagement_type: "external",
    testing_window: "Operator-confirmed current session",
    in_scope: [{ target, type }],
    authorization_reference: reference.trim(),
    machine_enforcement: { mode: "enforce", in_scope: [target] },
  };
}

export function directApproval(roeBytes: Buffer): string {
  return `roe:${createHash("sha256").update(roeBytes).digest("hex")}\n`;
}
