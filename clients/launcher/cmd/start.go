package cmd

import (
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"time"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/cmd/opscontrol"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/compose"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/engagement"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/health"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/migrate"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/platform"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/starprompt"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/ui"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/updater"
	"github.com/spf13/cobra"
)

func nullDevice() string {
	if runtime.GOOS == "windows" {
		return "NUL"
	}
	return "/dev/null"
}

// Indirected so tests can swap WSL detection without touching the
// real /proc/version or /etc/resolv.conf on the host they run on.
var (
	isWSLFn     = platform.IsWSL
	wslHostIPFn = platform.WSLHostIP

	// Indirected so tests can assert which AUTO_UPDATE branch ran
	// without making network calls into GitHub.
	autoUpdateFn   = updater.AutoUpdateIfAvailable
	promptUpdateFn = updater.PromptIfUpdateAvailable
)

// applyAutoUpdate dispatches the self-update check based on AUTO_UPDATE.
// See the inline comment in start() (section 2.5) for the value table
// and rationale for default-on behaviour.
//
// The `skip` argument is the --no-update CLI flag. When true it
// short-circuits *before* the env lookup so the operator's one-shot
// override always wins over the persistent .env setting — including
// AUTO_UPDATE=true and AUTO_UPDATE=prompt. This is the escape hatch
// for "I know there's a newer version, I want this exact binary".
//
// Unrecognized values (e.g. AUTO_UPDATE=disabled, where the operator
// probably MEANT "false") fall through to the prompt path rather than
// silent auto-update. Fail-loud beats fail-silent — an unexpected
// re-exec is harder to debug than a one-line prompt.
func applyAutoUpdate(env map[string]string, version string, skip bool) {
	if skip {
		return
	}
	// Which release stream to track (stable | latest). Default stable.
	ch := updater.ResolveChannel(config.Get(env, "DECEPTICON_CHANNEL", ""))
	switch strings.ToLower(strings.TrimSpace(config.Get(env, "AUTO_UPDATE", ""))) {
	case "", "true", "1", "yes", "on":
		if _, err := autoUpdateFn(version, ch); err != nil {
			ui.Warning("Auto-update: " + err.Error())
		}
	case "false", "0", "no", "off":
		// self-update disabled
	default:
		// Includes the explicit `prompt` / `ask` / `interactive`
		// opt-ins AND any unrecognized value (safer fallback).
		if _, err := promptUpdateFn(version, ch); err != nil {
			ui.Warning("Update check: " + err.Error())
		}
	}
}

// skipUpdate is bound to --no-update. One-shot override for the
// AUTO_UPDATE=true (now default) self-update path: useful in CI, when
// debugging a specific launcher version, or when intentionally
// running an older release against a known-good stack.
var skipUpdate bool

var startCmd = &cobra.Command{
	Use:   "start",
	Short: "Start Decepticon services and launch the CLI",
	RunE:  runStart,
}

func init() {
	rootCmd.AddCommand(startCmd)

	// PersistentFlag on root → inherited by `start`, so both
	// `decepticon --no-update` (no subcommand → runStart) and
	// `decepticon start --no-update` accept the flag.
	rootCmd.PersistentFlags().BoolVar(&skipUpdate, "no-update", false,
		"Skip the self-update check for this launch (does not change AUTO_UPDATE in .env)")

	rootCmd.RunE = func(cmd *cobra.Command, args []string) error {
		return runStart(cmd, args)
	}
}

