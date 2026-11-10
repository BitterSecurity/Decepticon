# Versioned change tracking

Before opening or updating any PR, including documentation or maintenance,
update the single root `CHAGELOG.md` and include PR links under Unreleased.
Preserve the full existing history; do not create a second changelog. Distinguish
proposed, merged and released changes. At an actual release, move the relevant
entries into a dated section using its real version or immutable release ID;
do not invent release dates or bump unrelated versions. SaaS and OSS versions
are independent. Link `CHAGELOG.md` from each PR description.
