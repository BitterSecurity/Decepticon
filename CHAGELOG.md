# Decepticon version history

All notable changes to the Decepticon project. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [Semantic Versioning](https://semver.org/) from `1.0.0`
onward (the `0.x` cycle is pre-stable per the core/framework/sdk split
design spec, §13.4).

Before opening or updating a PR, record its changes and PR link under
**Unreleased**. An open linked PR means proposed work; a merged linked PR means
integrated but unreleased work. Only dated release sections describe shipped work.
Maintain this single root `CHAGELOG.md`, preserving the full version history.
At release, move merged entries into a dated version section and add a comparison link.
Keep proposed and unmerged changes out of released sections.

## [Unreleased]

- Proposed OpenShell host-provider guard ([#1123](https://github.com/BitterSecurity/Decepticon/pull/1123)): allow the trusted host to require native execution, reject a run-config downgrade to HTTP, and keep graph-construction backends unbound until a real admission resolves. Bind background-job notifications to the current runtime without sharing notification state across engagements. No default provider switch.

- Proposed ([#1118](https://github.com/BitterSecurity/Decepticon/pull/1118)) — restrict domain wildcards to one DNS label across RoE and research scope checks. `*.example.com` permits `a.example.com`, while `a.b.example.com` needs an explicit rule. Exclusions use the same depth. Filter automatic passive enrichment to the same one-label wildcard boundary so discovery cannot add deeper hosts or the apex. Keep explicitly provided apex rules and stop Python/Go creation builders from adding an apex for wildcard-only input. Existing saved RoE entries are not rewritten. Validate fixtures with the selected formatter. Review existing recursive exclusions before adopting this source revision: deeper exclusions must now be explicit. Consolidate full history and references into `CHAGELOG.md`. Source change only; SaaS/Desktop pins and releases are unchanged.
- Proposed test-lane cleanup ([#1121](https://github.com/BitterSecurity/Decepticon/pull/1121)): separate externally provisioned OpenShell/Neo4j tests behind explicit `--live`, require selected live services to be configured and healthy, consolidate duplicate tmux parser assertions and move framework boot checks out of SDK-only tests. Isolate benchmark unit tests from real stack health/cost calls; restore collected RoE network tests after removing unused legacy fixture methods. [Test structure](docs/testing.md). No production runtime change.

- Proposed opt-in OpenShell session transport ([#1120](https://github.com/BitterSecurity/Decepticon/pull/1120)): route the retained tmux/daemon contract through an admitted OpenShell SDK session, with per-request admission checks and no direct daemon HTTP fallback. Preserve existing defaults; keep the live listener override compatible with the HTTP server protocol; add stock-runtime tests for controlled network policy, sessions, interactive input, jobs and files. [Validation and remaining production gates](docs/validation/openshell-session-adapter-20261026.md). Depends on the agent-stack refresh; no E2B driver or production rollout.

- Proposed agent-stack refresh ([#1119](https://github.com/BitterSecurity/Decepticon/pull/1119)): move the framework to Deep Agents 0.7.32, LangChain 1.4.7 and LangGraph 1.2.17. Preserve per-engagement filesystem/transport selection using task-local bindings after upstream removed backend factories and `_get_backend`; keep recursive delete unexposed until it has the same scope/RoE contract. Consolidate the complete version history into root `CHAGELOG.md` and update references. [Compatibility and validation](docs/validation/agent-stack-20261026.md). Source change only; no package release or SaaS deployment.

- Released v2.1.1 — 2026-10-26: the sandbox image build fix (PR #1109) completes the v2.1.0 publication that was stopped on the Kali base-image drift (pip uninstall-no-record-file). The intermediate v2.1.0 PyPI wheels are immutable and remain; the v2.1.0 GitHub draft was superseded and never announced.

## [2.1.1] — 2026-10-26

- Published from `8fdb3bb2f636abc3636ec310ea7dd273c6897f9d` at 07:16:10 UTC after the sandbox image build fix was verified passing in PR CI and the release workflow (sandbox amd64+arm64 built, manifests signed, SBOMs generated, images verified).

- Released (design): record the AutoBot repo-migration decision and execution
  plan — Phase 1 landed items reconciled (PRs #1101–#1107), the approved
  deviation for the optional decision detection adapter (typed classification,
  opt-in, fail-closed; re-scoped to the triage tier once the Sigma/OpenSearch
  substrate lands), and the three-stage "full independence" migration:
  bootstrap the AutoBot repository with the rebased WIP package (2a), move the
  sensor stack and a blue-only agent runtime (2b), then remove the in-tree
  blue_cell code from Decepticon while keeping the URL-based client surfaces
  and the blue.json interop contract (2c). Design:
  [docs/design/2026-10-23-autobot-redesign.md](docs/design/2026-10-23-autobot-redesign.md) §0.

- Released (TUI): fix the engagement-scoped Blue sensor commands — `compose`
  interpolates the whole merged model even for `ps`/`down`, so `/blue status`
  and `/blue stop` failed under an engagement with "required variable
  BLUE_ENGAGEMENT_DIR is missing a value" (the overlay declares it required).
  The CLI now supplies the Blue workspace root on every engagement-scoped
  compose invocation that lacks it. Found by driving the real TUI through a
  PTY; [live TUI verification](docs/verification/blue-tui-live-20261026.md)
  covers create/status/up/stop against the actual Ink process.

- Released (Blue): pluggable detection adapters and incident batching for the
  local sensor. `BLUE_DETECTOR` selects `rules` (the default regex/Sigma-ish
  matcher, unchanged) or `decision` — a typed classification pass over each
  ingested page where every event gets exactly one verdict
  (`attack`/`suspicious`/`benign` with severity and bounded rationale, in the
  spirit of a typed decision model); malformed or unknown verdicts are dropped
  individually and a non-array response fails the page. The blue sensor compose
  files pass the variable through, and the decision adapter requires the Blue
  agent (fail-closed at startup). Repeat hits of the same rule inside
  `BLUE_DEDUP_WINDOW_SECONDS` (default 60) collapse into one incident with an
  incremented `evidence.hits` counter, so scan traffic no longer opens one
  incident per request. Engagement-creation surfaces (`start_engagement` MCP
  tool, `decepticon-cli scan --blue-target`) gain an optional blue block that
  materializes the reviewed `blue.json` (fail-closed, never overwrites).
  [Live verification: decision adapter + batching](docs/verification/blue-detector-adapters-20261026.md).

- Released (AutoBot split — Decepticon side): add the shared **contract layer** in
  `decepticon-core` that the defensive **AutoBot** package (shipped separately in
  [BitterSecurity/AutoBot](https://github.com/BitterSecurity/AutoBot), depending on
  `decepticon-core`) builds on, and make the existing Blue sensor path
  source-agnostic. Contract-layer only here (pydantic + stdlib, no runtime deps);
  **no behavior change to existing agents**. Design:
  `docs/design/2026-10-23-autobot-redesign.md` (+ intent, blue-cell-analysis).
  - `decepticon_core.types.ocsf_event` — OCSF-aligned normalized event envelope
    (`OcsfEvent`, `SignalType`, `SeverityId`, `Observable`, `Metadata`,
    `PROMOTED_FIELDS`); preserves `raw_data`/`unmapped`, exposes source-agnostic
    promoted query keys.
  - `decepticon_core.types.event_store` + `protocols.event_store.EventStoreProtocol`
    — swappable, source-agnostic event-store query contract (no proxy/target enum).
  - `decepticon_core.protocols.coverage.CoverageAdapterProtocol` — pluggable
    per-environment log coverage contract.
  - Drop the hardcoded two-source enum from the Blue timeline/metrics path so
    arbitrary coverage sources are queryable; receiver `/metrics` gains
    source-agnostic `events_by_source` (legacy keys kept for backward compat).
  - Public-API surface 74 → 87. (The AutoBot runtime — OpenSearch store, coverage
    adapters, Sigma detection, triage — lands in the AutoBot repo once this core
    contract merges.)

- Released (design): Blue engagement configuration — a declarative, reviewed
  `blue.json` in a Blue-owned workspace root (`~/.decepticon/blue/<label>`,
  kept outside the Red engagement workspace so the attacking agent cannot
  reach Blue's observation scope, rules and sensor layout by filesystem
  coincidence; purple disclosure stays an explicit, logged opt-in for later)
  holding targets, logs, rules file and watch settings, materialized at
  creation time by a shared fail-closed module mirroring `plan/roe.json`
  semantics, consumed by `/blue up`, with engagement-addressable sensor
  state. Design review document:
  [docs/design/2026-10-26-blue-engagement-configuration-design.md](docs/design/2026-10-26-blue-engagement-configuration-design.md).
- Released (Blue): make the local sensor's detection rules operator-editable —
  `BLUE_RULES_FILE` loads a jsonl rule set in the bootstrap / Sigma-ish subset
  that `decepticon.blue_cell.rule_match` accepts (with `compose.rules.yaml` as
  an overlay and `rules/sample-web-rules.jsonl` as the shipped example); an
  unreadable or invalid file stops the monitor instead of silently keeping the
  built-in web rules, which remain the default without the variable. The
  autonomous watch and investigation prompts now name the defended target id
  and the loaded rule set, and the Blue agent treats that as scope context.
  [Live-stack verification](docs/verification/blue-rules-file-20261026.md).
- Released (TUI): surface the Blue agent's autonomous assessment **inline in the
  Blue feed** — Blue runs continuous observation, so the assessment lands in the
  conversation when it is produced instead of only when the operator asks. The
  bounded reasoning slice appears on `assessment`/`watch_alert` notifications
  (full text stays in `/alerts`), and the Blue monitor is instructed to write
  its watch summaries and investigation responses in English. Raw rule
  detections (`detected`) no longer enter the conversation feed — under scan
  traffic they would flood it — and remain viewable via `/alerts` and the
  unread badge while the feed carries the agents' conclusions and operational
  signals only.
  [Live-stack verification](docs/verification/blue-assessment-inline-20261026.md).

- Released (TUI): scope Blue Cell event notifications to **Blue mode only** so they
  no longer invade the Red (or Plan) conversation. `useBlueNotifications` takes a
  `surface` gate; the REPL passes `() => assistantId === "blue_cell"`, so Blue
  detections/incidents are injected into the feed only while in Blue mode. Events
  still accumulate in state regardless of mode, so they remain viewable in Blue
  mode via `/alerts` and the unread badge. Default mode stays Red.

- Released (Blue): implement engagement-scoped Blue target configuration —
  `blue.json` materialized in the Blue-owned workspace root
  (`~/.decepticon/blue/<label>`, `DECEPTICON_BLUE_WORKSPACE` override) by a
  shared fail-closed module (`blue_cell/engagement_config.py`) with strict
  rule-file validation at creation and never-overwrite semantics for reviewed
  configurations. New `decepticon-cli blue-engagement create|env` surfaces and
  TUI `/blue engagement create` (dry-run preview, `--confirm` to write);
  `/blue up` consumes the reviewed config as defaults (explicit arguments
  override for one run), and the compose project gains an engagement suffix so
  containers, volumes and networks are isolated per engagement. Ad-hoc usage
  without an engagement is unchanged. Design review:
  [docs/design/2026-10-26-blue-engagement-configuration-design.md](docs/design/2026-10-26-blue-engagement-configuration-design.md) (PR #1104).

- Released [PR #1100](https://github.com/BitterSecurity/Decepticon/pull/1100): record actual 2.0.0 publication, candidate launcher/model and WebSocket acceptance, preserved sessions/files/ports, signed distributions and installation update. Reconcile merged changes into the dated release history; the immutable release tag and runtime versions remain unchanged.

- Merged, unreleased [PR #1090](https://github.com/BitterSecurity/Decepticon/pull/1090):
  make LiteLLM and LangGraph healthcheck startup grace honor the launcher’s
  validated `DECEPTICON_STARTUP_TIMEOUT_SECONDS` budget (default 600 seconds).
  Preserve readiness probes, overall wait failures, post-start retries, data
  and credentials. Share launcher/opscontrol budget normalization; integrate
  with current main without selecting a release or changing downstream pins.
  [Integration verification](docs/verification/startup-budget-integration-20261026.md).

- Released [PR #1109](https://github.com/BitterSecurity/Decepticon/pull/1109): fix the sandbox image build — the Kali rolling base began shipping an apt-managed Python certifi without a RECORD file, and pip's certifi upgrade aborted with uninstall-no-record-file; the sandbox pip layer now uses --ignore-installed (packaging defect patch per RELEASE.md).
## [2.0.0] — 2026-10-26

- Published from `dd5b43c01bbc6ac09615a98444afcd2a4664480e` at 00:22:53 UTC after source and downloaded-candidate qualification. See the [qualification record](docs/releases/2.0.0-qualification.md) and [release](https://github.com/BitterSecurity/Decepticon/releases/tag/v2.0.0).
- **Major command change:** `/resume` selects saved sessions and `/resume <thread-id>` restores one; the prior `/resume [message]` pause-feedback meaning is replaced. Send ordinary input to continue a paused session; use `/answer` for a pending question. Existing engagement, conversation and database formats are retained; no reset or migration is required. Upgrade with `decepticon update --channel latest`, then reopen the OSS CLI. Source version fields retain the release-stamped `0.0.0` sentinel; SaaS versions are independent.

- Released [PR #1099](https://github.com/BitterSecurity/Decepticon/pull/1099): stop the search-closure middleware from turning greetings, operator questions and read-only status answers into unsolicited assessment runs. Track actual assessment work per interactive turn; preserve pending objectives and completed receipts across conversation, while retaining receipt/review/budget gates for assessment tools, checkpoint resume and explicit autonomous search requests. New checkpoint state is runtime-owned and optional; no data migration or workflow-pin reset is required.

- Released [PR #1098](https://github.com/BitterSecurity/Decepticon/pull/1098): isolate two no-credential resolver tests from ambient OAuth intent and provider settings using the existing credential scrub helper. This fixes local release verification on configured developer machines; shipped authentication behavior and credentials are unchanged.

- Released [PR #1097](https://github.com/BitterSecurity/Decepticon/pull/1097): add a fixed fullscreen terminal conversation with mouse selection, composer caret placement, wheel/keyboard history scrolling and tool expansion. `/resume` now selects saved sessions and restores without dispatching a model turn; send new input to continue a paused conversation and use `/answer` for a pending question. Stabilize the port-recovery HTTP fixture against ephemeral-port reuse in macOS CI. Clarify the session/runtime footer and apply graphite/silver styling to inherited text and previously unthemed first-launch Go prompts. Existing saved conversations and scope checks remain compatible. Use `--no-alt-screen` or `/tui inline` for native scrollback and `--no-mouse` or `/mouse off` for native selection; no data migration is needed.

- Released [PR #1096](https://github.com/BitterSecurity/Decepticon/pull/1096): refresh approval from current trusted, bound launch configuration on resume, including revocation. Separate direct scope from Autohunt bootstrap wording, expose canonical OPPLAN objective enums/integer priorities and treat greetings as conversation rather than mission bootstrap. Stop replaying main-model failures as compaction failures; context-check status completes before generation. Existing scope gates, revision checks and saved workspace files remain intact; no data reset is required.

- Released [PR #1095](https://github.com/BitterSecurity/Decepticon/pull/1095): recognize upstream cybersecurity/content refusals inside HTTP 500 and fallback wrappers. Show a compact policy/access explanation instead of authentication-priority advice or router dumps; distinguish OAuth subscription access from API-project Daybreak approval. Existing model selection, safeguards and access settings are unchanged; no migration is required.

- Release record: document actual 1.6.1 publication, immutable distributions,
  measured port recovery, neutral onboarding and same-volume WSL acceptance
  ([PR #1094](https://github.com/BitterSecurity/Decepticon/pull/1094)).
  This post-tag documentation does not move the release tag or change runtime versions.

## [1.6.1] — 2026-10-25

- Published from `8c6c0fdccf59f53ad621493c2fb6481be297ef72` after source and
  downloaded-candidate qualification. See the [qualification record](docs/releases/1.6.1-qualification.md).
  Upgrade with `decepticon update --channel latest`; no database reset is needed.
  Port-conflict recovery can change host URLs and saves the selected values.

- Released [PR #1093](https://github.com/BitterSecurity/Decepticon/pull/1093): use example.com/decepticon.red engagement examples, query passive sources concurrently under one deadline and keep partial results with concise timeout guidance; preserve literal wildcard targets in the scope review. Unify the Ink CLI/TUI and Go launcher's branding, controls, activity, agents and Markdown around graphite/silver surfaces, neutral focus edges and explicit status glyphs. Existing commands, scope approval and persisted data remain compatible; external discovery availability is still best-effort.

- Released [PR #1092](https://github.com/BitterSecurity/Decepticon/pull/1092): preserve the web terminal's internal port 3003 when host publications are remapped, and pass the selected web host port to its origin checks. Browser runtime endpoints continue using host ports. This compatible follow-up to #1091 requires no data migration.

- Released [PR #1091](https://github.com/BitterSecurity/Decepticon/pull/1091): recover Docker-confirmed loopback TCP port conflicts during launcher startup by trying higher ports and atomically saving the chosen values. Rewire host probes, CLI/web connection hints and credential migration; keep internal service addresses, credentials, volumes and the existing port owner intact. Custom hardcoded bindings and exhausted ranges remain actionable errors. No manual migration is required; persisted host URLs can change after a conflict.

- Released [PR #1089](https://github.com/BitterSecurity/Decepticon/pull/1089),
  following #1088: audit all 295 configured/picker model routes as of 2026-10-25 KST,
  with per-route sources, explicit unresolved contracts and redacted managed
  identities. Correct 17 reviewed model/route output defaults (including AWS's
  Sonnet 4.6 exception); preserve positive global overrides. Local/custom
  deployments now use server output defaults unless explicitly overridden,
  which may shorten responses. Remove three embedding-only models from the
  CLI agent picker while retaining their embedding routes. Includes local HTTP
  regressions and a [full audit](docs/model-contract-audit-2026-10-25.md);
  provider-wide inference remains explicitly unverified; release qualification is recorded separately.
  Honor positive role-assignment output caps on primary/fallback clients and
  preserve them across desktop/role model overrides; global caps take priority.
  Existing configured role caps now take effect instead of being ignored.
  Follow-up checks resolve 76 previously unverified output contracts/policies,
  with explicit account/registry limitations for the remainder. Honor exact
  Kilo/Vercel sampling exclusions even on invocation kwargs/extra request bodies,
  preserving caller-owned dictionaries. Fix three MiMo
  routes to use the inference API host while preserving IDs and explicit dynamic
  base overrides. New automatic Groq defaults use current GPT-OSS models after
  free/developer Llama retirements; explicitly selected legacy IDs remain intact.
  Use original publisher specifications for identified models across hosts,
  recording separate hosting constraints and access checks. All 25 former
  identity-only entries now reference publisher specs; NVIDIA Nemotron 70B's
  official 4K output limit corrects the default to 4000 across exact aliases.
  Automatic Google API defaults use current GA 3.8 Flash/3.5 Flash-Lite for
  new-project eligibility; explicit legacy and subscription IDs are preserved.

- Released [PR #1088](https://github.com/BitterSecurity/Decepticon/pull/1088)
  (fixes #1087): resolve Claude output ceilings by model/version across provider,
  dotted, dated and Bedrock IDs. Opus/Sonnet 4.5 default to 64000; newer supported
  models keep 128000 and Haiku 5.5 gains its supported 128000 default. Align
  Haiku/dotted 5.5 thinking replay and sampling exclusions with existing catalog
  policy. Preserve positive global overrides, route identities and saved state;
  unknown models retain the 64000 fallback. Includes a [related-path audit](docs/model-output-limit-audit.md) and
  actual local HTTP request regressions. Released in 1.6.1 without replacing 1.6.0 artifacts; no data migration is required.
  Provider-wide inference is not claimed.


- Release record: document the actual 1.6.0 publication, immutable artifacts,
  WSL/desktop upgrade acceptance and known verification limits
  ([#1086](https://github.com/BitterSecurity/Decepticon/pull/1086)).

## [1.6.0] — 2026-10-25

- Release qualification: reconcile the 1.5.4 history and record the integrated
  1.6.0 source, security audits and persistent web state verification. Publication
  passed actual distribution checks
  ([#1079](https://github.com/BitterSecurity/Decepticon/pull/1079)).

- Released [PR #1085](https://github.com/BitterSecurity/Decepticon/pull/1085): fix web CLI draft/cursor writes under the image's non-root account, whose default home is `/nonexistent`. Add a private `web_state` volume and separate default state by engagement and mode; preserve explicit state overrides and existing readable installation state. Verify writable state under the image's actual runtime user during the Docker build. Keep the new volume on upgrade/recreation; conversation checkpoints, workspace files and credentials are unchanged.

- Released [PR #1084](https://github.com/BitterSecurity/Decepticon/pull/1084): close the remaining dependency-audit gaps: patch locked aiohttp/filelock and optional MCP (now requires `>=1.30,<2`); audit every registry package in uv.lock, including optional/dev dependencies, without treating editable `0.0.0` workspace sentinels as published packages. Remove the obsolete click exception. Update the separately locked telemetry Worker tooling/validator and compatible undici patch; preserve schema 1.0 acceptance/limits and the deployed runtime configuration. Add blocking Worker audit/type/test/bundle gates to CI and local quality checks. Worker development requires Node 22.12+; no telemetry service deployment is performed by this source change.

- Released [PR #1083](https://github.com/BitterSecurity/Decepticon/pull/1083): integrate external [PR #1082](https://github.com/BitterSecurity/Decepticon/pull/1082) with current localized Blue notifications. Back off optional-monitor failures to a 30-second cap, reset to normal polling on success and stop retries on unmount. Preserve the ES2022 CLI target and existing non-overlapping pagination; no Blue service is required for ordinary Red/Plan use.

- Released [PR #1081](https://github.com/BitterSecurity/Decepticon/pull/1081): prevent web terminal reconnect loops from deferred StrictMode initialization and obsolete WebSocket callbacks. Only the current connection can update terminal/session state or schedule reconnection; real current-connection failures still recover.

- Released [PR #1079](https://github.com/BitterSecurity/Decepticon/pull/1079): release qualification: integrate the final subscription-effort and
  Compose-discovery and web mount/socket follow-ups, record actual WSL and
  desktop-browser model/scope/upgrade evidence,
  and reconcile the already-published 1.5.4 history against its tag and real
  publication metadata. Quality targets accept `CLI_TEST_ARGS` for bounded
  local test workers. Released as 1.6.0 after final source CI/security and actual candidate
  distribution/product qualification. See
  [qualification record](docs/releases/1.6.0-qualification.md).

- Released [PR #1080](https://github.com/BitterSecurity/Decepticon/pull/1080): align launcher-bound web sessions with the CLI/server engagement mount, while keeping existing web checkpoint namespaces and files intact. Reject sibling engagement connections and stale model/tool dispatch after a runtime rebind; shared-root Compose and native policy adapters retain their existing behavior.
- Released [PR #1077](https://github.com/BitterSecurity/Decepticon/pull/1077) (issue #1044): subscription effort routing correction: allow the
  reasoning-effort parameter on Codex OAuth and Claude subscription routes
  whose custom handlers already translate it. Declare the Claude allowlist in
  the local model overlay and regenerate its YAML, preserving the shared
  catalog drift gate. Fix the actual gateway HTTP 400
  seen with per-session/host-selected effort; keep ordinary provider validation,
  default effort and scoped credential handling unchanged. No account migration.

- Released [PR #1074](https://github.com/BitterSecurity/Decepticon/pull/1074): dependency security refresh: update the workspace lockfile and
  Next.js/Sharp/test-runner versions to patched releases; remove unused OpenUI
  packages (the shipped chat uses its existing Markdown renderer). The npm
  audit falls from 42 findings, including 4 critical, to zero without advisory
  suppression. Add a blocking high-severity npm audit to CI/local quality gates.
  Bound the real PTY startup regression check to measured cold imports on
  busy hosts (Ink alone took 21.5 seconds, exceeding its former 15-second
  startup deadline). No credentials, saved conversations or database migration
  are changed.

- Released [PR #1076](https://github.com/BitterSecurity/Decepticon/pull/1076): image build availability fix: use Kali’s official Cloudflare CDN
  package mirror for sandbox and Sliver images after the regional redirector
  repeatedly selected an unreachable mirror. Preserve pinned base images, APT
  signature verification and the existing HTTPS bootstrap; no runtime or data
  migration changes.

- Released [PR #1078](https://github.com/BitterSecurity/Decepticon/pull/1078): launcher startup correction: allow a bounded 15 seconds for the
  Compose version probe, avoiding false missing-plugin errors when a healthy
  Docker installation takes longer than three seconds to respond. Report
  timeout/cancellation distinctly with a direct diagnostic command. A local
  discovery timeout defers to the actual Compose startup/readiness gates;
  parent cancellation and a genuinely missing plugin still stop startup.
  Saved installation data remains unchanged.

- Released public Claude legacy selection ([PR #1075](https://github.com/BitterSecurity/Decepticon/pull/1075)): expose Opus 4.8/4.7 and Sonnet 4.6 picker metadata and add active Opus 4.6/4.5 public API routes. Keep public identity separate from tenant subscription access; no provider calls, package publication or SaaS deployment is implied.

- Released [PR #1073](https://github.com/BitterSecurity/Decepticon/pull/1073) (issue #1044): add per-session fallback, effort and
  autocompaction launcher flags and checkpointed overrides; show observed model
  routing, fallback attempts, context/compaction checks and cached budget usage
  in the TUI. Preserve default routing, tenant credentials and authoritative
  Desktop model policy. `/doctor` includes saved controls. See
  [configuration and provider limits](docs/runtime-controls.md); no data migration.

- Released [PR #1068](https://github.com/BitterSecurity/Decepticon/pull/1068): TUI Red execution, web
  mode switching and web terminals now accept the mode-less machine scope
  generated by the launcher. All three use the same readiness check, matching
  unconditional runtime enforcement; legacy mode fields remain readable and
  missing or unusable machine scopes remain blocked. Web creation also stops
  writing the retired mode field. Existing valid scopes need no migration.

- Released [PR #1066](https://github.com/BitterSecurity/Decepticon/pull/1066):
  integrate host-scoped Desktop model/tool policy (#1027), native terminal and
  selected-account handoff, and planning-role tool isolation (#1067). Preserve
  existing default CLI behavior and saved policies. Fix qualification checks for
  canonical macOS/Windows host paths and platform-specific permission semantics;
  keep private POSIX modes enforced and format the integrated Python sources.

- Released Desktop tool-scope correction ([#1067](https://github.com/BitterSecurity/Decepticon/pull/1067), follow-up to [#1066](https://github.com/BitterSecurity/Decepticon/pull/1066)): saved external HTTP tool grants no longer expand planning roles or prevent Soundwave's graph from loading. Honor registered role purpose, including custom planners, while preserving execution/observation grants, explicit model routing and stale-handle revocation. No release or version bump is implied.

- Accept a native-selected, bounded regular subscription profile during Desktop service startup, including Windows account paths translated for WSL ([PR #1066](https://github.com/BitterSecurity/Decepticon/pull/1066)). Only the selected provider receives it; unrelated profiles remain unmounted and ordinary CLI home discovery is preserved. This connects native OS authorization custody to the existing provider adapters without sending account material through the renderer.

- Tag internal search-continuation directives in message metadata so native transcript hosts can separate runtime coordination from the operator's own messages ([PR #1066](https://github.com/BitterSecurity/Decepticon/pull/1066)). The agent's search contract and checkpoint content are preserved.

- Released native Desktop runtime integration ([PR #1066](https://github.com/BitterSecurity/Decepticon/pull/1066)): the following service, policy, image, model relay and terminal changes are shipped source changes. See the [native contract](docs/design/desktop-native-terminal.md); native Windows/macOS Desktop acceptance is not claimed.

- Include the validated native policy revision in the agent service environment, so applying changed agent/tool settings reconstructs cached graphs during idle startup instead of leaving old policy handles active. Missing, oversized, redirected or mismatched owner policy files fail before Compose.

- Native terminal startup reads inherited tmux status settings with an exact session/window target. Real sandbox qualification caught that a session-only target was rejected by show-options, preventing service registration despite healthy containers.

- Desktop Ollama startup accepts only a native-provided private-IP relay endpoint and labels its owned model proxy independently from the terminal sandbox. This supports Linux loopback model access through the native host without exposing Ollama publicly. Desktop mounts only the selected subscription's credential file; Ollama and API-key sessions do not mount unrelated Codex/Claude account files. Normal interactive CLI credential discovery remains unchanged.

- Native Desktop may supply its locally prepared agent image using the fixed `decepticon-desktop-agent:sha-<source commit>` identity. The private overlay applies it only to the agent service; malformed image references fail before Compose. This lets the packaged native host build its matching pinned runtime without depending on an unpublished registry image.

- Desktop service-only startup now starts the agent/knowledge services and their dependencies without the separate web dashboard or interactive CLI container. Normal CLI start/attach retains its existing topology. This reduces unused local resources while preserving service health checks.

- Integrated Desktop native terminal integration: accept a complete native-issued workspace/engagement/resource identity and nonce only during service startup, label the owned sandbox and return its immutable container identity plus the measured tmux status height. Preserve an existing agent shell, validate labels before publishing the registration and keep policy overlays private. Desktop independently verifies the container and mount before enabling PTY access. Integrates the pending #1027 settings policy with current main; no OSS version/tag or published image change is claimed.

- Released [PR #1027](https://github.com/BitterSecurity/Decepticon/pull/1027)
  Desktop settings policy adapter: host-owned,
  workspace/engagement-scoped model routing and tool approval with explicit
  selected-model failures and revocation checks before dispatch and result
  publication. Existing behavior remains unchanged without the opt-in.
  [Contract and qualification limits](docs/design/desktop-settings-policy.md).
  Runtime pin, native qualification and release remain pending.

- Released [PR #1069](https://github.com/BitterSecurity/Decepticon/pull/1069): update the locked setuptools
  dependency from 83.0.0 to 84.2.1, fixing GHSA-xmj3-9gf7-h52w and restoring
  the blocking dependency audit without suppressing findings. No API or data
  migration is required.

- Released [PR #1072](https://github.com/BitterSecurity/Decepticon/pull/1072): reattach once after an interrupted
  stream using the existing run and event cursor; show reconnect progress.
  Transcript view gains bounded PgUp/PgDn navigation and the normal slash-command
  dispatcher. Existing error dismissal, numbered question answers, contextual
  Blue footer and unused-component cleanup are preserved. Blue notices follow
  the configured English/Korean language, with English fallback. Internal runtime
  directives remain outside the restored operator transcript. No migration required.

- Released [PR #1065](https://github.com/BitterSecurity/Decepticon/pull/1065):
  add read-only in-session `/doctor` diagnostics, including scoped checkpoint ID,
  OPPLAN revision, actual workflow ID/version/SHA pins, pending tasks, runtime
  plugin bundles, machine-scope readiness and budget. Launch selections are
  distinguished from persisted pins; unavailable reads are explicit and do not
  start or mutate runs; checkpoint reads have a shared 10-second deadline. Host `doctor --deep --engagement <name>` adds scoped KG
  revision/node counts, sandbox HTTP liveness/advertised API probes and per-role
  fallback-slot readiness. Addresses
  issue #1040; no migration is required.

- Released [PR #1071](https://github.com/BitterSecurity/Decepticon/pull/1071): use the complete `tsx` loader for
  the actual PTY CLI process, preserving CommonJS JSON dependencies. Fixes CLI
  startup exiting with a JSON parse error under `tsx/esm`; no dependency or
  workspace migration is required. A real WebSocket-to-PTY startup regression
  check verifies the connected composer and absence of agent run creation,
  with an explicitly interactive PTY child even when its parent runs in CI.

- Released [PR #1070](https://github.com/BitterSecurity/Decepticon/pull/1070): document the shared engagement/thread/
  run binding, lifecycle mappings, transcript and SSE cursors, authorization and
  adapter acceptance for CLI/MCP and downstream Desktop Local/Cloud. Clarify that
  background preparation is not agent execution and attaching does not duplicate
  runs. Documentation only; no API, storage or migration change.
## [1.5.4] — 2026-10-23

Published at `2026-10-23T05:59:20Z` from tag commit
`3eb96117da314e0387c04086a3bd5ea005a06470`. These entries were carried
under Unreleased in that tag and are reconciled here using source ancestry;
1.5.3 remained an unpublished draft. SaaS deployment and native Desktop
acceptance are separate from this OSS publication.

- **Breaking** (SAST entry removal, [issue #1059](https://github.com/BitterSecurity/Decepticon/issues/1059)): the standalone
  static/CI code-scanning surface is removed — `decepticon-cli scan`
  loses `quick` mode, `--scope-mode diff`/`--diff-base`, `--sarif-output`
  and `--fail-on`; filesystem-path targets are rejected with a pointer to
  the migration; the `.github/actions/decepticon-scan` composite, its
  example workflow and the CI/CD integration docs are deleted; and the
  path-scoped engagement machinery (`_is_path_target`, the empty-`in_scope`
  RoE branch and `build_engagement_roe`'s `require_scope` escape hatch)
  is gone, closing the empty-allowlist egress fail-open at its source.
  `decepticon-cli scan` remains as the headless DAST entry for network
  targets (`standard`/`deep`), and code analysis inside an engagement —
  analyzing source the operation itself obtains — is unchanged. Findings
  are read through `decepticon_engagement_findings` (MCP) or the
  workspace graph instead of a local SARIF file.

- [PR #1046](https://github.com/BitterSecurity/Decepticon/pull/1046):
  add read-only runtime diagnostics — `decepticon doctor --deep` reports the
  middleware slot matrix for every role (KG/HITL/model_fallback previously
  failed silently), KG reachability, Skillogy health and MCP auth mode.
  Addresses issue #1040.

- (launcher scope recommendations): engagement creation now runs
  passive discovery — certificate-transparency logs (crt.sh) and passive DNS
  (HackerTarget, both keyless) — against the first hostname target and
  suggests in-scope entries through a pre-checked multi-select the operator
  can trim, plus a free-text field for anything the sources missed. Results
  are filtered to the target's own DNS zone (lookalikes never match),
  normalized through the same pipeline as manual input, and the step is
  strictly optional: a timeout or source failure folds back to the typed
  targets with a note, so creation never blocks on the network.

- [PR #1058](https://github.com/BitterSecurity/Decepticon/pull/1058): mechanical engagement creation across the Go launcher. The
  launcher now generates the same machine-enforceable `plan/roe.json` the MCP
  bridge and CLI scan produce, from a broadened creation grammar — multiple
  targets (URLs, hostnames, CIDRs, `*.domain` globs) plus an optional
  out-of-scope denylist — pinned to the core builder by shared golden test
  vectors. The `.red-approved` approval marker and the Red-approval ceremony
  (`/mode red approve`, the web plan-approval POST, `provision_roe
  --approve-red`) are retired: creating an engagement with a declared scope is
  the authorization (the operator owns it), the RoE middleware enforces the
  scope on every tool call, and readiness across the launcher picker, TUI Red
  gate and web dashboard is "an enforced machine scope exists". Legacy
  dual-form RoE documents remain readable. The creation flow reviews the
  generated scope before it is committed — a normalized preview (in-scope
  entries, denylist, and dropped patterns) requires an explicit Create
  confirmation — and the engagement picker shows each engagement's scope
  summary (in-scope/denied counts and first target). Source only, unreleased.

- [PR #1051](https://github.com/BitterSecurity/Decepticon/pull/1051):
  structured tool errors — the ops tools report failures as
  `{is_error, tool, cause, recovery}` envelopes built by
  `decepticon.tools.errors.tool_error`, and the TUI routes tool results
  that carry that envelope to the structured error display (cause and
  recovery rendered separately) instead of dumping raw JSON. Removes an
  internal design note accidentally committed at the repository root.
  Source only, unreleased.

- [PR #1038](https://github.com/BitterSecurity/Decepticon/pull/1038):
  TUI slash-command UX parity — completion, suggestions, /help grouping.
  Path completions for `/file` keep the typed directory prefix, so
  `sub/bri<tab>` completes to `sub/brief.md` and `~/`-relative suggestions
  stay home-anchored instead of collapsing to a bare entry name.

- [PR #1039](https://github.com/BitterSecurity/Decepticon/pull/1039):
  TUI error-recovery polish — esc-to-dismiss error banners, number-key question
  selection (including the "Other" free-text entry, previously only reachable with
  the arrow keys), a Blue footer that only renders while monitoring is relevant, removal of
  dead client code (SessionPicker, SpinnerIcon) and English blue notification templates.
  Auto-reconnect and transcript interactivity are deferred as designed follow-ups.
  Partially addresses issue #1034.

- [PR #1050](https://github.com/BitterSecurity/Decepticon/pull/1050):
  page `assistants.search` in the MCP `decepticon_list_graphs` tool so every
  exposed graph is returned. The LangGraph SDK defaults to `limit=10`, which
  silently hid agents (`recon`, `soundwave`, the `decepticon` orchestrator, …)
  on installs shipping more than ten graphs — the `standard` bundle ships 22.
  Source only, unreleased.

- [PR #1054](https://github.com/BitterSecurity/Decepticon/pull/1054):
  surface budget usage in the REPL footer from BudgetMiddleware's
  `budget_warning` custom event. The footer line is extracted into a
  `BudgetFooter` component so it is testable with real Ink rendering;
  `ink-testing-library` is added to the CLI devDependencies (the test
  suite could not load without it under CI's `npm ci`). Source only,
  unreleased.

- [PR #1053](https://github.com/BitterSecurity/Decepticon/pull/1053):
  emit `compact.start` / `compact.end` / `compact.summarization_skipped`
  lifecycle events from the summarization slot so a silent skip becomes
  observable. The emitter now writes through `EventLog.for_workspace`
  (the flat `<workspace>/events.jsonl` the dashboard, shutdown.py and
  resume read) and resolves engagement/workspace scope from
  `request.state` then `DECEPTICON_ENGAGEMENT_ID` — mirroring
  `EventLogMiddleware._resolve_scope` — instead of an orphaned
  `engagements/<id>/events.jsonl` keyed on a non-existent env var.
  Source only, unreleased.

- [PR #1057](https://github.com/BitterSecurity/Decepticon/pull/1057): RoE unification phase 1 — engagement creation now mechanically
  generates the enforced `plan/roe.json` from the creation data only (MCP
  `start_engagement`: `targets` + optional `out_of_scope`). Creation targets
  are normalized before they become scope rules: embedded lists split
  (`"a, b"`), URLs and scheme-relative `//host` collapse to their hostname,
  CIDRs normalize to the canonical network address (`10.0.0.5/24` ->
  `10.0.0.0/24`), `host:port` authorizes the host on all ports (the RoE is
  host-scoped), hostnames lowercase / lose the DNS trailing dot / IDNA-encode,
  bracketed bare IPv6 drops the brackets, a `*.subdomain` glob adds its apex
  so the bare domain stays reachable, and patterns no real DNS/IP target can
  match are dropped with a warning. Plain-hostname targets, URL and git-remote
  hosts, and glob base domains are passively enriched with
  Certificate-Transparency subdomains (crt.sh, bounded and root-filtered,
  best-effort — discovery failure degrades to the operator-provided targets
  and never blocks creation). The CLI scan path (`decepticon-cli scan`) now
  generates the same enforced `plan/roe.json` at creation (new
  `--out-of-scope` flag) and engagements whose targets are all filesystem
  paths materialize an empty network allowlist (denylist and forbidden
  destinations still enforce). An engagement with no derivable in-scope
  target is refused at creation (fail-closed) instead of running audit-only.
  The legacy audit/warn/enforce `EnforcementMode` is removed — the RoE is
  always fail-closed enforce, so `machine_enforcement` no longer carries a
  `mode` key and `EnforcementMode` leaves the core public API
  (`CORE_PUBLIC_API` 75 -> 74). Source only, unreleased.

- [PR #1052](https://github.com/BitterSecurity/Decepticon/pull/1052):
  stop mislabelling a LiteLLM cooldown (`RouterRateLimitError` — "No deployments
  available ... cooldown_list") as a provider rate limit. A fatal upstream error
  (e.g. a 404 `model_not_found` from a custom OpenAI endpoint) cools the
  deployment down and resurfaces as a 429; `_reraise_with_actionable_message`
  now names the masked-upstream cause and points at the proxy logs instead of
  telling the operator to add a fallback. Source only, unreleased.

- Review corrections for [PR #1036](https://github.com/BitterSecurity/Decepticon/pull/1036):
  decode cumulative SDK `messages/partial` and `messages/complete` events and
  single message tuples correctly. Retire live text by message ID, ignore late
  finalized snapshots and child namespaces, and clear drafts on pause/completion.
  Keep running-tool previews on one line. No shared web stream-mode change or
  release is included.

- Review corrections for [PR #1035](https://github.com/BitterSecurity/Decepticon/pull/1035):
  retain runtime fallback examples for MCP and infracontrol help, and apply
  command suggestions through the actual startup validator with its existing
  three-edit threshold. Remove the unused parallel classifier; preserve positional resume
  IDs and side-effect-free invalid-argument errors. Isolate the readiness test
  helper from host clipboard-tool discovery without changing product deadlines.
  Source only, unreleased.

- [PR #1035](https://github.com/BitterSecurity/Decepticon/pull/1035):
  finish the launcher CLI docs pass — Long descriptions and Example blocks for doctor,
  update, stop, remove, onboard, skill, mcp, kg-health and serve (logs/status/start landed
  earlier), a root-help recipe block covering session selection and stack management, and
  unknown-command suggestions on the root command (typo'd commands now suggest the nearest
  command name via alias-aware edit distance before falling back to the resume hint;
  root keeps ArbitraryArgs so `--resume` + positional thread ID still work). No flag
  semantics or human output-format changes. Addresses the remainder of issue #1018.

- [PR #1047](https://github.com/BitterSecurity/Decepticon/pull/1047):
  update certifi to 2026.10.19 for PYSEC-2026-4320 / GHSA-6q48-6c7w-9gw2.
  Require the fixed minimum in published package metadata as well as the
  workspace lock so pip installations upgrade vulnerable transitive copies.
  Rebuild container images when released; existing images are not changed by
  this source update. No product version bump or release is included.

- [PR #1036](https://github.com/BitterSecurity/Decepticon/pull/1036):
  render token-level streaming output in the TUI — cumulative AI partials from the
  already-subscribed LangGraph `messages` mode now update a live buffer with a cursor,
  finalized by the matching values snapshot, and in-flight tool calls render as a compact
  `● Tool(args…) …` header. Pure Ink primitives, no platform-specific terminal behavior.
  Addresses issue #1032.

- [PR #1039](https://github.com/BitterSecurity/Decepticon/pull/1039):
  TUI error-recovery polish — esc-to-dismiss error banners, number-key question
  selection, a Blue footer that only renders while monitoring is relevant, removal of
  dead client code (SessionPicker, SpinnerIcon) and English blue notification templates.
  Auto-reconnect and transcript interactivity are deferred as designed follow-ups.
  Partially addresses issue #1034.

- [PR #1045](https://github.com/BitterSecurity/Decepticon/pull/1045):
  bind MCP `decepticon_start_engagement` to an explicit `workspace_path`
  (default `/workspace`, traversal-validated) and return the binding in
  `StartResult`, so coding agents start engagements against the workspace the
  interactive CLI selected. A non-default binding must be the engagement's own
  directory (final segment = `engagement_name`), so one engagement's run can
  never write into another engagement's workspace. Addresses issue #1042.

- [PR #1031](https://github.com/BitterSecurity/Decepticon/pull/1031):
  simplify the README demo area by removing the repeated OSS capability list and
  trailing decorative image. Add acknowledgements for integrated open-source
  projects, adapted code, security references and CLI design inspiration, with
  upstream links and existing attribution notices. Documentation only.

- [PR #1030](https://github.com/BitterSecurity/Decepticon/pull/1030):
  document interactive CLI commands and familiar keys in the README and getting
  started guide. Correct the direct Red setup flow, explain `/new` session and
  scrollback preservation, and distinguish merged source from released images.
  Documentation only; no runtime or version changes.

- Merged [PR #1029](https://github.com/BitterSecurity/Decepticon/pull/1029):
  CLI slash command conveniences add `/plan`, `/shortcuts`,
  `/version` (`/about`) and `/pwd` (`/cwd`), plus `/new` as an alias for `/clear`.
  Display aliases in help and autocomplete, including existing `/exit` and `/?`.
  A fresh conversation requires idle state and an empty queue, preserving active
  runs, queued prompts and saved sessions. Plan remains optional and retains
  existing mode and web terminal restrictions. On exit, disconnect the local
  stream so the CLI can terminate while server work continues. Source changes
  are unreleased.

- Merged [PR #1028](https://github.com/BitterSecurity/Decepticon/pull/1028): add Shift+Enter
  and backslash-Enter multiline input, word navigation/deletion, cut recovery,
  input undo, empty-prompt help, Alt+P model selection and confirmed empty Ctrl+D
  exit. Esc dismisses focused UI before pausing active work while retaining queued
  input. Preserve existing Ctrl+A/E aliases and authorization/run semantics.
  Negotiate enhanced keys only with supported terminals; portable fallbacks remain.
  No release or terminal configuration rewrite is included.

- [PR #1015](https://github.com/BitterSecurity/Decepticon/pull/1015)
  lifecycle completion: add protected per-run directory mounts and a trusted SDK
  caller that registers the API-generated run UUID after startup, publishes fresh
  credentials atomically, handshakes before model work after queue admission,
  isolates browser sessions, and revokes/stops them after
  completion or failure. Retain the static descriptor option and default
  Playwright backend. The compatible native host context remains a downstream
  dependency; Windows forwarding is explicitly unsupported. No release or SaaS
  runtime pin change is included. CI follow-up makes the new registration tests
  explicitly narrow optional results without changing runtime behavior.

- Review follow-up for [PR #1015](https://github.com/BitterSecurity/Decepticon/pull/1015):
  accept canonical parent-directory aliases used by macOS while still rejecting
  descriptor symlinks and paths outside the private run directory. Test Windows'
  explicit unsupported-transport rejection separately from Unix integration.
  Correct the protocol document's action count to 13.
  Native engine support remains platform-specific; Windows forwarding remains
  unqualified. Keep the integration proposed and unreleased.

- Merged [PR #1026](https://github.com/BitterSecurity/Decepticon/pull/1026):
  interactive
  startup checks the configured CLI service's API route instead of requiring
  host loopback connectivity. Service-only/Desktop startup and `doctor` retain
  their host endpoint checks. Exact assistant validation, bounded requests and
  the existing startup deadline apply to both routes; broken CLI DNS or API
  responses still block interactive startup. Related to #1004; the reporter's
  specific host forwarding timeout is not claimed resolved. Readiness tests
  separate host Node startup from request deadlines for Windows verification.

- Merged [PR #1025](https://github.com/BitterSecurity/Decepticon/pull/1025): process Backspace even when
  terminal input batches it with typed text, and keep Up/Down navigating recalled
  slash-command history until another editing action. Preserve multiline paste,
  Unicode graphemes and the unsent draft. Source only; no release is published.

- Merged [PR #1024](https://github.com/BitterSecurity/Decepticon/pull/1024):
  startup readiness and `doctor`
  now honor the shell's `LANGGRAPH_PORT` before `.env`, matching Compose's
  published port. An explicitly empty shell value selects the default 2024.
  This fixes false failures when the two configurations disagree; it does not
  establish resolution of the affected host's separate timeout in issue #1004.

- [PR #1023](https://github.com/BitterSecurity/Decepticon/pull/1023):
  name the workload-lifecycle command and Go packages `infracontrol`, while
  retaining `decepticon opscontrol` as an alias. New service units invoke the
  new command; installed unit names, legacy units, environment variables,
  socket/PID paths, Compose overlay names and doctor JSON keys remain valid.
  Source only; no release is published.

- Merged [PR #1022](https://github.com/BitterSecurity/Decepticon/pull/1022):
  document launcher commands and session startup; suggest command names for
  root typos, validate log service names against the Compose stack, and add
  `status --json` for scripts. Keep existing session, service and human-output
  behavior. Source only; no release is published.

- Merged [PR #1021](https://github.com/BitterSecurity/Decepticon/pull/1021):
  point active repository, benchmark submodule, release, package and desktop
  links at BitterSecurity; use the current repository slug for launcher star
  prompts. Keep Go module/import paths, installed identifiers and historical
  records unchanged. Source only; no release is published.

- Merged [PR #1017](https://github.com/BitterSecurity/Decepticon/pull/1017): fix CLI Backspace and forward Delete,
  Unicode-safe cursor editing, familiar multiline and history controls, and
  filter `/model` choices to routes supported by the owner's local provider
  configuration. Preserve manual model IDs and explicit local/BYOK endpoints.
  Picker eligibility does not verify live provider entitlement. Source change
  only; no OSS release is published.

- [PR #1012](https://github.com/BitterSecurity/Decepticon/pull/1012):
  add UUID artifact revisions, immutable snapshots, a structured Info log and
  OPPLAN search toward independently verified High/Critical findings. Preserve
  legacy workflow checkpoints and finding readers. Review follow-up restores
  committed finding bytes after failed specialists, serializes verifier writers,
  retains historical Info pins, avoids implicit workflow injection into optional
  legacy plugin agents and makes backend publication failures retryable.
  Preserve imported revisions when publication fails, merge parallel task
  receipts and parallel recovery failures, and regenerate the checked-in skill
  graph after protocol edits.
  This source integration does not publish a release or deploy SaaS. Paired SaaS
  integration: [decepticon-saas#1294](https://github.com/BitterSecurity/decepticon-saas/pull/1294).

- [PR #1015](https://github.com/BitterSecurity/Decepticon/pull/1015):
  Desktop owned-browser-v1 adapter with trusted per-run browser injection,
  authenticated capability/identity negotiation, protected launcher socket mounts,
  bounded cancellation and fail-closed protocol errors preserve the existing
  `browser_action` schema and default Playwright behavior. Depends explicitly on
  the reviewed PR #1009 revision `0e364229`; native engine containment, actual
  graph run registration and Windows forwarding still require qualification.
  No SaaS pin or release changes.

- [PR #1009](https://github.com/BitterSecurity/Decepticon/pull/1009): add the Desktop `serve --engagement` adapter for existing local
  engagements, with native model selection, private runtime configuration and
  engagement-scoped mounts. Preserve normal CLI startup and existing homes.
  Support canonical Desktop workspace/engagement document paths alongside legacy
  CLI folders. Source work only; no package version or release is published.
  Review fix: route the selected model into every agent role and use the catalog's
  actual Kimi/Minimax credential names; retain Windows platform-specific file
  permission checks alongside private configuration handling. Keep Desktop
  selection on one model without inherited cloud fallbacks, preserve the
  validated workspace path for spawned workloads, detect managed-service
  run-directory mismatches before startup, and apply updated service settings
  during explicit reinstall.

- Consolidate change tracking in this single `CHAGELOG.md`; remove the former
- Consolidate change tracking in this single `CHANGELOG.md`; remove the former
  alternate entry point and update contributor/release instructions.

- PR #1013 verification follow-up: format catalog tooling with repository Ruff
  rules and keep safety assertions independent of the growing model count.

- [PR #1013](https://github.com/BitterSecurity/Decepticon/pull/1013):
  refresh the shared public catalog from reviewed SaaS main pricing/model data,
  retaining context-tier prices and provenance. Include four public models and
  admit their metadata in the locally reviewed generator. Existing credentials,
  defaults and explicit local compatibility overrides remain; this does not
  publish an OSS version or prove provider availability.

- [PR #1014](https://github.com/BitterSecurity/Decepticon/pull/1014):
  defer agent factory exports until requested, so framework role registration
  does not construct every agent graph during import. Preserve factory names,
  identity, signatures and all configured LangGraph endpoints. Existing startup
  health limits remain unchanged; this source fix is not a published release.

- [PR #1011](https://github.com/BitterSecurity/Decepticon/pull/1011):
  own a shared public LiteLLM model catalog and deterministic route/CLI generation
  for OSS and the pinned SaaS consumer. Retain existing route semantics,
  credential references and local OAuth behavior; admit new providers explicitly.
  No runtime network dependency, version bump or release. See
  [catalog maintenance](model-catalog/README.md).

- Consolidate version history into the single root `CHAGELOG.md`, preserving
- Consolidate version history into the single root `CHANGELOG.md`, preserving
  existing history and updating contributor guidance, links and tooling.
  Documentation only; no runtime or upgrade changes.
  [PR #1010](https://github.com/BitterSecurity/Decepticon/pull/1010).

- Distinguish host-side LangGraph HTTP connectivity, status errors, malformed
  assistant catalogs and missing `graph_id: decepticon` registration in startup
  and `doctor`. Replace string matching with exact catalog validation, reject
  redirects/invalid ports, and enforce the overall startup timeout across HTTP
  requests and retry waits. Keep the doctor JSON `langgraph` key; its check now
  requires both HTTP health and the actual assistant. Preserve existing timeout
  configuration, scopes, credentials and volumes. Proposed
  [PR #1008](https://github.com/BitterSecurity/Decepticon/pull/1008), related to
  [#1004](https://github.com/BitterSecurity/Decepticon/issues/1004); the reported
  host networking root cause remains unconfirmed and is not declared resolved.

- Record v1.5.2's actual publication date and included fixes; retain v1.5.1 as
  an incomplete candidate rather than a published final. Documentation only;
  no runtime or upgrade changes. [PR #1007](https://github.com/BitterSecurity/Decepticon/pull/1007).

## [1.5.2] — 2026-10-18

- Fix release CLI image metadata being reset to the source `0.0.0` sentinel:
  stamp the version after copying source, preserve dependency cache across
  versions, and verify the actual published CLI container package version in CI
  before signing/publication. Candidate v1.5.1 final publication was withheld;
  its existing tag/packages remain immutable. [PR #1006](https://github.com/BitterSecurity/Decepticon/pull/1006).

- Restore direct Red execution for new CLI engagements: collect name and target
  web URL, automatically create a canonical RoE and byte-bound direct approval,
  and open Red ready for the first request. Plan remains optional; no external
  scope file, authorization-reference field or manual approval command is needed.
  Preserve existing engagement scopes and conversations; legacy missing-scope
  recovery remains available. URL input follows current exact-root/default-port
  enforcement rather than expanding it. [PR #1005](https://github.com/BitterSecurity/Decepticon/pull/1005).

- Release-history documentation follow-up: record the published v1.5.0
  version/date and preserve the canonical history. No runtime or upgrade behavior
  changes. Define OSS Major/Minor/Patch selection, required actual WSL and candidate
  artifact gates, immutable releases, final/stable channel rules, and failure/hotfix
  procedures in RELEASE.md. Align contributor and agent instructions; this policy
  is a documentation change and does not create a new product version. [PR #1003](https://github.com/BitterSecurity/Decepticon/pull/1003).

## [1.5.0] — 2026-10-17

- WSL startup correction in [#1002](https://github.com/BitterSecurity/Decepticon/pull/1002): use LiteLLM’s supported offline migration mode and its bundled native Prisma CLI. Resolve the CLI at image build time and fail the build if missing. Avoid repeated generated Python-client imports exhausting migration deadlines; retain normal schema migrations and existing database volumes.

- PR review corrections for [#1002](https://github.com/BitterSecurity/Decepticon/pull/1002): keep a paused conversation recoverable when destination mode lookup fails; refuse cross-session restoration while undelivered queued messages remain; give Delete forward-delete semantics.

- Integration review for [#1002](https://github.com/BitterSecurity/Decepticon/pull/1002): preserve #1001 workflow/scope contracts alongside session restoration and CLI UX; align launcher and Korean documentation with optional Plan and explicit scope approval.

### Client and workflow contracts

- [PR #1001 review corrections](https://github.com/BitterSecurity/Decepticon/pull/1001):
  leave planning workflow selectors unset when launch overrides are removed so
  resumed threads retain their immutable pins. MCP forwards validated selectors
  without resolving the bridge-local catalog or inventing a version; the target
  runtime owns resolution and purpose checks. Scope validation rejects redirects
  rather than sending operator-provided files to another endpoint. Configure the
  final API URL directly; planning versions require an explicit planning ID.

- Complete Red-first OSS client launch and keep Soundwave exclusively in optional
  Plan mode. Separate its stable capability prompt from planning/WORKFLOW.md and
  review its 17 reusable knowledge/template skills. Operator scope import and
  explicit direct Red approval do not require the seven additional Plan files.
- Share canonical RoE validation between the operator importer and a bounded,
  validation-only HTTP route. Preserve original document bytes, exclusive import,
  scope-change invalidation and validated Plan bundle approval semantics. Failed
  approval or run interruption keeps the existing mode and conversation.
- Forward trusted workflow IDs/versions through launcher, CLI and MCP adapters;
  preserve separate planning pins and use registered role-purpose metadata for
  execution approval checks, including plugin roles. Unknown roles require
  execution approval. Existing workflow checkpoints retain their pinned source.
- Add the contributor extension contract and reference it from repository
  instructions; replace obsolete RoE-authoring, fixed-phase, skill-format and
  artifact-path guidance. Replace misleading prompt-prose checks with execution,
  checkpoint, schema and client-boundary regression coverage.
- Compatibility: clients and runtime must include the new validation/purpose
  routes together. Existing approved direct RoE and validated bundle digests remain
  supported; changing business workflow requires a new execution thread. This is
  a follow-up to merged #999 and #1000, included in v1.5.0.
  [PR #1001](https://github.com/BitterSecurity/Decepticon/pull/1001).

### Added

- CLI experience improvements: searchable mode/model/session pickers,
  multiline editing, private persistent drafts/history and external-editor input,
  a lossless waiting-message queue, compact Blue summaries with incident details,
  and independent monitor/collector/AI status. Add `/status`, `/sessions`, `/queue`,
  `/alerts`, private Markdown/JSON `/export`, host `doctor --json`, and explicit
  startup resume/continue controls. See [compatibility notes](docs/cli-experience.md).
  [PR #1002](https://github.com/BitterSecurity/Decepticon/pull/1002).

- Fix CLI command completion changing `/mode` into `/model`; preserve exact matches
  and make arrow/Tab/Enter selection consistent. Open engagements in Red while
  retaining scope approval checks. Scope and paginate saved sessions, restore
  pending questions, and offer confirmed migration of older conversation metadata.
  Mount the selected CLI workspace and persist local UI state across container
  recreation. Retain waiting messages when session creation or streaming fails,
  and clear stale Blue connection errors after recovery. No database reset is required. Updated images and
  Compose configuration are required; these changes ship in v1.5.0.

- Add a persistent light/dark dashboard toggle, retaining dark as the default.
  Honor system preferences when toggling and keep status/severity labels readable
  in light mode. Fixed dark terminal and graph surfaces retain their palette.
  Browser-local preference only: no database migration, package version change
  or configuration upgrade is required.
  ([#997](https://github.com/BitterSecurity/Decepticon/pull/997))

- Optional vendor-neutral OpenAI-compatible gateway (`compat/…`) driven by
  `OPENAI_COMPAT_GATEWAY_API_BASE`, `OPENAI_COMPAT_GATEWAY_API_KEY`, and
  `OPENAI_COMPAT_GATEWAY_MODEL` (or per-tier `*_MODEL_{HIGH,MID,LOW}`).
  Default off; no vendor endpoint is hardcoded. Addresses the capability in
  [#989](https://github.com/BitterSecurity/Decepticon/issues/989) through
  [#995](https://github.com/BitterSecurity/Decepticon/pull/995).
  Existing provider ordering and `CUSTOM_OPENAI_*` remain supported. Invalid URLs,
  placeholder keys and empty model slugs stay disabled consistently in credential
  detection and proxy route generation; sentinel tier models use the shared model.
  Requires the v1.5.0 code/images; existing provider settings remain supported.

- [PR #999](https://github.com/BitterSecurity/Decepticon/pull/999): separate stable
  capability specialists and reusable `SKILL.md` knowledge from individual
  `WORKFLOW.md` playbooks for bug bounty, red team, pentest and vulnerability
  research. Pin workflow content in checkpoints and inherit it in subagents;
  enforce evidence dependencies through OPPLAN. Review core prompts and migrate
  startup/lifecycle business procedures out of skills.
- Compatibility/upgrade for PR #999: add optional `Objective.workflow_stage`;
  preserve existing unbound plans through a policy-only compatibility snapshot.
  Workflow updates affect new executions; existing checkpoint pins remain stable.
  Optional legacy VR and benchmark contracts retain their
  separately documented integration boundaries. Add root change-tracking
  instructions while preserving the canonical CHAGELOG.md
  instructions while preserving the canonical CHANGELOG.md
  history and release automation. Included in v1.5.0.
- Regenerate the checked-in Skillogy graph from the changed skill corpus so
  retrieval metadata and the deterministic CI build gate match the source (#999).
### Fixed

- Add an operator-confirmed, create-only RoE import for existing engagements,
  preserving planning artifacts and requiring fresh planning/Red approval.
  Includes canonical schema export and installed/source usage instructions.
  ([#1000](https://github.com/BitterSecurity/Decepticon/pull/1000), fixes
  [#998](https://github.com/BitterSecurity/Decepticon/issues/998))
  Upgrade the launcher, configuration and Docker images together to v1.5.0 to
  use the new command; existing database credentials and volumes are preserved.
- Expect the unauthorized call to revert in generated Foundry access-control tests.
  ([#991](https://github.com/BitterSecurity/Decepticon/pull/991))
- Retain file-backed approval decisions until their matching request consumes them.
  ([#993](https://github.com/BitterSecurity/Decepticon/pull/993))

### Additional fixes and maintenance

- Retain engagement configuration on resume and question-answer requests
  ([#981](https://github.com/BitterSecurity/Decepticon/pull/981)); preserve
  canonical finding title, summary and lifecycle metadata in the dashboard
  ([#979](https://github.com/BitterSecurity/Decepticon/pull/979)) and export all
  planning documents ([#975](https://github.com/BitterSecurity/Decepticon/pull/975)).
- Reject premature engagement completion when coverage follow-ups remain
  ([#977](https://github.com/BitterSecurity/Decepticon/pull/977)); improve finding
  YAML/rationale handling ([#986](https://github.com/BitterSecurity/Decepticon/pull/986))
  and document finding-quality relative paths
  ([#980](https://github.com/BitterSecurity/Decepticon/pull/980)).
- Support Cheaper Inference gateway configuration and credential handling
  ([#956](https://github.com/BitterSecurity/Decepticon/pull/956),
  [#985](https://github.com/BitterSecurity/Decepticon/pull/985)); fail closed on
  unpriced routes or unavailable model inventory under budget constraints
  ([#994](https://github.com/BitterSecurity/Decepticon/pull/994)).
- Make Skillogy English term rejection actionable and support key-only embedding
  installations ([#982](https://github.com/BitterSecurity/Decepticon/pull/982),
  [#988](https://github.com/BitterSecurity/Decepticon/pull/988)).
- Use the compatible opscontrol bind-mount form
  ([#978](https://github.com/BitterSecurity/Decepticon/pull/978)); reorganize the
  terminal benchmark and remove unsafe host swap-file deletion
  ([#987](https://github.com/BitterSecurity/Decepticon/pull/987),
  [#984](https://github.com/BitterSecurity/Decepticon/pull/984)).
- Document coordinated security disclosure and require local product testing
  before releases ([#996](https://github.com/BitterSecurity/Decepticon/pull/996),
  [#970](https://github.com/BitterSecurity/Decepticon/pull/970)).

### Upgrade notes

- Run `decepticon update --channel latest`. Update matching Docker images and
  configuration together. No credential reset or volume deletion is required.
- Existing workflow checkpoints keep their immutable workflow pins. Scope approval
  remains explicit; Plan is optional. Blue observation remains local and does not
  imply complete host telemetry or autonomous response.

## [1.4.4] — 2026-10-14

### Fixed

- Select startup Update now / Skip with arrow keys and confirm with Enter,
  keeping Skip selected by default.
  ([#966](https://github.com/BitterSecurity/Decepticon/pull/966))
- Preserve and recover empty directories left at the opscontrol socket path;
  reject files, nonempty directories and symlinks. Report daemon readiness only
  after binding, and prevent Compose from creating a missing socket source.
  ([#967](https://github.com/BitterSecurity/Decepticon/pull/967))
- Recover specific Docker missing-network startup failures with bounded retries
  for empty, configured networks owned by the current Compose project. Preserve
  workspace/auth environment, profiles and volumes; reject active or foreign
  networks and unrelated failures.
  ([#968](https://github.com/BitterSecurity/Decepticon/pull/968))

### Upgrade notes

- Run `decepticon update --channel latest` to install this patch. Older binaries
  retain their previous prompt until upgraded. Database credentials, volumes and
  existing engagement data do not need to be recreated.

## [1.4.3] — 2026-10-14

### Fixed

- Ask only for an engagement name in the CLI launcher, then open a planning
  session. Target, mode and authorization-reference fields no longer block
  creation; existing engagement scopes remain resumable.
  ([#963](https://github.com/BitterSecurity/Decepticon/pull/963))
- Allow Plan, Red and Blue conversations to be selected during a CLI run,
  preserving each mode's thread, pending question and paused checkpoint. Keep
  execution prerequisites separate from mode selection, and bound server pause
  acknowledgment to 10 seconds without switching on failure.
  ([#964](https://github.com/BitterSecurity/Decepticon/pull/964))

### Upgrade notes

- Run `decepticon update --channel latest` to install this patch. New CLI
  engagements open Plan; select `/mode plan`, `/mode red` or `/mode blue`
  during the session. Actual Red operations require a currently approved scope
  (`/mode red approve`); Blue investigation requires a connected local sensor.
  Existing engagement data and database credentials do not need to be recreated.

## [1.4.2] — 2026-10-14

### Fixed

- Require an explicit Update now / Skip choice before startup updates, with
  Skip selected by default, including for legacy `AUTO_UPDATE=true` settings.
  Non-interactive launches only show a notice, and update notices identify
  the selected release channel.
  ([#959](https://github.com/BitterSecurity/Decepticon/pull/959))
- Default launcher checks, Bash installs, and unpinned Compose images to the
  latest final release. The seven-day stable channel remains an explicit opt-in.
  ([#960](https://github.com/BitterSecurity/Decepticon/pull/960))

### Upgrade notes

- Older launcher binaries keep their compiled update behavior until upgraded.
  Run `decepticon update --channel latest` once to install this patch. Subsequent
  interactive startup checks offer `1. Update now` / `2. Skip` only when a newer
  release exists; pressing Enter skips. `AUTO_UPDATE=false` and `--no-update`
  disable startup checks. An explicit `DECEPTICON_CHANNEL=stable` retains the
  seven-day delay.

## [1.4.1] — 2026-10-14

### Fixed

- Require matching release versions of Core and framework dependencies in
  published Python packages so upgrades include their sibling fixes.
  ([#955](https://github.com/BitterSecurity/Decepticon/pull/955))
- Reject duplicate CVSS metrics, unsupported version labels, and empty vector
  segments before scoring. ([#948](https://github.com/BitterSecurity/Decepticon/pull/948))
- Complete planning-document schema guidance, detect model/table drift, and
  report all invalid documents in one validation attempt.
  ([#929](https://github.com/BitterSecurity/Decepticon/pull/929))
- Transfer SARIF imports and reports through the run sandbox with trusted graph
  and workspace scope, preserving protected files, MCP-started workspaces,
  and registered SARIF adapter overrides.
  ([#936](https://github.com/BitterSecurity/Decepticon/pull/936))
- Attach the image digest manifest before publishing a release; GitHub Actions
  does not start a second workflow from a release published with `GITHUB_TOKEN`.
  ([#938](https://github.com/BitterSecurity/Decepticon/pull/938))
- Require successful terminal completion of finding checks and their negative
  controls; retain bounded baseline output, exit status, and hash.
  ([#953](https://github.com/BitterSecurity/Decepticon/pull/953))
- Restore injected runtime handling for OPPLAN task dispatch and finding-quality
  checks. ([#923](https://github.com/BitterSecurity/Decepticon/pull/923))
- Bind tmux command completion to the submitted shell command, preserving printed
  prompt markers and waiting for the final exit status of multiline commands.
  ([#951](https://github.com/BitterSecurity/Decepticon/pull/951))
- Buffer Ollama tool-bound requests on the wire as well as in the response
  parser, while preserving text streaming, token callbacks, and usage accounting.
  ([#947](https://github.com/BitterSecurity/Decepticon/pull/947))
- Preserve LangGraph threads and checkpoints across container recreation by
  storing development runtime state in a Compose project volume.
  ([#954](https://github.com/BitterSecurity/Decepticon/pull/954))
- Give LangGraph access to the engagement workspace for planning validation and
  readiness markers. ([#924](https://github.com/BitterSecurity/Decepticon/pull/924))
- Recover missing terminal threads before PTY startup and stop safely on backend
  errors or disconnected startup. ([#935](https://github.com/BitterSecurity/Decepticon/pull/935))
- Stop terminal initialization after navigation unmounts it while xterm is loading,
  avoiding abandoned terminal resources. ([#932](https://github.com/BitterSecurity/Decepticon/pull/932))

- Discover all registered `/agent` orchestrators across assistant catalog pages,
  retaining the current selection if discovery fails or times out.
  ([#949](https://github.com/BitterSecurity/Decepticon/pull/949))
- Start engagement terminals only after opening Live, preserving visited mode
  terminals across tab navigation. ([#934](https://github.com/BitterSecurity/Decepticon/pull/934))

- Load browser API and terminal endpoints from the running web server so one
  prebuilt image supports relative reverse-proxy routes across deployments.
  ([#931](https://github.com/BitterSecurity/Decepticon/pull/931))
- Stop disconnected terminal startup before it can replace an attached session
  or spawn an orphan PTY. ([#930](https://github.com/BitterSecurity/Decepticon/pull/930))
- Route ChatGPT OAuth profiles through current Codex models (GPT-6 Astra,
  GPT-6.1 Sol, GPT-6 Luna), and remove retired OAuth routes from the model picker.
  ([#950](https://github.com/BitterSecurity/Decepticon/pull/950))
- Follow renamed-repository redirects during installer release lookup, pin
  explicit-version config downloads to the matching release, and correct the
  version recovery command. ([#927](https://github.com/BitterSecurity/Decepticon/pull/927))

- Apply CLI `/model` selection and clearing through persisted graph state,
  including Soundwave, specialists, and resumed runs, while preserving
  provider-specific model settings.
  ([#952](https://github.com/BitterSecurity/Decepticon/pull/952))
- Allow freshly onboarded installations to start without a legacy credential
  migration backup; continue reporting unreadable backup files.
  ([#946](https://github.com/BitterSecurity/Decepticon/pull/946))

### Upgrade notes

- LangGraph history is retained after the new volume is in use. Existing
  unmounted container history is not automatically migrated or recovered;
  back it up before replacing that container if it must be retained.
  Development-runtime persistence does not guarantee crash durability.

## [1.4.0] — 2026-10-13

### Added

- Add explicit Plan, Red, and Blue modes to the OSS launcher, CLI, and web
  dashboard. Red accepts a directly approved target; Soundwave's eight-document
  interview is optional Plan mode. Keep mode-specific sessions and check Red
  scope approval and Blue sensor availability before switching. (#919)
- Expose the current production LiteLLM model routes in OSS, including the
  provider mappings used by the CLI and agents. (#916)
- Show all nine planning documents in the engagement Plan view. (#893)

### Fixed

- Keep the listed local Ollama model ID directly selectable in the CLI. (#905)
- Count the complete LiteLLM model response and show concise service health
  details instead of truncating the response before counting. (#897)
- Show Settings collection request and validation failures; report HTTP errors
  even when the error body is not JSON. (#895)
- Bind each report tool call to the current engagement graph partition and
  restore the prior scope after the call. (#907)

### Validation

- Run the web unit tests in CI, including tests written in `.test.tsx` files.

### Documentation

- Explain planning, specialist execution, REA/Ghidra reversing, Blue Cell,
  and operator review on the English and Korean README entry pages, with
  links to the current behavior guides.

## [1.3.1] — 2026-10-12

### Fixed

- Hide legacy Ghidra tools when the REA workload is configured, so the
  reversing agent uses REA for native analysis.
- Keep REA discovery schemas intact as model-safe JSON text and inject bash
  runtime configuration outside model tool schemas. This lets Gemini select
  REA tools and cite their saved Evidence.

## [1.3.0] — 2026-10-12

### Added

- Use a dedicated REA/Ghidra service for the reversing agent. The agent can
  discover the available REA tools, analyze binaries in its engagement
  workspace, and retain the original Evidence bundle and integrity manifest.
  (#913)
- Port 30 capabilities, including 69 tools and six middleware components.
  (#884)

### Fixed

- Distinguish invalid model tool calls from empty CLI responses. (#882)

## [1.2.2] — 2026-10-03

This feature-bearing patch version was selected by the maintainer.

### Added

- **Local Blue Cell sensor and CLI:** observe a local web service through a
  reverse proxy, capture request bodies and optional target logs, retain bounded
  events, and show incidents and alerts in the interactive CLI. (#833)
- **Resident defensive agent:** continuously review live event windows,
  investigate incidents, persist progress across restarts, and deliver findings
  to the CLI chat. (#837)

### Fixed

- Parse guarded sensor-tool output safely during resident Blue Cell reviews.
  (#838)
- Wait for the initial tmux environment export to finish before capturing the
  first command's output. (#836)

## [1.2.1] — 2026-10-02

### Added

- Enforce versioned, evidence-backed DAG execution for OPPLAN.

## [1.2.0] — 2026-10-01

### Added

- **Dependency-aware OPPLAN:** validate the objective DAG before plan mutations,
  reject cycles and unresolved prerequisites, and prevent completion while a
  required objective remains incomplete. The orchestrator's startup, planning,
  delegation, and specialist skills now use the same OPPLAN contract. (#829, #830)

### Fixed

- Preserve streamed Codex function-call arguments when an SSE argument delta
  arrives before the corresponding output-item metadata. (#828)

## [1.1.47] — 2026-09-26

### Fixed

- **Soundwave interview cut from ~18 questions to ≤8.** `roe-template`
  alone drove 10 separate `ask_user_question` calls and `conops-template`
  another 8 (including a question for the DEPRECATED `communication_plan`
  field), so a fresh engagement paid for every dimension one field at a
  time before any planning document existed. Added a hard 8-question
  interview budget (system prompt CRITICAL_RULES #12): RoE consolidates
  related fields into 6 combined questions, CONOPS/Threat Profile asks
  only tier + success criteria (2) and derives motivation, initial
  access, attack narrative and deconfliction method from tier/RoE
  defaults instead of asking, and Contacts / Data sensitivity / Abort
  triggers / Persistence footprint default from schema + RoE/CONOPS
  content unless the operator's own answers raise a flag a default
  can't cover. Assumption-challenge follow-ups are now folded into the
  original picker's options instead of spawning a second question
  round. (#825)

## [1.1.45] — 2026-09-24

### Added

- Persist UTC creation times on streamed sub-agent start, message, tool, and end events so Run transcripts retain their original times after a reload. Previously stored events remain compatible and undated. (#821)

## [1.1.44] — 2026-09-19

Version v1.1.44 was selected by the maintainer. This release includes the cumulative
changes since the last public release, v1.1.40. Versions v1.1.41–v1.1.43
remained unpublished GitHub drafts.

### Added

- **Autohunt autonomous planning:** a separate bootstrap planning lane accepts
  an explicitly authorized target and validates planning documents and scope
  before marking the engagement ready. Soundwave remains the default
  interview-first planner. (#813)
- **Specialist validation contracts:** executable capability contracts,
  evidence validation, lane scorecards, held-out evaluation fixtures, CVE-Bench
  dispatch plumbing and pinned MITRE STIX bootstrapping. (#811)
- **Provider support:** default AWS Bedrock routes and native Kimi for Coding
  support, including launcher onboarding. (#796, #803)
- **Evidence controls:** local API ingestion, dependency reachability evidence
  and severity ceilings. Provider calls gain extra-header and streaming
  controls; dynamic workload teardown is scoped by engagement and run. (#801)
- Typed finding-location guidance in the skill catalog. (#810)

### Fixed

- Restore missing OPPLAN workspaces before filesystem bootstrap and preserve
  native Ollama tool calls by buffering tool-bound requests. (#807)
- Match recursive domain wildcards in Rules of Engagement. (#800)
- Fall back correctly when provider status is malformed. (#794)
- Preserve the release JSON payload used by the installer's stable-version
  resolver. (#795)
- Restore OSS installation readiness with the Neo4j pin and stricter handling
  of missing installation secrets and legacy salt fallback. (#804)
- Support Python package metadata 2.5 in release publishing. (#805)
- Disable the oversized, duplicate per-platform BuildKit sandbox SBOM while
  retaining provenance and the merged-manifest CycloneDX/signing path. (#806)
- Use the canonical `BitterSecurity` repository and container registry paths
  for release publishing, image pulls, installation and launcher updates.

### Security and operational changes

- Update Next.js and its ESLint configuration to 16.3.5, addressing the
  critical AVIF image-optimization advisory and other Next.js advisories.
  Update CLI Vitest to 3.2.7 to remove its critical advisory. Other npm
  dependency advisories remain; this is a targeted security update.
- Update Python dependencies to address security advisories. (#797)
- Reject default launcher credentials and disable telemetry in `.env.example`.
  The onboarding wizard still preselects telemetry sharing; select
  "No, keep it off" or set `DECEPTICON_TELEMETRY=off` to disable it.
  Add a release image-digest manifest workflow for immutable image pinning.
  Existing installations using default credentials or missing secrets must
  update their configuration before starting. (#802, #804)
- An Electron dashboard shell and cloud-session support were added, then
  marked deprecated. The web dashboard and CLI remain the supported paths.
  (#730, #798, #802)

## [1.1.40] — 2026-07-27

### Fixed

- **Engagement scope is a boundary again, not a convention.** `KGStore` was
  engagement-scoped but the legacy attack-graph helpers around it were not, and
  the scope that did exist could be overridden by the caller — a raw statement
  that omitted the predicate read every engagement's nodes, and
  `params={"engagement": …}` replaced the value the store had resolved (a
  dict-merge order bug: the caller's spread landed on top of the trusted value).
  Both were reachable from an agent tool, since `plan_attack_chains` issues raw
  Cypher through `query_custom`. Anywhere one graph holds more than one
  engagement — a shared LangGraph plane, a hosted control plane, a team
  instance — those were cross-engagement read/write primitives.
  `execute_read`/`execute_write` now reject a statement that does not bind
  `$engagement` and build their params so the resolved value always wins;
  schema migrations stay exempt. The in-repo callers (`chain.py`, `summary.py`,
  `kg_adapter.py`, `adcs_post.py`, the snapshot path) carry the scope rather
  than being left to fail. Summarization also binds the model selected for the
  current request instead of the deployment-keyed one captured at process boot,
  so concurrent runs stop summarizing through each other's model. (#790)

  **Breaking for raw Cypher:** a statement without `$engagement` now raises
  instead of returning unscoped rows. `$engagement` is already injected into the
  params, so an external caller adds a `WHERE` predicate, not new plumbing.

## [1.1.39] — 2026-07-27

Released as a patch by maintainer decision: the one `feat:` in the range
(#773, a benchmark-provider addition) changes no runtime behaviour for
existing users, so the SemVer minor bump the auto-tag rules would have
computed was overridden.

> **Note on the sections below.** The `[1.1.8] — Unreleased` heading that
> follows has not been rotated since 2026-06-01, so releases 1.1.9 through
> 1.1.38 are absent from this file. That backlog is untouched here rather
> than reconstructed from git; this section covers only 1.1.38 → 1.1.39.

### Fixed

- **LLM responses stream again — they never actually did.** Two independent
  causes, either sufficient on its own. The Claude Code OAuth handler's
  `streaming`/`astreaming` awaited the *buffered* completion and chopped the
  finished answer into chunks, never sending `"stream": true` upstream; and
  `_create_chat_model` built `ChatOpenAI` without `streaming`, so `.invoke()`
  issued a non-streaming request and LangChain never fired
  `on_llm_new_token` — which is what LangGraph's `messages` stream mode is
  built on. Both handlers now translate their upstream SSE directly, and
  `stream_usage=True` accompanies `streaming=True` so the measurement path
  still sees `usage_metadata`. The Codex ChatGPT lane had the same symptom
  from a third angle (it asked to stream, then read `resp.text` before
  walking the events) and is fixed alongside. (#786)
- **Telemetry fields that shipped empty or constant.** `wrap_model_call`
  returns a `ModelResponse` wrapper, not an `AIMessage`, so reasoning,
  tokens and stop reason were read off attributes that do not exist; and
  `_slug(None)` returned the literal `"none"`, poisoning four columns with a
  real-looking value. Redaction also gains credential-KV and opaque-blob
  detectors, and stops mangling dotted code as domains. (#785)
- **`bash` prompt steers to `curl_cffi` over raw `curl`** for web fetches —
  raw `curl`'s TLS/JA3 fingerprint is blocked by WAFs on the first request,
  and retrying never clears it. (#777)
- **Windows launcher self-update** moves the running image aside instead of
  renaming over it (an executable cannot be overwritten while in use), with
  rollback on failure and best-effort cleanup on the next launch. (#775)
- **CLI recovers after thread-creation retries are exhausted** instead of
  leaving the run state stuck. (#774)

### Documentation

- Model catalog, tier table and CLI slash-command reference synced with the
  code — MID is `claude-sonnet-5`, HIGH is `claude-opus-4-8`, and the web
  dashboard is dynamic-spawn (`/web`), not part of the default stack. (#778)

[1.1.40]: https://github.com/BitterSecurity/Decepticon/compare/v1.1.39...v1.1.40
[1.1.39]: https://github.com/BitterSecurity/Decepticon/compare/v1.1.38...v1.1.39

## [1.1.8] — Unreleased

The agent-driven dynamic infrastructure release. Specialist workloads
(BloodHound CE, Sliver C2, Ghidra MCP, …) and the web dashboard no
longer come up on `decepticon start` — the orchestrator brings them
up on demand. Core management plane (LiteLLM, PostgreSQL, Skillogy,
LangGraph, sandbox) keeps the always-on contract.

### Added

- **Open-web acquisition engine (`web_search` / `web_fetch`).** A new
  site-agnostic fetch engine (`decepticon/sandbox_web/`) that escalates past
  WAF/anti-bot defenses — Verdict-based 4-layer validation ("HTTP 200 is an
  inspection-start condition, not success"), ranked WAF-product detection, a
  transform×TLS-impersonate×referer grid, and a headless-browser fallback. It
  runs **inside the sandbox** (all egress stays in sandbox-net behind the
  nftables allowlist) and is dispatched by two new agent tools over the bash
  execution surface — no new transport. RoE is layered: middleware gate
  (management) + per-hop `scope_check` from `roe.json` (engine) + nftables
  (authoritative). `web_fetch` is target-gated; `web_search` is OSINT over an
  allowlisted provider (audited, target-exempt). Both outputs are
  prompt-injection-quarantined (`UNTRUSTED_TOOL_NAMES`). Wired into the `recon`
  and `osint_operator` agents. Engine precisely derived from
  `fivetaku/insane-search` (MIT) with its governance inverted for RoE/network
  isolation. See [ADR-0010](docs/adr/0010-open-web-acquisition.md). (#682)
- **CI hardening pass.** `ci-ok` aggregator job — branch protection now
  needs exactly one required check; every job added to `ci.yml` in the
  future is automatically merge-blocking (skipped path-gated lanes
  still pass, failures/cancellations block). All third-party actions
  across the 8 workflows + the composite action pinned to full commit
  SHAs with version comments (supply-chain; Scorecard pinned-deps).
  `pip-audit` is now blocking (was `|| true` with a placeholder ignore)
  with a documented `.pip-audit-ignore` escape hatch — and the gate's
  first catch is in this PR: locked `pyjwt 2.12.1` carried 4 PYSECs,
  bumped to 2.13.0. `dependency-review` enforces (high+ severity new
  deps block). Dead license-gated Gitleaks job removed (TruffleHog in
  security.yml + gitleaks pre-commit hook already cover it). Every job
  carries `timeout-minutes`. PR coverage lane adds diff-cover patch
  coverage to the step summary (still non-blocking by design).
  `merge_group` trigger added so a future merge queue re-runs the same
  gates on the queued commit. (#673)
- **Universal LiteLLM provider support.** `litellm_dynamic_config.py`
  now carries the full LiteLLM v1.89.0 provider catalog (114 providers,
  source-verified env vars): ~75 new single-key providers in
  `PROVIDER_API_KEY_ENV`, a `PROVIDER_KEY_ENV_ALIASES` table for
  multi-alias keys (Together, Fireworks, Perplexity, Cohere, …,
  first-set-var-wins), a `PROVIDER_EXTRA_PARAMS` table for providers
  needing base/region/project params (Azure AI, watsonx, Databricks,
  Predibase, Snowflake, local servers), and a no-API-key set for
  SigV4/ADC/signed providers (Bedrock, SageMaker, Vertex, OCI, …).
  `ALLOWED_DYNAMIC_PROVIDERS` derives from the union, so any
  `DECEPTICON_LITELLM_MODELS=<provider>/<model>` slug from the catalog
  routes with the correct credentials; unknown prefixes get a
  remediation message naming the supported-provider count. (#672)
- **CVE-Bench harness (research-credibility track).** Adds
  [CVE-Bench](https://github.com/uiuc-kang-lab/cve-bench)
  ([leaderboard](https://cvebench.com/)) as a sibling benchmark to
  XBOW: 40 critical CVEs × 2 variants × 8 win conditions. Ships
  `benchmark/cve_bench/{loader,scorer,runner,dry_run}.py`, 3-CVE
  fixture snapshot, and `make cve-bench-dry` — an offline,
  deterministic (seed=0), mocked-LLM end-to-end harness exercise.
  Live 40 × 2 run gated on stack + LLM credentials (out of scope;
  see `benchmark/results/cve-bench/README.md` "FULL RUN PENDING").
  Coverage delta: `0 / 40 → 3 / 40` dry-run reachable.

- **ADR-0006 agent-driven container lifecycle.** A host-binary
  `opscontrol` daemon, supervised by systemd (Linux) / launchd (macOS),
  owns the docker socket and exposes a Unix-domain socket bind-mounted
  into the langgraph container. The orchestrator's `ops_start("ad")`,
  `ops_stop("ad")`, `ops_status` tools route through that socket.
  Async control plane: `ops_start` returns immediately with
  `state: "starting"`; the daemon's background goroutine runs the
  compose-up off the request path. The orchestrator's
  `OpsControlNotificationMiddleware` polls the registry once per turn
  and injects a `<system-reminder>` HumanMessage when a workload
  transitions to `running` / `stopped` / `unknown`, so the agent
  learns about completions without polling `ops_status`. (#619, #620)
- **`/web` slash command in the CLI.** The web dashboard is now
  dynamic-spawn — bring it up with `/web` (`/web up` / `/web stop` /
  `/web url` / alias `/dashboard`) from inside the terminal CLI.
  The CLI image ships with `docker-ce-cli` + `docker-compose-plugin`;
  the compose entry bind-mounts the host docker socket and the
  operator's `$DECEPTICON_HOME` so the slash command shells out
  against the operator's compose project. OSS scope only — downstream
  bundles override these volumes via a separate compose overlay. (#625)
- **One-shot upgrade migration** for the stale
  `COMPOSE_PROFILES=c2-sliver` line that pre-ADR-0006 (v1.1.7 and
  earlier) shipped in `.env.example`. On first v1.1.8 launch the
  launcher rewrites that line to a comment (preserving the original
  value inline) and writes a single `.env.bak` backup. Idempotent on
  repeat starts; the backup is not overwritten when an operator
  reintroduces the active line. (#624)

### Changed

- `docker-compose.yml`: the `web` service moves from default-start
  to `profiles: [web]`. Specialist workload services (`bhce` /
  `bhce-neo4j` / `bhce-postgres-init` under `[ad]`, `c2-sliver`,
  `ghidra-mcp` under `[reversing]`) keep their profile gates and are
  now driven exclusively by the orchestrator's `ops_start(...)`
  calls. (#620, #625)
- `decepticon start` UX: the message in the README and the launcher
  banner now states "Start the core stack and drop into the terminal
  CLI" rather than "Start everything" — the dashboard and specialist
  workloads no longer come up by default.

### Fixed

- **Auth / LiteLLM stack error-proofing.** Review pass over the
  subscription handlers, LiteLLM glue, LLM factory, and auth CLI:
  all six OAuth handlers now reject malformed refresh/mint/completion
  responses with actionable `AuthenticationError`/`APIError` instead of
  raw `KeyError`/`JSONDecodeError` tracebacks (and never echo tokens);
  `auth/` dispatcher validates non-string/empty model ids;
  `write_json_atomic` survives short `os.write`; `with_retry_on_401`
  clamps non-positive attempts; `write_dynamic_config` handles
  null-block/malformed/non-mapping YAML with clear errors; factory adds
  a 403 remediation branch, a credential-shape redaction net for
  unclassified provider errors, and resolves the LLM timeout before
  creating the request coroutine (no un-awaited coroutine on
  misconfig); CLI `auth` degrades cleanly on corrupt `.env` files and
  inventory-probe failures.

- **`SandboxNotificationMiddleware` background-completion delivery.**
  `build_sandbox_backend()` was returning a fresh `HTTPSandbox` from
  every graph factory; with 11 graphs in `langgraph dev`, the bash
  tool registered jobs on instance A (via the `set_sandbox` last-wins
  module-level fallback) while every middleware instance polled its
  own factory-time instance B — every `_jobs.pending_completions()`
  returned an empty list and the `● Background command "..."
  completed (exit code N)` reminder never reached the agent.
  `build_sandbox_backend()` is now `(base_url, token)`-keyed
  cached so every graph + middleware + tool sees the same
  `BackgroundJobTracker`. Tests that monkeypatch the env keep their
  isolation; multi-tenant pools targeting different daemons
  keep separate clients. (#623)
- **BHCE PostgreSQL bootstrap on v1.1.6/v1.1.7 → v1.1.8 upgrades.**
  The Postgres `init.d` script only runs on an empty data volume, so
  any existing user upgrading into the BHCE-Neo4j topology hit a
  `database "bloodhound" does not exist` failure on the first
  `ops_start("ad")`. v1.1.8 adds an idempotent `bhce-postgres-init`
  init service that creates the database (and the BHCE role) before
  BHCE starts, on every cold start. (#618)
- **Benchmark harness no longer crashes on hosts without docker.**
  Every `docker compose` / `docker exec` / `docker network` hygiene
  call in `benchmark/harness.py` now routes through a `_run_docker`
  helper that degrades to a logged no-op (synthetic exit 127) when
  the docker CLI is not on `PATH` — previously `run_challenge()`
  died with `FileNotFoundError` before the provider ever ran, and
  the harness unit tests could only pass on docker-equipped hosts.
- **`HTTPSandbox` retry helper could `raise None`.** When
  `_retry_on_connection_error` was called with `max_retries <= 0`
  the loop body never ran and the trailing `raise last_exc` raised
  `None`; it now raises a `SandboxError` naming the misconfiguration.
- `decepticon.middleware.skillogy`: dropped the no-op
  `wrap_tool_call` / `awrap_tool_call` passthrough overrides (base
  `AgentMiddleware` behavior is identical) and their stale
  `ToolMessage` return annotations.
- `tools.reversing.ghidra_available()` return annotation corrected
  to `dict[str, bool | str]` (it reports install dir / MCP URL
  strings alongside the booleans).
- `test_subagent_streaming` None-id guard tests updated for the
  `session_id` parameter added to `StreamingRunnable._process_messages`
  in v1.1.10 — the fast test lane was red.

## [1.1.6] — 2026-06-01

Re-cut of `v1.1.5` to restore version coherence — no functional change.
During the `v1.1.5` release the PyPI wheels published, but the container
publish was interrupted by a transient GitHub-hosted `ubuntu-24.04-arm`
runner outage; recovery attempts also pushed `1.1.6` wheels to PyPI. With
the outage resolved, `v1.1.6` re-runs the full pipeline on the native arm64
runners so the PyPI wheels, all seven signed multi-arch images, and the
GitHub release are consistent at the highest published version. Carries the
same Skillogy publish fix as `v1.1.5`. (#452)

## [1.1.5] — 2026-06-01

Patch release. Fixes a release-pipeline gap that broke `decepticon start`
for every published install since the Skillogy layer landed in `v1.1.4`.

### Fixed

- **Skillogy image is now published** — the release pipeline never built,
  pushed, verified, or promoted `ghcr.io/purpleailab/decepticon-skillogy`,
  even though the always-on compose `skillogy` service pulls it. Every
  release since `v1.1.4` therefore failed at `decepticon start` with
  `No such image: ghcr.io/purpleailab/decepticon-skillogy:<version>` /
  `error from registry: denied`. `decepticon-skillogy` is now part of the
  multi-arch `docker` build matrix and the `publish-release` verify +
  `:latest` promote lists (and `release-recover.yml`), so a missing image
  now fails the release loudly instead of shipping a broken compose file.
  (#450)

### Changed

- **`make dogfood`** now tears down any prior repo-root stack before
  starting, to avoid a container-name conflict. (#447)
- **`.env.example`** — the `LANGSMITH_API_KEY` placeholder is commented
  out so it no longer triggers 403 tracing-spam when LangSmith tracing is
  off. (#448)

## [1.1.4] — 2026-05-30

Capability + safety expansion on top of `v1.1.3`. Lands the Sisyphus
mega-PR (#350) [16 sub-PRs] and the 6-tier hardening + Offensive Vaccine
runtime (#342), the static-analysis CI arsenal (#343), three new
specialist agents, the Skillogy skill-as-a-service layer, six new
safety/security middleware, and the Makefile-as-single-source-of-truth
CI refactor (#443). The OSS default runtime, public plugin contract
(`decepticon-core` / `decepticon-sdk` surface), and three-package layout
are unchanged. All three Python packages release in lockstep.

### Added

- **Three specialist agents** — `Phisher`, `MobileOperator`,
  `WirelessOperator`. Each ships with a prompt under
  `agents/prompts/standard/` and a factory under `agents/standard/`.
  (#342)
- **Skillogy — skill-as-a-service** — dedicated gRPC + REST layer at
  `packages/decepticon/decepticon/skillogy/` (Dockerfile under
  `containers/skillogy.Dockerfile`). v0.1 design spec in
  `docs/design/skillogy.md`; user docs in `docs/skillogy.md`. Skill
  authoring (`SKILL.md`) is unchanged — Skillogy is a discovery layer
  on top. Ships behind a feature flag until benchmark validation
  passes. (#350, #445)
- **Blue cell — Offensive Vaccine runtime** — `blue_cell/` adds the
  tap + Sigma matcher infrastructure for the attack → defend → verify
  loop. Sigma/YARA → SIEM/EDR push exporters. (#342)
- **Six new safety / security middleware** — `PromptInjectionShield`
  (agent self-defense), `BudgetEnforcementMiddleware` (spend caps),
  `UntrustedOutputMiddleware` (structural quarantine for tool output),
  `HITLApprovalMiddleware` (transport-abstracted human-in-the-loop),
  `RoEMiddleware` (RoE enforcement + HMAC-chained audit log),
  `SkillogyMiddleware` (dynamic skill graph dispatch). (#342, #350)
- **OpenTelemetry exporter** — opt-in spans for engagement / agent /
  tool / LLM events; runs alongside LangSmith. New runtime deps
  `opentelemetry-{api,sdk,exporter-otlp}>=1.27`. (#350)
- **Static-analysis CI arsenal (18 tools)** — Semgrep custom rules
  (`.semgrep/`), bandit, deptry, vulture, refurb, radon, xenon, mypy,
  yamllint added under a new `lint` dependency group. OpenSSF
  Scorecard workflow. Consolidated `security.yml` and
  `security-scan-example.yml` workflows. (#343)
- **SARIF v2.1.0 export** for GitHub code scanning + DefectDojo. (#350)
- **Sandbox tool expansion** — Caido proxy bundle (capture / replay /
  scope / sitemap), persistent Playwright browser sessions
  (`browser_action` multiplex), `tmux pipe-pane → asciicast v2`
  evidence export, WAVE-4 6.1 Buttercup benchmark integration,
  WAVE-5 sandbox tools. (#350)
- **`decepticon-cli`** — `decepticon-cli auth` (headless provider /
  auth introspection); `decepticon-cli scan` + GitHub Action template
  for CI/CD parity. (#342, #350)
- **Runtime infrastructure** — bounded graceful SIGTERM/SIGINT
  shutdown library; append-only engagement `events.jsonl` log;
  record/replay layer for deterministic re-execution; CART skeleton
  with OPPLAN-matrix adapter seam; `SubAgentTaskSpec` data contract
  for scoped child dispatch; skill registry + slug/fuzzy resolver for
  dynamic `load_skill`. (#350)
- **Per-engagement isolation** — sandbox minimum-cap hardening;
  Neo4j per-engagement scoping (closes cross-engagement leak);
  allowlist-only APOC with client-side safety guard; Decepticon
  self-threat-model documented at
  `docs/security/decepticon-threat-model.md`. (#342)
- **Five new security docs** under `docs/security/` — decepticon
  threat model, Neo4j hardening, prompt-injection defense, sandbox
  isolation, sisyphus-pr top-level summary. (#342)
- **`Makefile` two-tier gate** — `make quality` mirrors the CI PR
  lane (`ci-lint` + `ci-test` + CLI + Web); `make quality-strict`
  mirrors the CI main-push lane (coverage 35% gate + full
  basedpyright warning audit). `.github/workflows/ci.yml` dispatches
  via `make` so local and CI cannot drift. (#443)
- **`scripts/check_basedpyright_errors.py`** — testable extraction of
  the inline Python that gated CI on basedpyright errors. (#443)

### Changed

- **Skill metadata schema** — `metadata.kind` (`reporting` |
  `analytic`) added to four `SKILL.md` files (the reporting +
  kill-chain-analysis set) for Skillogy graph node classification.
  Redundant `mitre_attack:` lists cleared on these four; the mappings
  move into typed graph edges. Other SKILL.md files unchanged. (#445)
- **`docs/skills.md`** — heads-up note linking to Skillogy as the
  planned successor to text-matching `SkillsMiddleware`. (#445)
- **`codeql.yml` workflow removed**, replaced by the consolidated
  `security.yml` (Semgrep custom rules + Trivy + bandit, deptry,
  vulture, etc.). (#343)
- **`xbow-validation-benchmarks` submodule** bumped
  `ec45927 → 1c15c32` — XBEN-099-24 FLAG plumbing (4 fixes),
  XBEN-084-24 base image bump (`node:14-alpine` → `node:18-alpine`),
  `.gitignore` for OMC local state. (#444)

### Fixed

- **`bash` tool — cross-thread fallback for HTTPSandbox `ContextVar`**.
  (#345)
- **streaming events** — `tool_call_id` included in
  `subagent_tool_call` / `subagent_tool_result` events so downstream
  consumers can correlate calls and results. (#346)
- **engagement workspace** — auto-materialized at orchestrator start;
  internal paths masked in error messages. (#347)
- **LLM subscription routing** — configured subscriptions used by
  default (auth priority). Fixes a regression introduced by the
  Sisyphus mega-PR. (#351)
- **Claude API `cache_control`** — capped when system blocks share
  content, preventing the 4-block hard limit from being exceeded.
  (#402)
- **OSS launcher UX** — de-duplicate `ctrl+o` hint; silence
  `DECEPTICON_STACK_NAME` compose warning when the stack name is the
  default. (#344)
- **Sandbox zombie reaper** — replaced the in-process SIGCHLD handler
  (clobbered exit codes) with `tini` (`init: true` on the sandbox
  compose service). (#340)
- **`asyncio.wait_for` timeout wrapper** around LiteLLM `acompletion`
  — caps provider hangs. (#297)
- **proxy env vars** inherited into sandbox tmux sessions for
  consistent `HTTP{S}_PROXY` / `NO_PROXY` propagation. (#296)
- **Five `test_initialize_*` mocks** in `test_session_log.py` — cover
  the `_sync_passthrough_env()` calls added by #296. Without this,
  every initialize test raised `StopIteration` under the stricter
  PR gate. (#443)
- **Cross-engagement Neo4j leak**, multiple hardcoded credentials,
  and one `verify=False` regression. (#342)
- Numerous post-#350 audit fixes — semgrep rule exclusions, GHAS
  findings, basedpyright `Optional` guards, codex token leak,
  JWT non-string headers, recording-replay fidelity, reverser
  robustness, RoE FQDN normalization, sandbox token const-time
  comparison, web engagement path traversal, AD BloodHound zipbomb
  stats, others. (#350)

### Security

- **PromptInjectionShield middleware** — agent self-defense against
  prompt injection in tool output. (#342)
- **UntrustedOutputMiddleware** — structural quarantine for tool
  output before it reaches the model. (#342)
- **RoE enforcement + HMAC-chained audit log** — RoE violations are
  rejected at middleware boundary; every dispatch logged with HMAC
  chaining for tamper-evidence. (#342)
- **Per-engagement Neo4j scoping** — closes the cross-engagement leak
  where one engagement's KG findings were visible to another. (#342)
- **Sandbox minimum-cap hardening** — drops unnecessary Linux
  capabilities from the sandbox container by default. (#342)
- **18-tool static-analysis CI** — bandit, Semgrep custom rules,
  Trivy, deptry, vulture, refurb, etc. integrated into the PR gate;
  SARIF uploaded to GitHub code scanning. (#343)

### Notes

- All three Python packages (`decepticon-core`, `decepticon`,
  `decepticon-sdk`) release in lockstep at `1.1.4`.
- `decepticon-core` and `decepticon-sdk` surface (the public
  plugin-author contract) is unchanged in this release. All additions
  land in `decepticon` (the framework).
- Pre-1.0 cleanup mode continues — see the design spec at
  `docs/superpowers/specs/2026-05-23-core-framework-sdk-split-design.md`
  for the rationale.

## [1.1.3] — 2026-05-27

Consolidation release on top of `v1.1.2` (the core/framework/sdk split).
Lands a backlog of contributor PRs across skills, cross-OS support,
reverse engineering, CLI/launcher, web dashboard, vulnresearch, runtime
stability, and CI — each re-reviewed and re-merged on current `main` with
conflicts resolved and dead code dropped. The OSS default runtime,
public API, and three-package layout are unchanged; every change is
additive or a fix.

### Added

- **Native Windows support** — `scripts/install.ps1` PowerShell installer
  (StrictMode, SHA-256 verification, Docker pre-flight); the Go launcher
  gains an OS/arch/distro + Docker-readiness System Check at `onboard`;
  release artifacts now include `windows_amd64` + `windows_arm64`. README +
  setup guide document the native path alongside WSL2. (#281)
- **Podman + nerdctl container runtimes** — the launcher auto-detects
  docker → podman → nerdctl (first reachable wins) with a
  `DECEPTICON_CONTAINER_RUNTIME` override; Podman socket discovery injects
  `DOCKER_HOST` so nested Docker-API consumers keep working. Docker users
  see zero behavioral change. (#292)
- **Ghidra 12.1 reverse-engineering backend** — `decepticon/tools/reversing/ghidra.py`
  (headless `analyzeHeadless` + optional MCP-bridge sidecar): `ghidra_analyze`,
  `ghidra_decompile`, `ghidra_xrefs`, `ghidra_status`. Gated behind
  `INSTALL_REVERSING=false` so the default sandbox image stays lean; the
  `ghidra-mcp` sidecar opts in via the `reversing` compose profile. (#288)
- **76 new skill playbooks** across two batches, all under the canonical
  `<skill-name>/SKILL.md` layout with `metadata.when_to_use` routing:
  - AD (ADCS ESC1/coercer/ntlm-relay/dcsync/kerberoasting/LAPS/netexec…),
    Cloud (IMDS/k8s/S3/Terraform + container escapes), Smart Contracts
    (access-control/flash-loan/oracle/signature-replay/proxy + bridge/
    governance/MEV), Web Exploit (jwt/oauth/saml/nosqli/…), LLM Red Team
    (AATMF T01–T15, under `plugins/`), Mobile, Reverser, Supply Chain. (#281, #291)
  - Modern API (gRPC/SOAP/WebSocket/SSE), ICS-OT (Modbus/BACnet/S7Comm/DNP3,
    with SAFETY-CRITICAL write-scope confirmation), C2 (Havoc/Mythic). (#291)
- **AD attack tooling** — new `delegation.py` (unconstrained/constrained/RBCD),
  `gpo.py` (GPO ACL abuse), `shadow_creds.py` (msDS-KeyCredentialLink);
  BloodHound-CE ingest format; `dcsync` multi-domain; kerberos AES128
  pre-auth pattern. (#290)
- **Web `@tool` surface** — `http_request` / `http_history` exposed to the
  agent; graphql IDOR heuristic; OAuth state-length + PKCE-downgrade checks. (#290)
- **Release scaffolding** — `RELEASE.md` documenting the 0.0.0-sentinel +
  tag-time version stamping flow; the Soundwave engagement bundle and its
  docs aligned to the full 8-document output. (#287)

### Fixed

- **Soundwave interview loop** — the picker's `" (Recommended)"` UI marker
  leaked into the agent's tool-result, so the model treated it as part of
  the engagement name, rejected it, and re-asked the same question forever.
  `ask_user_question` now strips the trailing marker on the agent-visible
  return (single + multi-select), leaving the picker UI unchanged. (#339, issue #328)
- **Codex/ChatGPT OAuth handler** dropped function names mid-stream
  (synthesized `function_call` had `name=""`, looping the model with
  "is not a valid tool") — added `response.output_item.added/.done`
  handlers. Fixes the empty-tool-name error reported in #321. (#295)
- **Streaming**: `StreamingRunnable` now subclasses `RunnableBinding` so it
  survives deepagents' `_get_subagents()` `.with_config()` call — the
  LangGraph Platform HTTP `stream_mode=["custom"]` path now delivers
  `subagent_*` events (was 0). (#324)
- **Sandbox zombie processes** — reparented `tmux`/`bash` grandchildren
  accumulated as `<defunct>` zombies until the PID table filled and
  `fork()` failed (`EAGAIN`). Now reaped by an init process (tini, run as
  PID 1 via `init: true` on the sandbox compose service) plus
  `kill_all_sessions()` on daemon shutdown. An earlier in-process SIGCHLD
  reaper was replaced after it was found to race with the daemon's own
  `subprocess.run` calls and clobber command exit codes to 0. (#336, #340)
- **`langgraph dev` BlockingError** — a sync `httpx` call inside the
  third-party `deepagents` subagent dispatch aborted runs ~85s in. The
  langgraph service now defaults to `--allow-blocking` (downgrades to a
  warning), with `LANGGRAPH_STRICT_ASYNC=1` to restore fatal behavior for
  debugging. Complements #295's structural fix for Decepticon's own sync
  calls. (#333)
- **LiteLLM truncated tool_use** — Claude models had no `max_tokens`, so
  LiteLLM fell back to its 4096 default and cut off 30–50KB report writes.
  Set per-model caps (Opus 4.7 = 128k, Sonnet/Haiku = 64k) across all three
  model groups. (#295)
- **poc.py inverted ZFP logic** — valid findings that demonstrated impact
  were being rejected; sandbox-runner errors now sentinel-prefixed. (#290)
- **CVE lookups**: capped 3 unbounded `httpx.AsyncClient` timeouts (NVD 30s /
  OSV 15s) that caused intermittent false-negatives. (#294)
- **`bash_kill` BlockingError** under `langgraph dev` — `session_log_path()`
  wrapped in `asyncio.to_thread`. **GraphRecursionError** — 7 sub-agents
  bumped 250 → 1000. (#295)
- **Web dashboard (≈16)** — terminal clears on tab switch (resize-to-0 PTY
  corruption), heartbeat pong-timer leak, health API real probe, N+1
  findings fetch, duplicate-name 409, infinite redirect loop, unmount
  guards, O(n²) event accumulation, findings parser CVSS/CWE/MITRE. (#307)
- **CLI Ink TUI** — empty-filter `selectedIndex=-1`, autocomplete dedupe,
  O(1) event push, tilde expansion, synchronous update check, subagent id
  no longer hardcoded. (#285, #307)
- **Silent exception swallows** surfaced to `log.debug` across
  research `_state`/`chain`/`cve` and the prompt compat shim. (#289, #294)
- **Docker startup race** — `sandbox` now waits on `neo4j` via
  `service_healthy` (was `service_started`). **sandbox.pids_limit** 1024 →
  4096 for parallel Go/Rust toolchains. (#307, #295)
- **Local test suite on macOS/Windows** — `posixpath` for virtual workspace
  paths, `pytest -n auto` class-state isolation, `USERPROFILE` alongside
  `HOME` in launcher tests. (#286, #284)

### Changed

- **Skill layout unified** to nested `<skill-name>/SKILL.md` (canonical
  Agent Skills spec) — migrated 23 legacy flat `exploit/web/*.md` +
  `recon/web-recon/*.md` files; 25+ `load_skill()` routing references
  updated. (#291 review follow-up)
- **`prompts/__init__.py`** (533 lines) split into a re-export shim +
  `builder.py` + `registry.py`; `llm/factory.py` and `sandbox_kernel`
  oversized helpers extracted. Public API unchanged. (#289)
- **Retired the dead docker-exec transport** — `_docker_tmux` → `_tmux`,
  `exec_prefix` defaults to `[]`; `HTTPSandbox` → in-container
  `DaemonSandbox` is the only path. (#289 review follow-up)
- **`DECEPTICON_LLM__TIMEOUT`** default 120s → 600s for long Opus
  generations. (#295)

### Dev infrastructure

- **Pre-commit hook gate** — file hygiene + shellcheck + hadolint + typos
  (with a `.typos.toml` allowlist for offensive-security jargon), run on
  every PR via a `pre-commit` CI job. (#293)
- **Tree-wide LF renormalization** — 45 files committed with CRLF brought
  into line with the `.gitattributes` `eol=lf` policy. (#293)
- **CI matrix** — Python lane is ubuntu-only by design (the backend runs in
  the Linux langgraph container); the Go launcher runs ubuntu+macOS+windows;
  PR-time `linux/arm64` Docker smoke build for cli+langgraph. Coverage gate
  raised 30% → 35%. (#284, #292, #318, #310)
- **Repo hygiene** — `skills/_corpus/` ignored, stale `clients/ee/` ignore
  rules removed, internal design specs untracked, `xbow-validation-benchmarks`
  submodule bumped to the buster apt-archive fix. (#282)

## [1.1.2-localfixes.1] — 2026-05-25 (fork — mohamedq9900/Decepticon)

Runtime-stability fork of upstream `v1.1.2` carrying four targeted fixes
that surfaced under sustained engagement load (multi-hour audits, large
report writes, parallel sub-agent fan-out). All changes are minimal and
preserve upstream contracts.

### Fixed

- **`bash_kill` BlockingError under `langgraph dev`** —
  `_sandbox.session_log_path()` was called synchronously inside the
  ASGI event loop, tripping `blockbuster`'s detector with
  `BlockingError: socket.socket.send`. Wrapped in `asyncio.to_thread`
  to match the pattern already used for the surrounding `kill_session`
  call. The CLI previously surfaced this as "An internal error
  occurred" on every successful session kill.
  ([`packages/decepticon/decepticon/tools/bash/bash.py`](packages/decepticon/decepticon/tools/bash/bash.py))

- **`GraphRecursionError: Recursion limit of 250 reached`** —
  bumped `_RECURSION_LIMIT` from `250` to `1000` on seven sub-agents
  that genuinely need deeper graphs for large engagements (recon
  sweeps with many candidates, parallel CVE probes, multi-target
  static analysis): `analyst`, `cloud_hunter`, `contract_auditor`,
  `ad_operator`, `reverser`, `exploiter`, `vulnresearch`. Other
  agents (`recon`, `exploit`, `postexploit`, `soundwave`,
  orchestrator) were already sized at ≥400 and remain unchanged.
  Cap of 1000 was chosen to cover observed worst-case depth without
  unbounded headroom.
  ([`packages/decepticon/decepticon/agents/standard/*.py`,
  `packages/decepticon/decepticon/agents/plugins/*.py`](packages/decepticon/decepticon/agents/))

- **`auth/gpt-*` Codex OAuth handler dropped tool names mid-stream** —
  the streaming handler only processed
  `response.function_call_arguments.delta` events, which carry the
  arguments fragment but NOT the function `name`. Synthesized
  function_calls ended up with `name=""`; on the next turn the model
  saw a history full of mis-named tool_calls and looped re-calling
  the same tool (e.g. `load_skill`) because tool_results couldn't be
  linked back to the original call. Added handlers for
  `response.output_item.added` (primary path, captures `name` +
  `call_id` when the function_call item starts) and
  `response.output_item.done` (defensive backfill for upstream
  variants that emit only `done`).
  ([`config/codex_chatgpt_handler.py`](config/codex_chatgpt_handler.py))

- **LiteLLM truncated `tool_use` JSON mid-`content` field** —
  `litellm_params` for every Claude model omitted `max_tokens`, so
  LiteLLM defaulted Anthropic requests to its 4096-token OpenAI
  fallback. Long `write_file` calls (a typical 30-50 KB markdown
  report ≈ 10-15 K output tokens) were cut off mid-stream and the
  `content` field arrived missing from the parsed tool_use, yielding
  `content: Field required` validation errors. Set `max_tokens`
  explicitly per model to match Claude Code's canonical caps:
  Opus 4.7/4.6 → 128000, Sonnet 4.6 → 64000, Haiku 4.5 → 64000.
  Applied to all three groups (`anthropic/`, `auth/`,
  `openrouter/anthropic/`).
  ([`config/litellm.yaml`](config/litellm.yaml))

### Changed

- **`sandbox.pids_limit`: `1024` → `4096`** — analyst sub-agents that
  drive Go/Rust toolchains in parallel (`gosec` + `cargo` + `semgrep`
  × fan-out) blow through the 1024-pid cgroup cap, then
  `subprocess.run()` in the sandbox FastAPI daemon fails with
  `BlockingIOError: [Errno 11] Resource temporarily unavailable`,
  which the daemon surfaces as HTTP 500 and the CLI prints as "An
  internal error occurred". 4096 has held under multi-hour Cosmos
  and Web3 audits.
  ([`docker-compose.yml`](docker-compose.yml))

- **Default `DECEPTICON_LLM__TIMEOUT`: `120` → `600` (10 min)** —
  with `max_tokens` bumped to 128K (fix above), long Opus generations
  with extended thinking + large tool_use payloads routinely exceed
  120s mid-stream. The langgraph httpx client aborted the connection
  while LiteLLM proxy kept streaming successfully (200 OK in proxy
  logs), surfacing in the CLI as `APITimeoutError: Request timed
  out`. Documented in `.env.example`; defaults pass through via the
  `DECEPTICON_LLM__*` Pydantic settings.
  ([`.env.example`](.env.example))

### Compatibility

- Patches apply on top of upstream `v1.1.2` commit `e1afba6`.
- No API or import surface changed; downstream code that imports
  any of the modified modules works unchanged.
- Reverting any single fix is a one-line revert against this branch.

### Tested against

Fedora 43, Docker 27.x, SELinux permissive. Engagement workload:
sustained multi-hour Cosmos / Web3 / Web2 bug-bounty audits with
parallel sub-agent fan-out, large recon outputs, and 30-50 KB
report writes on `auth/claude-opus-4-7` and `auth/gpt-5.5`.

## [1.1.2] — 2026-05-23

This release introduced the three-package split (additive — every
legacy import path keeps working via compat shims), shipped as
``v1.1.2`` on the OSS series. Removal of
the compat shims, ``PluginBundle`` aggregate shape, and the legacy
``decepticon.agents.middleware_slots.MiddlewareSlot`` re-export is
deferred to ``2.0.0`` (see "Deprecated" table below).

### Added — three-package split (core / framework / sdk)

OSS shifts from a monolithic `decepticon` wheel to three coordinated
wheels. The split exposes a stable contract layer that commercial
products, downstream frameworks, and the community can extend without
touching framework internals. Full design rationale in the
core/framework/sdk split design spec.

- **`decepticon-core`** (new) — pure types, protocols, plugin contracts,
  registry primitives. Zero `langchain` / `langgraph` / `deepagents` /
  `httpx` / `fastapi` runtime dependency. Safe to pin from any context
  (CLI tooling, serverless workers, type-check-only environments).
  - 7 runtime-checkable `Protocol`s for plugin authors
    (`BackendProtocol`, `MiddlewareProtocol`, `ToolProtocol`,
    `CallbackProtocol`, `LLMProtocol`, `SandboxProtocol`,
    `AgentProtocol`).
  - 5 focused contribution dataclasses (`ToolContribution`,
    `MiddlewareContribution`, `PromptContribution`,
    `SubAgentContribution`, `SafetyDeclaration`) replacing the
    kitchen-sink `PluginBundle` shape.
  - `RoleRegistry`, `SkillSourceRegistry`, `PluginRegistry` with
    `PluginConflictWarning` + `RoleResolution` introspection types.
- **`decepticon-sdk`** (new) — single-import surface for plugin
  authors. Re-exports 23 stable symbols from `decepticon-core`. Ships
  `decepticon_sdk.testing` (`FakeBackend` / `FakeLLM` / `FakeSandbox`
  that satisfy their respective `Protocol`s at runtime) and a
  `decepticon-sdk plugin new` scaffolder covering six plugin kinds
  (tool / middleware / agent / callback / skill / prompt).
- **`decepticon`** (relocated to `packages/decepticon/src/decepticon/`) —
  the opinionated framework. Same agent factories, middleware, tools,
  LLM router as before; depends on `decepticon-core` for every
  contract surface it touches.

### Added — plugin extension primitives (closes 9 of 12 spec §8 gaps)

- `make_agent_backend(extra_routes=...)` with longest-prefix-wins
  routing (closes gap #1, gap #3). Tenant-specific paths like
  `/skills/tenant/<id>/` deterministically override the generic
  `/skills/` default — load-bearing per the split design spec
  §16.4 #5 for the future B2B Enterprise tier.
- `RoleRegistry.register(name, *, slots, skill_sources,
  llm_role_fallback)` for custom agent roles (closes gap #5).
  Idempotent on identical parameters (multi-process worker startup
  safe). The framework registers all 16 OSS roles at boot via
  `decepticon._boot.run()`.
- `PluginRegistry.load()` walks the nine `decepticon.*` entry-point
  groups (`tools`, `middleware`, `agents`, `subagents`, `callbacks`,
  `skills`, `bundles`, `roles`, `prompts`) and surfaces same-key
  collisions as `PluginConflictWarning` (closes gap #4, gap #7).
- `SkillSourceRegistry.register(source, owner)` validates `/skills/`
  prefix + collision detection (closes gap #12). Malformed paths
  fail registration loudly.
- `SafetyDeclaration` for plugin-extended safety-critical
  tool/middleware names (closes gap #10). Additive-only per the split
  design spec §16.4 #4 — plugins cannot remove safety on OSS-declared
  names.
- `PromptContribution` + `decepticon.prompts` entry-point group for
  prompt-only plugins (closes gap #8). No longer requires wrapping in
  `PluginBundle`.
- `roles=` / `parent_agents=` now explicitly required on every
  contribution (closes gap #6). Empty tuple raises at registration.

### Added — author tooling + docs

- Scaffolding CLI: `decepticon-sdk plugin new --kind=KIND --name=NAME
  --path=PATH`. Generates a buildable plugin package (`pyproject.toml`
  + `README.md` + `src/<module>/__init__.py`) wired to the matching
  entry-point group.
- Six runnable example plugins under
  [`packages/decepticon-sdk/examples/`](packages/decepticon-sdk/examples/),
  one per kind. All six build to wheel + sdist via `uv build`.
- New audience-specific guides:
  - [`docs/plugin-author-guide.md`](docs/plugin-author-guide.md)
  - [`docs/library-consumer-guide.md`](docs/library-consumer-guide.md)
  - [`docs/contributor-architecture.md`](docs/contributor-architecture.md)
  - [`docs/migration/from-0.0.x.md`](docs/migration/from-0.0.x.md)

### Changed

- Source tree relocated: `decepticon/` and `tests/` moved into
  `packages/decepticon/src/decepticon/` and
  `packages/decepticon/tests/` respectively (history preserved via
  `git mv`). End-user CLI commands and the Docker stack UX are
  unchanged.
- The root `pyproject.toml` is now a workspace umbrella
  (`[tool.uv] package = false`). Workspace members live under
  `packages/*`. Run `uv sync` from the workspace root to install all
  three packages in lockstep.
- Framework imports rewritten to consume `decepticon_core.*` directly
  (71 files). Legacy import paths keep working via thin re-export
  shims for one release; see migration guide.
- `containers/langgraph.Dockerfile` switches to `uv sync --no-dev
  --frozen --extra neo4j` against the workspace; `langgraph.json`
  graph paths repointed to `./packages/decepticon/src/decepticon/`.

### Deprecated

The following legacy import paths keep working but emit a
`DeprecationWarning` via `decepticon.compat.register_legacy_imports()`
(default-on; opt-out via `DECEPTICON_NO_COMPAT=1`). Shims removed at
**2.0.0**.

| Legacy path | Canonical path |
|-------------|----------------|
| `decepticon.core.schemas` | `decepticon_core.types.engagement` |
| `decepticon.llm.models` | `decepticon_core.types.llm` |
| `decepticon.tools.research.graph` | `decepticon_core.types.kg` |
| `decepticon.plugin_loader` | `decepticon_core.plugin_loader` |
| `decepticon.core.config` | `decepticon_core.utils.config` |
| `decepticon.core.logging` | `decepticon_core.utils.logging` |
| `decepticon.agents.middleware_slots.{MiddlewareSlot, SLOTS_PER_ROLE, SAFETY_CRITICAL_SLOTS}` | `decepticon_core.contracts.slots.*` |

### Notes

- `decepticon-core` LOC: 4,130 (spec §10 Phase 6 budget: ≤4,000).
  Modest over-shoot from the registry + protocols modules; trim in a
  follow-up if it remains a concern. None of the over-budget code
  imports langchain/langgraph/deepagents (defended by
  [`test_no_runtime_deps`](packages/decepticon-core/tests/test_no_runtime_deps.py)).
- All three packages ship a PEP 561 `py.typed` marker.
- Three packages release in lockstep with a single version string
  stamped from the git tag — verified by the release workflow at tag
  time.

### Deferred to subsequent releases

- `LLMFactory` consumption of `RoleRegistry.skill_sources` /
  `llm_role_fallback` fields (completes gap #5).
- `PluginRegistry.introspect_role()` real implementation (completes
  gap #7; currently a typed stub).
- Per-import `DeprecationWarning` emission via `sys.modules` aliasing
  (current implementation emits a single boot-time warning listing all
  legacy paths).
- Ruff `flake8-tidy-imports.banned-api` rule for `decepticon-core`
  (defended by runtime test at present).
- PyPI Trusted Publisher OIDC configuration for the three-wheel
  atomic release.
- Downstream plugin package lockstep migration.

[Unreleased]: https://github.com/BitterSecurity/Decepticon/compare/v2.0.0...HEAD
[2.1.1]: https://github.com/BitterSecurity/Decepticon/compare/v2.0.0...v2.1.1
[2.1.0]: https://github.com/BitterSecurity/Decepticon/compare/v2.0.0...6243c51640aed28fd68834d05e728f634eb40855
[2.0.0]: https://github.com/BitterSecurity/Decepticon/compare/v1.6.1...v2.0.0
[1.6.1]: https://github.com/BitterSecurity/Decepticon/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/BitterSecurity/Decepticon/compare/v1.5.4...v1.6.0
[1.5.4]: https://github.com/BitterSecurity/Decepticon/compare/v1.5.2...v1.5.4
[1.5.2]: https://github.com/BitterSecurity/Decepticon/compare/v1.5.0...v1.5.2
[1.5.0]: https://github.com/BitterSecurity/Decepticon/compare/v1.4.4...v1.5.0
[1.4.4]: https://github.com/BitterSecurity/Decepticon/compare/v1.4.3...v1.4.4
[1.4.3]: https://github.com/BitterSecurity/Decepticon/compare/v1.4.2...v1.4.3
[1.4.2]: https://github.com/BitterSecurity/Decepticon/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/BitterSecurity/Decepticon/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/BitterSecurity/Decepticon/compare/v1.3.1...v1.4.0
[1.3.1]: https://github.com/BitterSecurity/Decepticon/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/BitterSecurity/Decepticon/compare/v1.2.5...v1.3.0
[1.2.2]: https://github.com/BitterSecurity/Decepticon/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/BitterSecurity/Decepticon/compare/v1.2.0...v1.2.1