func runStart(cmd *cobra.Command, args []string) error {
	// 1. Check .env exists
	if !config.EnvExists() {
		ui.Warning("No configuration found. Running setup wizard...")
		fmt.Println()
		if err := runOnboard(cmd, nil); err != nil {
			return err
		}
		fmt.Println()
	}

	// 1.5. One-shot migration for v1.1.7→v1.1.8 upgraders.
	// Old .env files ship with an active "COMPOSE_PROFILES=c2-sliver"
	// line that forces every default start to bring up the Sliver C2
	// container. ADR-0006 routes specialist workloads through ops_start
	// instead; the stale line is silently rewritten to a comment (with
	// a one-line notice and a .env.bak backup) before LoadEnv reads it,
	// so the rest of the boot path sees the post-migration state.
	if rewrote, mErr := config.MigrateActiveComposeProfiles(config.EnvPath()); mErr != nil {
		ui.Warning("Could not migrate stale COMPOSE_PROFILES in .env: " + mErr.Error())
	} else if rewrote {
		ui.Info("Migrated stale COMPOSE_PROFILES in .env (specialist workloads now spawn via ops_start). Backup at " + config.EnvPath() + ".bak")
	}

	// 2. Load and validate .env
	env, err := config.LoadEnv(config.EnvPath())
	if err != nil {
		return fmt.Errorf("load config: %w", err)
	}

	// 2.1. Apply idempotent config/policy migrations for already-onboarded
	// users. They never re-run the onboard wizard (step 1 only fires when
	// .env is absent), so this is how a new release's env keys and policy
	// changes reach them: env-key backfill (every start) + one-time
	// interactive re-consent prompts (deferred on non-TTY). New steps plug
	// into internal/migrate.Registry — no wiring needed here.
	migrate.RunAll(env)

	if err := config.ValidateAuth(env); err != nil {
		return err
	}

	// Verify Docker is available
	if _, err := exec.LookPath("docker"); err != nil {
		return fmt.Errorf("Docker is not installed or not in PATH. Install from https://docs.docker.com/get-docker/")
	}
	// Verify Docker Compose V2
	out, err := exec.Command("docker", "compose", "version").CombinedOutput()
	if err != nil {
		return fmt.Errorf("Docker Compose V2 is required. Got: %s. Upgrade Docker Desktop or install the compose plugin.", strings.TrimSpace(string(out)))
	}

	// Warn — don't block — if Ollama is selected but the URL doesn't
	// reach a running server. We translate ``host.docker.internal`` to
	// ``localhost`` for the host-side probe; from inside the litellm
	// container the original URL is what gets used at runtime.
	probeOllamaIfSelected(env)

	// 2.3. Ensure config files exist (docker-compose.yml, litellm.yaml, workspace)
	home := config.DecepticonHome()
	composePath := filepath.Join(home, "docker-compose.yml")
	if _, err := os.Stat(composePath); os.IsNotExist(err) {
		// Use installed version tag; fall back to branch for dev builds
		ref := "v" + version
		if version == "dev" || version == "" {
			ref = config.Get(env, "DECEPTICON_BRANCH", "main")
		}
		ui.Info("Downloading configuration files...")
		// release == nil here: this branch only triggers when compose
		// files are missing on launch (e.g. user wiped ~/.decepticon), so
		// we lack the prefetched Release object that ApplyUpdate carries.
		// SyncConfigFiles falls back to unverified download with a
		// warning — the install.sh path is the verified-by-default entry.
		if err := updater.SyncConfigFiles(ref, nil); err != nil {
			return fmt.Errorf("sync config: %w", err)
		}
	}

	// Ensure workspace directory exists
	_ = os.MkdirAll(filepath.Join(home, "workspace"), 0o755)
	// Same for the telemetry dir the langgraph container mounts: if Docker
	// creates it first it lands root-owned, and the host-side CLI can no longer
	// write the anonymous install id.
	_ = os.MkdirAll(filepath.Join(home, "telemetry"), 0o755)

	// Ensure DECEPTICON_HOME is set in .env (Docker Compose needs absolute path)
	if config.Get(env, "DECEPTICON_HOME", "") == "" {
		env["DECEPTICON_HOME"] = home
		if err := config.AppendEnvLine(config.EnvPath(), "DECEPTICON_HOME", home); err != nil {
			ui.Warning("Could not set DECEPTICON_HOME in .env: " + err.Error())
		}
	}
	if err := migrateLegacyCredentials(env); err != nil {
		return fmt.Errorf("upgrade legacy credentials: %w", err)
	}

	// 2.6. Set CLAUDE_CREDENTIALS_VOLUME for conditional mount in docker-compose.
	// When the credentials file exists, mount it into litellm. Otherwise mount
	// /dev/null (NUL on Windows) so docker doesn't create it as a directory.
	userHome, homeErr := os.UserHomeDir()
	if homeErr != nil {
		// A failed home-dir resolution must not fall through to a relative
		// ".claude/..." path, which os.Stat would resolve against the CWD and
		// mis-mount. Skip the optional host credential mounts; onboarding can
		// supply them later.
		ui.Warning("Could not resolve home directory; skipping host credential mounts: " + homeErr.Error())
		_ = os.Setenv("CLAUDE_CREDENTIALS_VOLUME", nullDevice())
		_ = os.Setenv("CODEX_AUTH_VOLUME", nullDevice())
	} else {
		credsPath := filepath.Join(userHome, ".claude", ".credentials.json")
		if _, statErr := os.Stat(credsPath); statErr == nil {
			_ = os.Setenv("CLAUDE_CREDENTIALS_VOLUME", credsPath)
		} else {
			_ = os.Setenv("CLAUDE_CREDENTIALS_VOLUME", nullDevice())
		}

		// Same pattern for the Codex CLI credential store at ~/.codex/auth.json.
		// The new auth/ ChatGPT handler reads (and writes) this file directly so
		// a host-side `codex login` flows into the container without a rebuild.
		codexAuthPath := filepath.Join(userHome, ".codex", "auth.json")
		if _, statErr := os.Stat(codexAuthPath); statErr == nil {
			_ = os.Setenv("CODEX_AUTH_VOLUME", codexAuthPath)
		} else {
			_ = os.Setenv("CODEX_AUTH_VOLUME", nullDevice())
		}
	}

	// 2.5. Update check, gated by AUTO_UPDATE in .env:
	//   unset (default) → fully unattended: apply the update + re-exec
	//   true / 1 / yes  → same as default (kept for backwards-compat)
	//   prompt / ask    → interactive prompt on a TTY, passive notice otherwise
	//   false           → skip entirely (air-gapped / version-pinned deploys)
	//
	// Default-on rationale: every released hotfix is dead weight until the
	// operator picks it up. The previous default ("prompt on TTY, notice
	// otherwise") meant a bug-fix release sat on the user's machine,
	// un-applied, until they noticed the notice and re-ran. Silent self-
	// update collapses the "fix shipped" → "fix running on user host" gap
	// from days/weeks to the next `decepticon start`, which is the same
	// behaviour Discord/Slack/electron-updater apps already use.
	//
	// Synchronous on purpose: the prompt + unattended paths apply and re-exec
	// before the rest of `start` proceeds. The GitHub fetch fails fast so a
	// slow network never blocks startup.
	applyAutoUpdate(env, version, skipUpdate)

	// 2.6. One-time GitHub star ask. Idempotent across launches — the
	// ack file at $DECEPTICON_HOME/.starred suppresses the prompt
	// after the user has been through it once. Silent no-op on
	// non-interactive stdin, so CI / piped invocations are untouched.
	starprompt.PromptIfNotStarred()

	// 3. Engagement picker — must run BEFORE compose Up so the sandbox
	// container starts with /workspace bound to the chosen engagement
	// directory. Without this, the operator would briefly see the whole
	// workspace through the sandbox before any picking happens.
	fmt.Println()
	choice, err := engagement.Select(home)
	if err != nil {
		return err
	}
	// Export the bind path. composeEnv() forwards os.Environ(), so docker
	// compose interpolates ${DECEPTICON_ENGAGEMENT_WORKSPACE} from this var.
	if err := os.Setenv("DECEPTICON_ENGAGEMENT_WORKSPACE", choice.WorkspacePath); err != nil {
		return fmt.Errorf("set engagement workspace env: %w", err)
	}

	// 4. Spawn the opscontrol daemon BEFORE `compose up` so the
	// langgraph socket bind-mount has a real socket to attach to.
	// ADR-0006 §1' — daemon owns docker socket; agent reaches it
	// only via the langgraph-only UDS mount.
	sock, err := opscontrol.EnsureRunning()
	if err != nil {
		// Non-fatal for backwards compatibility with stacks that
		// have not yet adopted the docker-compose.opscontrol.yml
		// override: agent ops_* tools will return a "daemon
		// unreachable" diagnostic rather than crashing the boot.
		ui.Warning("opscontrol daemon not started: " + err.Error())
	} else if err := os.Setenv("DECEPTICON_OPSCONTROL_SOCK_HOST", sock); err != nil {
		ui.Warning("set DECEPTICON_OPSCONTROL_SOCK_HOST: " + err.Error())
	}

	// 4.5. Refuse to start with default credentials. The compose file
	// ships sk-decepticon-master / decepticon as fallbacks so a bare
	// `docker compose up` still works for dev, but the launcher is the
	// supported path — a default-keyed LiteLLM proxy on a reachable host
	// is a free LLM proxy for anyone who finds it. Fail loud with the fix.
	if err := checkDefaultCredentials(env); err != nil {
		return err
	}

	// 5. Start services
	c := compose.New()

	ui.Info("Starting Decepticon services...")
	if err := c.Up(compose.Profiles.CLI); err != nil {
		return fmt.Errorf("start services: %w", err)
	}

	// 5. Health checks
	if err := health.WaitForServices(env); err != nil {
		return err
	}

	// 6. Launch CLI
	fmt.Println()
	ui.Info("Launching Decepticon CLI...")

	cliEnv := map[string]string{
		"DECEPTICON_VERSION":      version,
		"DECEPTICON_ASSISTANT_ID": choice.AssistantID,
		"DECEPTICON_ENGAGEMENT":   choice.Engagement,
	}
	if port := config.Get(env, "WEB_PORT", "3000"); port != "" {
		cliEnv["WEB_PORT"] = port
	}

	// Pass through terminal. Services are intentionally left running on CLI exit
	// so re-entry is fast (cold start is ~75s); use 'decepticon stop' to shut
	// the stack down.
	if err := c.RunInteractive(
		[]string{compose.Profiles.CLI},
		"cli",
		cliEnv,
	); err != nil {
		ui.Warning("CLI exited with error: " + err.Error())
		return fmt.Errorf("cli: %w", err)
	}

	ui.DimText("CLI exited. Services kept running — run 'decepticon stop' to shut down.")
	return nil
}

