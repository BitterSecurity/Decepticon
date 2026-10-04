import { createHash } from "node:crypto";
import { readFile, unlink, writeFile } from "node:fs/promises";
import path from "node:path";

const PLAN_DOCUMENTS = [
  "roe.json",
  "threat-profile.json",
  "conops.json",
  "deconfliction.json",
  "contact.json",
  "data-handling.json",
  "abort.json",
  "cleanup.json",
];

function workspaceRoot(): string | null {
  if (!process.env.DECEPTICON_ENGAGEMENT) return null;
  return process.env.DECEPTICON_WORKSPACE_PATH || "/workspace";
}

export async function planningBundleDigest(root: string): Promise<string> {
  const hash = createHash("sha256");
  for (const filename of PLAN_DOCUMENTS) {
    hash.update(filename);
    hash.update("\0");
    hash.update(await readFile(path.join(root, "plan", filename)));
    hash.update("\0");
  }
  return hash.digest("hex");
}

export async function approveRedMode(): Promise<void> {
  const root = workspaceRoot();
  if (!root) return;
  const current = await planningBundleDigest(root);
  const draft = (await readFile(path.join(root, ".planning-draft-ready"), "utf-8")).trim();
  if (draft !== current) {
    throw new Error("Planning documents changed after validation. Finish Interview validation again.");
  }
  await writeFile(path.join(root, ".red-approved"), current, { mode: 0o600 });
}

export async function revokeRedMode(): Promise<void> {
  const root = workspaceRoot();
  if (!root) return;
  await unlink(path.join(root, ".red-approved")).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT") throw error;
  });
}
