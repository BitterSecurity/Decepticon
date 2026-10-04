import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { approveRedMode, planningBundleDigest, revokeRedMode } from "./planMode.js";

const DOCS = [
  "roe.json", "threat-profile.json", "conops.json", "deconfliction.json",
  "contact.json", "data-handling.json", "abort.json", "cleanup.json",
];
const DIGEST = "23bb0ad057e96e5098d6c8ef731ce1afadd9e1957f6adf6b720196ad35409418";

describe("Red mode approval", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("requires a validated draft and invalidates changed documents", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "decepticon-mode-"));
    await mkdir(path.join(root, "plan"));
    for (const filename of DOCS) await writeFile(path.join(root, "plan", filename), "{}");
    vi.stubEnv("DECEPTICON_ENGAGEMENT", "test");
    vi.stubEnv("DECEPTICON_WORKSPACE_PATH", root);

    expect(await planningBundleDigest(root)).toBe(DIGEST);
    await expect(approveRedMode()).rejects.toThrow("Complete and validate");
    await writeFile(path.join(root, ".planning-draft-ready"), DIGEST);
    await approveRedMode();
    expect(await readFile(path.join(root, ".red-approved"), "utf-8")).toBe(DIGEST);

    await writeFile(path.join(root, "plan", "roe.json"), '{"changed":true}');
    await expect(approveRedMode()).rejects.toThrow("Planning documents changed");
    await revokeRedMode();
    await expect(readFile(path.join(root, ".red-approved"), "utf-8")).rejects.toThrow();
  });
});
