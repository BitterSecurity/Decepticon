# Versioned change tracking

Before opening or updating any PR, including documentation or maintenance changes,
update root `Chagelog.md` and link it from the PR description. Preserve the canonical
`CHANGELOG.md` history and release automation: keep `Chagelog.md` an entry point and
update the canonical history in the same PR. Record user-visible changes, fixes and
compatibility/upgrade notes with PR links under Unreleased; distinguish proposed,
merged and released changes. On release, move entries into a dated section using
the actual version or immutable release identifier. Do not invent dates, bump
unrelated versions or edit another dirty worktree. SaaS releases and OSS package
versions are independent.