// probeOllamaIfSelected does a best-effort GET on /api/tags to verify the
// user's Ollama server is reachable when `ollama_local` is configured.
// Failures don't block startup — the user might be about to launch
// Ollama, or running on an unusual setup we can't introspect. We just
// surface a hint so they aren't surprised by a 'model not found' on the
// first agent prompt.
//
// On WSL2 the probe walks several candidate hosts because there's no
// single "the host" address: Docker Desktop installs may have
// host.docker.internal in /etc/hosts; native-WSL Docker installs need
// the Windows host IP from /etc/resolv.conf; an Ollama running inside
// the WSL distro itself sits on 127.0.0.1. Whichever returns 2xx wins.
// checkDefaultCredentials refuses to start when compose fallback credentials
// are still in effect. A bare docker compose up remains available for dev,
// while the supported launcher path cannot expose a default-keyed service.
func checkDefaultCredentials(env map[string]string) error {
	insecure := make([]string, 0, len(legacyCredentialDefaults))
	for name := range legacyCredentialDefaults {
		if name == "LITELLM_SALT_KEY" && isLegacyCredential(env, name) && env[legacySaltMarker] == "true" && strings.TrimSpace(env[name]) != "" {
			continue
		}
		if isLegacyCredential(env, name) {
			insecure = append(insecure, name)
		}
	}
	if len(insecure) == 0 {
		return nil
	}
	slices.Sort(insecure)
	return fmt.Errorf(
		"refusing to start with missing or default credentials in %s: %s.\n"+
			"For a new install, delete %s and run `decepticon onboard`.\n"+
			"For an initialized install, do not change database passwords in .env alone; "+
			"back up the engagement, run `decepticon remove`, then onboard again so credentials and volumes are recreated together.",
		config.EnvPath(), strings.Join(insecure, ", "), config.EnvPath())
}

