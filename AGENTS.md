# Versioned change tracking

Before opening or updating any PR, including documentation or maintenance changes,
update the single root `CHAGELOG.md` and link it from the PR description. Preserve
the full existing history and release automation; do not create another changelog.
Record user-visible changes, fixes and
compatibility/upgrade notes with PR links under Unreleased; distinguish proposed,
merged and released changes. On release, move entries into a dated section using
the actual version or immutable release identifier. Do not invent dates, bump
unrelated versions or edit another dirty worktree. SaaS releases and OSS package
versions are independent.

For each task, fetch and confirm the latest remote main, then create a separate
task branch/worktree from it. Preserve other dirty worktrees. After confirming
the task PR merged, remove its worktree only after checking for uncommitted,
unpushed or active/shared work.

# Extension architecture

Before extending agents, skills, workflows or client adapters, read
[the extension contract](docs/design/extension-contract.md). Keep stable capability
cores, reusable SKILL.md knowledge, per-process WORKFLOW.md contracts and trusted
client launch/authorization adapters separate. Preserve immutable workflow pins,
OPPLAN ownership and optional explicit Plan mode.

# OSS release policy

Before selecting a version, tagging, publishing or recovering an OSS release,
follow [RELEASE.md](RELEASE.md): classify by compatibility/user impact, verify
actual WSL product and candidate artifacts, preserve immutable versions and user
data, and record the actual publication date. A merged PR does not authorize a
release or require a version bump. SaaS identifiers remain independent.
