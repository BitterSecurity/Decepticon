# Contributor instructions

Before opening or updating any product, documentation, or maintenance PR, update
[Chagelog.md](Chagelog.md) and the canonical [CHANGELOG.md](CHANGELOG.md) with a
concise Unreleased entry, PR link, user-visible behavior, and compatibility notes.
Keep Chagelog.md as the entry point rather than duplicating version history.
Distinguish proposed, merged, and deployed/released changes. Preserve history and
release automation; move entries to a dated real version or immutable release
identifier only when released. SaaS and OSS versions are independent. Do not bump
unrelated versions, invent release dates, or edit another dirty worktree for this
requirement. Do not publish a release unless explicitly requested.