func probeOllamaIfSelected(env map[string]string) {
	priority := strings.ToLower(env["DECEPTICON_AUTH_PRIORITY"])
	hasOllama := strings.Contains(","+priority+",", ",ollama_local,")
	base := strings.TrimSpace(env["OLLAMA_API_BASE"])
	if !hasOllama && base == "" {
		return
	}
	if base == "" {
		ui.Warning("ollama_local selected but OLLAMA_API_BASE is empty — skipping reachability probe.")
		return
	}

	candidates := candidateProbeURLs(base)
	client := &http.Client{Timeout: 2 * time.Second}
	var lastStatus int
	for _, candidate := range candidates {
		resp, err := client.Get(candidate + "/api/tags")
		if err != nil {
			continue
		}
		status := resp.StatusCode
		resp.Body.Close()
		if status < 400 {
			ui.DimText(fmt.Sprintf("Ollama reachable at %s.", base))
			return
		}
		lastStatus = status
	}

	if lastStatus != 0 {
		ui.Warning(fmt.Sprintf(
			"Ollama responded with %d at %s — verify the URL is correct.",
			lastStatus, base,
		))
		return
	}
	ui.Warning(fmt.Sprintf(
		"Ollama not reachable at %s (host-side probe). "+
			"Start it with 'ollama serve' or check OLLAMA_API_BASE.",
		base,
	))
}

