# Entire with GitHub

GitHub remains the source of truth for code, PRs, branch protection and releases.
Use the existing `/gh/BitterSecurity/` Entire mirrors; do not detach them into
Entire-native repositories. The older SaaS migration proposal is superseded by
this workflow. Keep `origin` pointing at GitHub. An optional `entire` remote uses
`entire://aws-ap-southeast-1.entire.io/gh/BitterSecurity/Decepticon`.
Push a task branch through either remote, then open its GitHub PR. Entire mirror
pushes forward to GitHub and preserve GitHub permissions and protections.

## Setup

Install Entire from https://github.com/entireio/cli/releases/latest and verify
the release checksums. This setup was checked with stable 0.13.4 (2026-11-13).
It fixes the 0.13.3 pre-push panic with Git worktree configuration.
Run `entire agent-help` before selecting CLI flags.
After checkout, run `entire enable --agent codex --telemetry=false` to install
local Git hooks; committed settings alone do not install these hooks in clones.
Run `entire status` and `entire doctor`. Codex discovers project hooks from the
primary checkout, so it must contain the committed `.codex/hooks.json` too.
If hook trust is requested, review it using Codex `/hooks`; do not write trusted
hashes manually. Restart agent sessions after installing or approving hooks.
Do not copy settings into someone else's dirty primary checkout to bypass this.

## Parallel work

Create one branch/worktree per task from freshly fetched GitHub main. Start each
agent with the intended worktree as cwd. Keep commits and index operations owned
by that worktree. The generated hooks include SubagentStart and SubagentStop;
Entire records child-session evidence and checkpoints alongside code commits.
Use `entire search` / `entire checkpoint explain` to retrieve relevant prior
work instead of copying complete transcripts into every agent's context.
Use `entire session` help before explicit resume/adoption; never attach another
active task's session. Do not import all past session history by default.
Entire does not schedule agents, resolve concurrent edits, or automatically
reduce tokens. Measure useful retrieval, capture health and token usage before
claiming a performance improvement. Keep existing local verification and shared
build-slot requirements. Do not add hosted CI or paid review agents for setup.

## Checkpoint privacy

Public OSS checkpoint uploads are disabled in project settings. Keep them local;
to share later, explicitly select a separate private checkpoint repository using
`entire configure --local --checkpoint-remote github:OWNER/PRIVATE-REPO`
and verify the destination before enabling uploads.
Review outgoing data with `entire checkpoint list --outgoing` and
`entire checkpoint explain <id>`. Checkpoints contain prompts/tool output;
redaction is best effort. Code mirroring and checkpoint uploading are different.

## Qualification on 2026-11-14

Both official Linux amd64 0.13.4 archives passed release SHA-256 checks.
The CLI and git-remote-entire helper were upgraded together, retaining backups.
`git ls-remote entire refs/heads/main` matched freshly fetched GitHub main:
SaaS `3c2b324f0ede54da7bc85076c3c1dd3c9c87c6ed`, OSS
`4cecf55b9272e50f97324fd560e8c7daaad7bc1f`.
Generated settings and hooks were inspected. The official tagged Codex source
includes child-session exact-ID matching and excludes inherited parent history;
these upstream tests were inspected, not executed locally.
Existing dirty primary checkouts lack the new project hooks, so current sessions
are not qualified as captured. Project setup becomes discoverable once the
primary checkout contains this change and hook trust is reviewed. No live
multi-agent speed/token benchmark or retrospective session import was performed.
This maintenance change does not alter application code or require an app build.

Sources: https://github.com/entireio/cli/tree/v0.13.4,
https://github.com/entireio/cli/releases/tag/v0.13.4,
https://github.com/entireio/cli/blob/v0.13.4/docs/security-and-privacy.md.

Independent fresh clones were also checked: both discover the committed Codex
hooks at their own root; Git hooks pass doctor; OSS uploads stay disabled.
Both report seven hook approvals required via Codex `/hooks`. No trusted hashes
were edited. Each task branch was pushed through the Entire Singapore mirror;
the GitHub ref API returned the identical commit, proving code forwarding.
JSON settings, all seven shell command syntaxes and git diff whitespace pass.
Live session/subagent capture remains pending interactive hook approval.
