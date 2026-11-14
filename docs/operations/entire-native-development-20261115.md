# Entire-first local development

Use the official Entire CLI, Graph, Brain and `entireio/skills` without a custom
replacement skill. This is local development setup; application runtime and
release versions are unaffected.

## Source and reviews

Use the existing Entire GitHub mirror as `origin`, with `github` retained for
GitHub inspection. The two remote main refs were compared before setup. Do not
detach the mirror. GitHub remains the upstream source and protection authority.
Trails group intent, branches, captured sessions and review evidence; a
GitHub-backed change uses a linked GitHub PR for CI/merging. Trail and GitHub
approvals are separate. Follow both sets of applicable gates without force or
bypass. Hosted Actions remain unavailable; retain actual local verification.

## Local installation

Qualified CLI 0.13.4, official Graph 0.6.0 and Brain 0.5.1. The plugin manager
verified official release checksums; plugins are pinned. Shared official skills
are cloned unmodified at `~/.agents/skills/entire`; the qualified source commit
is recorded in the evidence. Read `entire agent-help` and subcommand help rather
than copying flags from a different release.

From a clean committed tree, setup used:

```sh
entire brain setup --no-backfill --no-daemon --no-agent-hooks
entire graph setup --repo . --no-claude-hook
entire enable --agent codex --telemetry=false --agent-help-skill --absolute-git-hook-path --checkpoint-push-remote origin --skip-push-sessions
```

The generated `.entire/agent-guide.md` is official output. Setup preserves text
outside its managed AGENTS.md/CLAUDE.md blocks. Commit generated project config
and guidance together. Local checkpoint destination remains untracked in
`.entire/settings.local.json`. Each independent clone needs Git hooks installed;
linked worktrees share Git hooks through their common Git directory.

`ENTIRE_BRAIN_NO_EGRESS=1` is set in this project's Codex shell environment and
in the operator's shell startup configuration. This is Brain's official egress
kill switch; Git/Entire code and checkpoint transport is configured separately.
No model backfill, Brain daemon, additional Claude hooks, hosted memory sync or
paid review agents are enabled by this setup. CLI session-end hooks can otherwise
trigger model extraction, so keep the no-egress setting on for this workflow.

## Start coding

After these project files reach main, fetch main and create a separate task
branch/worktree from the latest remote commit. Start `codex` with that worktree
as its cwd. Do not launch a coding session from the home directory and then edit
unrelated repositories: context ownership and privacy depend on the repo.
Use a separate session per repository when their visibility differs.

Codex can discover project hooks at the primary checkout; ensure it also contains
the merged config. Use `/hooks` to inspect sources, duplicate global/project
handlers and exact trust requests. Never edit trust hashes or bypass trust.
Existing global hooks do not prove that this project's hooks have been approved.
Restart sessions after setup. Validate `entire status` and actual session data;
`entire doctor` exit zero can still include hook warnings.

Use the official skills for search, provenance, review and handoff. Brain briefs
provide indexed history/docs; Graph locates committed code and relationships.
Drop `--head` only when the question needs uncommitted code. Inspect source at
returned locations. Missing history means no recorded intent, not proof of a
particular design rationale. Commits create checkpoints automatically; do not
create manual checkpoints unless explicitly requested.

## Privacy

Source is public. Project settings disable checkpoint uploads. Do not attach a session that contains private work. Keep transcripts local unless a separate private checkpoint destination is explicitly selected and verified.

## Verification

[Measured evidence](entire-native-development-20261115.json) records initial
indexing, query latency, coverage and remaining rollout checks. These are local
observations, not a performance improvement claim. Graph queries returned source
locations; the source anchors are checked separately. Parser limitations remain
visible. Runtime tests and production builds are unnecessary for this config-only
change; no application code is modified.

## Official references

- https://docs.entire.com/agents/codex
- https://docs.entire.com/guides/trails/overview
- https://docs.entire.com/guides/repositories/mirrors
- https://docs.entire.com/guides/checkpoints/checkpoint-destination
- https://docs.entire.com/guides/brain/overview
- https://docs.entire.com/guides/brain/storage-and-sync
- https://docs.entire.com/guides/graph/overview
- https://github.com/entireio/skills

Setup review is tracked in [Trail 2](https://entire.com/gh/bittersecurity/-/trails/2).