// candidateProbeURLs returns the URLs the launcher should probe to
// verify host-side Ollama reachability. The returned list is ordered
// best-first so the loop short-circuits on the most likely candidate.
//
// For URLs that don't reference `host.docker.internal` the list is
// just the URL itself — the user wired up an explicit address (real
// IP, DNS name) and we trust it.
//
// For `host.docker.internal` the resolution depends on platform:
//
//   - Always try the URL verbatim first. Docker Desktop on macOS,
//     Windows, and WSL2 typically populates /etc/hosts with this name.
//   - On WSL, also try the Windows host IP found in /etc/resolv.conf.
//     Native-WSL Docker installs (no Docker Desktop) don't get the
//     hosts entry, but the Windows host is always the WSL2 default
//     nameserver, so this catches the "Ollama on Windows" case.
//   - Always fall back to 127.0.0.1. Native Linux Docker reaches the
//     host loopback via the `extra_hosts: host-gateway` mapping,
//     which on the host is just localhost. On WSL this also catches
//     the "Ollama running inside the WSL distro" case.
func candidateProbeURLs(raw string) []string {
	u, err := url.Parse(raw)
	if err != nil {
		return []string{raw}
	}
	host, port, splitErr := net.SplitHostPort(u.Host)
	if splitErr != nil {
		host = u.Host
		port = ""
	}
	if host != "host.docker.internal" {
		return []string{raw}
	}

	candidates := []string{raw}
	seen := map[string]struct{}{raw: {}}
	add := func(replacement string) {
		v := *u
		if port == "" {
			v.Host = replacement
		} else {
			v.Host = net.JoinHostPort(replacement, port)
		}
		s := v.String()
		if _, dup := seen[s]; dup {
			return
		}
		seen[s] = struct{}{}
		candidates = append(candidates, s)
	}

	if isWSLFn() {
		if hostIP := wslHostIPFn(); hostIP != "" {
			add(hostIP)
		}
	}
	add("127.0.0.1")
	return candidates
}
