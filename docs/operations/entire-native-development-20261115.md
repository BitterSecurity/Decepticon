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
as its cwd and run `codex` normally. No separate profile or launcher is needed.
The operator's supported Codex hook-state settings disable seven legacy global
Entire handlers, so only the repository's official hooks record the session.
Approval hashes, official skills, saved sessions and worktrees are preserved.
The earlier local `entire` profile has been retired and backed up. Do not launch a coding session from the home directory and then edit
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

The Trail created [GitHub PR #1198](https://github.com/BitterSecurity/Decepticon/pull/1198)
automatically when its branch was linked. Source pushes through Entire were
observed at the same GitHub branch commit. No review protections were changed.

Live capture requires approval of the project hooks in Codex /hooks. Generated
hook syntax and primary-checkout discovery are checked independently.

Initial Graph coverage intentionally skips 2 minified JSON data files. No
source parser errors were reported in that baseline.

Project hook installation, generated syntax, discovery, local Brain queries,
Graph queries and linked-worktree cache use pass. Codex still requires approval
of the seven project hooks through `/hooks` on this machine; no approval hashes
were copied. Live OSS capture remains unqualified until that user action.

This review supersedes [the earlier setup draft #1197](https://github.com/BitterSecurity/Decepticon/pull/1197);
its source branch and worktree are preserved, with no claim that it merged.

The operator now starts plain `codex` with duplicate legacy global handlers
disabled in the supported user hook settings. No separate profile is needed.
This does not approve the OSS project's seven hooks; `/hooks` review remains a
required user action before claiming live OSS capture.


## Integration review, 2026-11-17

Reviewed all 11 proposed files at `45ffa1d` and integrated current main
`59827f6`, retaining every existing change-history entry and the Rust/Axum
instruction. No generated hook command, project environment setting or public
checkpoint policy was changed. JSON/TOML parsing and `sh -n` for each of the
seven commands pass. The CLI reports automatic checkpoint pushing disabled.
Graph's exact proposed-tree comparison covers 11 files; Brain recall returned
no durable intent facts. No checkpoint trailers were available, so this is a
source/configuration review rather than a transcript-backed intent claim.

The configured public checkout's actual `entire status` still reports seven
Codex hooks requiring `/hooks` approval. Only the normal Codex trust UI can
complete that operator step. No trust values are copied or edited, and no
private session is attached to this public repository. Live capture remains
unqualified until a new public-repository session runs with approved hooks.
No application test/build, hosted-CI pass, release or performance gain is claimed
for this configuration-only integration.

## Actual public live capture, 2026-11-18

The earlier approval/capture limitation above is historical. The user completed
the public README-only Codex request in the configured validation clone. Actual
`entire session list --json` first showed the new session active, then idle with
**one captured turn and zero pending turns**. This establishes live capture and
completion of the read-only turn. Zero checkpoints is expected without a commit.
It does not establish terminal process exit or execution of every hook event.
The session-end event and commit checkpoint creation are not independently
claimed. [Sanitized measured evidence](entire-public-live-capture-20261118.json).

The seven project definitions have trust records with no explicit disabled
overrides; no trust hashes were changed or exported. All generated configuration
and hooks are byte-identical between the captured clone and the integrated
proposed tree. JSON/TOML parsing and shell syntax for every generated command
are rechecked. Automatic public checkpoint pushing, telemetry and Brain egress
remain disabled; no private SaaS session or transcript is attached/published.

`/home/catow/GIT/decepticon_new` remains the canonical OSS checkout. The separate
`/home/catow/GIT/entire/Decepticon` is the configured local-tool validation clone;
this qualification neither moves the canonical repository nor overwrites its
existing dirty work. No application code, package version or deployment changes.
