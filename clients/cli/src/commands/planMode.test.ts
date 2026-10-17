import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { approveRedMode, assertRedApproved, planningBundleDigest, revokeRedMode } from "./planMode.js";

const DOCS = [
  "roe.json", "threat-profile.json", "conops.json", "deconfliction.json",
  "contact.json", "data-handling.json", "abort.json", "cleanup.json",
];
const DIGEST = "23bb0ad057e96e5098d6c8ef731ce1afadd9e1957f6adf6b720196ad35409418";

describe("Red mode approval", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("refuses Red outside an engagement workspace", async () => {
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "");
    await expect(approveRedMode()).rejects.toThrow("engagement workspace");
  });

  it("refuses to approve an incomplete Plan without direct RoE approval", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "decepticon-incomplete-mode-"));
    await mkdir(path.join(root, "plan"));
    await writeFile(path.join(root, "plan", "roe.json"), "{}");
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "incomplete");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", root);

    await expect(approveRedMode()).rejects.toThrow();

    await expect(readFile(path.join(root, ".red-approved"))).rejects.toMatchObject({ code: "ENOENT" });
    await expect(assertRedApproved()).rejects.toThrow();
  });

  it("requires a validated draft and invalidates changed documents", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "decepticon-mode-"));
    await mkdir(path.join(root, "plan"));
    for (const filename of DOCS) await writeFile(path.join(root, "plan", filename), "{}");
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "test");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", root);

    expect(await planningBundleDigest(root)).toBe(DIGEST);
    await expect(approveRedMode()).rejects.toThrow("Planning documents changed");
    await writeFile(path.join(root, ".planning-draft-ready"), DIGEST);
    await approveRedMode();
    expect(await readFile(path.join(root, ".red-approved"), "utf-8")).toBe(DIGEST);
    await expect(assertRedApproved()).resolves.toBeUndefined();

    await writeFile(path.join(root, "plan", "roe.json"), '{"changed":true}');
    await expect(approveRedMode()).rejects.toThrow("Planning documents changed");
    await expect(assertRedApproved()).rejects.toThrow("scope changed");
    await revokeRedMode();
    await expect(readFile(path.join(root, ".red-approved"), "utf-8")).rejects.toThrow();
  });

  it("accepts a direct RoE approval only while its bytes match", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "decepticon-direct-mode-"));
    await mkdir(path.join(root, "plan"));
    const roe = '{"in_scope":[{"target":"example.com"}]}';
    await writeFile(path.join(root, "plan", "roe.json"), roe);
    await writeFile(path.join(root, ".red-approved"), `roe:${createHash("sha256").update(roe).digest("hex")}`);
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "direct");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", root);
    await expect(approveRedMode()).resolves.toBeUndefined();
    await expect(assertRedApproved()).resolves.toBeUndefined();
    await writeFile(path.join(root, "plan", "roe.json"), '{"in_scope":[]}');
    await expect(approveRedMode()).rejects.toThrow("authorization is missing");
    await expect(assertRedApproved()).rejects.toThrow("scope changed");
  });
});
