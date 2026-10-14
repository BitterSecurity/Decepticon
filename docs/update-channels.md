# Update channels

Decepticon publishes two update channels, modeled on
[Claude Code's release channels](https://code.claude.com/docs/en/setup#configure-release-channel).
**Both channels deliver only _final_ releases** (never pre-releases /
betas). The difference is a **bake/soak delay**, not pre-release inclusion.

| Channel | Tracks | GHCR tag | Default |
|---------|--------|----------|---------|
| **stable** | The newest **final** release that has baked for at least the soak window (default 7 days). | `:stable` | ✅ default |
| **latest** | The newest **final** release, immediately. | `:latest` | opt-in |

So `latest` gets every release the moment it ships, and `stable` adopts it
about a week later — long enough to surface regressions before
conservative users see it.

> **Claude Code parity, with one deliberate difference.** The *semantics*
> match Claude Code (soak model, final-only). Claude Code defaults to
> `latest`; Decepticon defaults to **`stable`** because it is an autonomous
> offensive-security tool where a conservative default is the safer choice.

`latest` is **not** "the main branch." It only moves when a release is
cut — not on every commit. (To track a git branch's config instead of a
release, use the separate `DECEPTICON_BRANCH` override.)

## Selecting a channel

The channel lives in `DECEPTICON_CHANNEL` in `~/.decepticon/.env` (default
`stable`; unrecognized values resolve to `stable`):

```bash
# ~/.decepticon/.env
DECEPTICON_CHANNEL=latest
# optional — override the stable soak window (days):
DECEPTICON_STABLE_SOAK_DAYS=7
```

Three ways to set it:

- **At install** — `CHANNEL=latest curl -fsSL https://decepticon.red/install | bash`.
- **In `.env`** — `DECEPTICON_CHANNEL=stable|latest`. The launch-time
  self-update and `decepticon update` both honor it.
- **Per command** — `decepticon update --channel latest` (one run only).

Pinning an exact version still wins over the channel: `VERSION=1.2.0
curl … | bash` (install) or `DECEPTICON_VERSION=1.2.0` (compose).

## How it works

On `decepticon` or `decepticon start`, a strictly newer release on the selected
channel offers **1. Update now** and **2. Skip**. **Skip is selected by default**;
press Enter to continue with the installed version. Skipping does not apply
update changes to the launcher, configuration, or Docker images. Update now
uses the verified update flow and restarts the launcher.

The notice includes the selected channel. A release visible on `latest` may
not yet be offered on `stable` because of its default seven-day soak window.
No choice is shown when the installed version is current or newer, for dev
builds, or when the release check fails. Non-interactive launches show only
a notice for newer releases and never install an update.

Unset `AUTO_UPDATE`, `prompt`, and legacy `true` / `1` / `yes` / `on` values all
require this choice. `AUTO_UPDATE=false` (also `0` / `no` / `off`) disables
the check; `--no-update` disables it for one launch. Explicit
`decepticon update` remains available.

- **Launcher self-update / `decepticon update`** (`internal/updater`):
  - `latest` → GitHub `…/releases/latest` (newest final; the endpoint
    already excludes pre-releases and drafts).
  - `stable` → lists `…/releases` and picks the newest **final** whose
    `published_at` is at least `DECEPTICON_STABLE_SOAK_DAYS` (default 7) in
    the past, by SemVer. If nothing has soaked yet, it falls back to the
    newest final so stable always resolves.
- **Install** (`scripts/install.sh`) resolves the same way (the stable
  date filter needs `python3`; without it, it falls back to the newest
  final).
- **Docker image tags**:
  - `:latest` is moved to every **final** release immediately by the
    release workflow's `publish-release` job.
  - `:stable` is moved to the newest soaked final by the **scheduled**
    `promote-stable.yml` workflow (daily), *not* on release.
  - Pre-releases move neither tag.
- **Compose fallback.** When `DECEPTICON_VERSION` is unset, the images in
  `docker-compose.yml` fall back to `:stable` (the conservative default).
  In normal operation the launcher pins `DECEPTICON_VERSION` to a concrete
  version, so the fallback is only a safety net for direct `docker compose`
  use.

## Notes

- Pre-releases (`vX.Y.Z-rc.N`) are never auto-selected by either channel.
  Install one explicitly with `VERSION=…` if needed.
- The channel is independent of `AUTO_UPDATE` (whether startup checks for
  updates) and `DECEPTICON_BRANCH` (track a git branch's config
  instead of a release tag).
- Switching from `latest` to `stable` will not downgrade an
  already-installed newer version (updates are forward-only); stable simply
  stops advancing until the soaked stable catches up.
