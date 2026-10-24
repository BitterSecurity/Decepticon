package cmd

import (
	"context"
	"encoding/json"
	"errors"
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

	"github.com/BitterSecurity/Decepticon/clients/launcher/cmd/infracontrol"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/compose"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/config"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/engagement"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/health"
	opsinternal "github.com/BitterSecurity/Decepticon/clients/launcher/internal/infracontrol"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/migrate"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/platform"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/starprompt"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/ui"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/updater"
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

	promptUpdateFn = updater.PromptIfUpdateAvailable
)

func checkStartupUpdate(env map[string]string, version string, skip bool) {
	if skip {
		return
	}
	// Which release stream to track (stable | latest). Default latest.
	ch := updater.ResolveChannel(config.Get(env, "DECEPTICON_CHANNEL", ""))
	switch strings.ToLower(strings.TrimSpace(config.Get(env, "AUTO_UPDATE", ""))) {
	case "false", "0", "no", "off":
		return
	default:
		if _, err := promptUpdateFn(version, ch); err != nil {
			ui.Warning("Update check: " + err.Error())
		}
	}
}

var (
	skipUpdate     bool
	resumeThread   string
	continueThread bool
	backgroundRun  bool
)

var startCmd = &cobra.Command{
	Use: "start [thread-id]",
	Long: `Start the agent stack, verify service and assistant readiness, then launch the CLI.

The launcher checks updates, prepares the selected engagement, starts the Compose services, verifies LangGraph and opens a session. Services stay running after the CLI exits; use decepticon stop to shut them down.
Use --resume to pick a session, --resume=THREAD_ID or --resume THREAD_ID for a known session, or --continue for the latest session.`,
	Example: `  decepticon start
  decepticon start --no-update
  decepticon start --resume
  decepticon start --resume=THREAD_ID
  decepticon start --continue`,
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
	rootCmd.PersistentFlags().String("workflow", "", "Execution workflow ID (defaults to DECEPTICON_WORKFLOW_ID in environment or .env)")
	rootCmd.PersistentFlags().String("model", "", "Per-session model override (LiteLLM model id, e.g. anthropic/claude-opus-4-7); overrides DECEPTICON_MODEL_OVERRIDE in environment or .env")
	rootCmd.PersistentFlags().String("fallback-model", "", "Per-session fallback IDs (comma-separated); auto restores the configured chain, none disables fallback")
	rootCmd.PersistentFlags().String("effort", "", "Per-session requested reasoning effort: auto, low, medium, high or max (provider support required)")
	rootCmd.PersistentFlags().String("autocompact", "", "Per-session context compaction: auto or token threshold (1024–10000000)")
	rootCmd.PersistentFlags().String("workflow-version", "", "Execution workflow version (requires a workflow ID)")

	rootCmd.PersistentFlags().StringVar(&resumeThread, "resume", "", "Resume a thread by ID, or open the session picker (use --resume=ID)")
	rootCmd.PersistentFlags().Lookup("resume").NoOptDefVal = "picker"
	rootCmd.PersistentFlags().BoolVar(&continueThread, "continue", false, "Continue the latest session in the selected engagement")
	rootCmd.MarkFlagsMutuallyExclusive("resume", "continue")
	startCmd.MarkFlagsMutuallyExclusive("resume", "continue")

	startCmd.Flags().BoolVar(&backgroundRun, "background", false,
		"Start services and prepare the engagement without opening the interactive CLI; attach later with 'decepticon attach <name>'")

	rootCmd.RunE = func(cmd *cobra.Command, args []string) error {
		return runStart(cmd, args)
	}
}

func runStart(cmd *cobra.Command, args []string) error {
	return runStartMode(cmd, args, "", false)
}

// runStartMode boots the stack for one of three callers: plain `start`
// (existingEngagement == ""), the desktop agent service (`serve`, which
// returns once services are ready), and `attach` (attachInteractive, which
// drops the operator into the interactive CLI on an existing engagement).
func runStartMode(cmd *cobra.Command, args []string, existingEngagement string, attachInteractive bool) error {
	if cmd == rootCmd {
		if err := validateRootArgs(args); err != nil {
			return err
		}
	}
	desktopRuntime, err := parseDesktopRuntime(existingEngagement != "")
	if err != nil {
		return err
	}
	terminalOwner, err := desktopTerminalRequest()
	if err != nil {
		return err
	}
	if terminalOwner != nil && (existingEngagement == "" || attachInteractive || desktopRuntime == nil) {
		return fmt.Errorf("owned terminal registration requires Desktop service startup")
	}
	workflowEnv, err := workflowEnvironment(cmd)
	if err != nil {
		return err
	}
	controlsEnv, err := runtimeControlsEnvironment(cmd)
	if err != nil {
		return err
	}
	thread := resumeThread
	if len(args) > 0 {
		if len(args) != 1 || thread != "picker" {
			return fmt.Errorf("unexpected arguments; use --resume=THREAD_ID or --continue")
		}
		thread = args[0]
	}
	updater.CleanupOldBinary()
	ui.Info("[1/5] Configuration · checking credentials and settings")
	// 1. Check .env exists
	if !config.EnvExists() {
		if existingEngagement != "" {
			if desktopRuntime == nil {
				return fmt.Errorf("complete Decepticon CLI setup before starting the desktop service")
			}
			if err := bootstrapDesktopEnv(); err != nil {
				return err
			}
		} else {
			ui.Warning("No configuration found. Running setup wizard...")
			fmt.Println()
			if err := runOnboard(cmd, nil); err != nil {
				return err
			}
			fmt.Println()
		}
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

	if desktopRuntime == nil {
		if err := config.ValidateAuth(env); err != nil {
			return err
		}
	} else {
		os.Unsetenv("DECEPTICON_DESKTOP_API_KEY")
	}

	ui.Info("[2/5] Check · verifying Docker and Compose")
	// Verify Docker is available
	if _, err := exec.LookPath("docker"); err != nil {
		return fmt.Errorf("Docker is not installed or not in PATH. Install from https://docs.docker.com/get-docker/")
	}
	// Verify Docker Compose V2
	if err := checkComposeVersion(cmd.Context(), func(ctx context.Context) ([]byte, error) {
		return exec.CommandContext(ctx, "docker", "compose", "version").CombinedOutput()
	}); errors.Is(err, errComposeProbeTimeout) {
		ui.Info("Docker Compose version discovery is slow; checking the actual startup command.")
	} else if err != nil {
		return err
	}

	// Warn — don't block — if Ollama is selected but the URL doesn't
	// reach a running server. We translate ``host.docker.internal`` to
	// ``localhost`` for the host-side probe; from inside the litellm
	// container the original URL is what gets used at runtime.
	if desktopRuntime == nil {
		probeOllamaIfSelected(env)
	}

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
	if err := os.MkdirAll(filepath.Join(home, "langgraph"), 0o700); err != nil {
		return err
	}

	// Ensure DECEPTICON_HOME is set in .env (Docker Compose needs absolute path)
	if config.Get(env, "DECEPTICON_HOME", "") == "" {
		env["DECEPTICON_HOME"] = home
		if desktopRuntime == nil {
			if err := config.AppendEnvLine(config.EnvPath(), "DECEPTICON_HOME", home); err != nil {
				ui.Warning("Could not set DECEPTICON_HOME in .env: " + err.Error())
			}
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
		if _, statErr := os.Stat(credsPath); statErr == nil && (desktopRuntime == nil || desktopRuntime.provider == "claude_code") {
			_ = os.Setenv("CLAUDE_CREDENTIALS_VOLUME", credsPath)
		} else {
			_ = os.Setenv("CLAUDE_CREDENTIALS_VOLUME", nullDevice())
		}

		// Same pattern for the Codex CLI credential store at ~/.codex/auth.json.
		// The new auth/ ChatGPT handler reads (and writes) this file directly so
		// a host-side `codex login` flows into the container without a rebuild.
		codexAuthPath := filepath.Join(userHome, ".codex", "auth.json")
		if _, statErr := os.Stat(codexAuthPath); statErr == nil && (desktopRuntime == nil || desktopRuntime.provider == "chatgpt") {
			_ = os.Setenv("CODEX_AUTH_VOLUME", codexAuthPath)
		} else {
			_ = os.Setenv("CODEX_AUTH_VOLUME", nullDevice())
		}
	}

	checkStartupUpdate(env, version, skipUpdate || existingEngagement != "")
	// A native-selected profile overrides home discovery, including Windows
	// account files explicitly translated for a WSL launcher. Other accounts
	// stay unmounted. Ordinary CLI startup keeps its existing discovery.
	applyDesktopAccountFile(desktopRuntime)

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
	ui.Info("[3/5] Engagement · select a workspace")
	var choice engagement.Choice
	if existingEngagement == "" {
		choice, err = engagement.Select(home)
	} else {
		choice, err = engagement.ResolveExistingAt(home, existingEngagement, os.Getenv("DECEPTICON_DESKTOP_WORKSPACE"))
	}
	if err != nil {
		return err
	}
	ui.Info("Mode: Red · Decepticon")
	if !choice.AuthorizationConfirmed {
		ui.Info("No engagement scope: create the engagement with targets, or import plan/roe.json with /scope import <file> --confirm. Optional planning: /mode plan.")
	} else {
		ui.Info("Target scope loaded. Red enforces this scope; /mode plan is optional.")
	}
	// Export the bind path. composeEnv() forwards os.Environ(), so docker
	// compose interpolates ${DECEPTICON_ENGAGEMENT_WORKSPACE} from this var.
	if err := os.Setenv("DECEPTICON_ENGAGEMENT_WORKSPACE", choice.WorkspacePath); err != nil {
		return fmt.Errorf("set engagement workspace env: %w", err)
	}
	// Bind the service namespace independently of the web container host view.
	runtimeEngagement := ""
	if desktopRuntime == nil {
		runtimeEngagement = choice.Engagement
	}
	if err := os.Setenv("DECEPTICON_RUNTIME_ENGAGEMENT", runtimeEngagement); err != nil {
		return fmt.Errorf("set runtime engagement: %w", err)
	}
	if err := opsinternal.RecordEngagementWorkspace(choice.Engagement, choice.WorkspacePath); err != nil {
		return fmt.Errorf("record engagement workspace for opscontrol: %w", err)
	}
	if existingEngagement != "" {
		if err := checkDesktopSandboxMount(strings.TrimSpace(os.Getenv("DECEPTICON_STACK_NAME")), choice.WorkspacePath); err != nil {
			return err
		}
	}

	// 4. Spawn the infracontrol daemon BEFORE `compose up` so the
	// langgraph socket bind-mount has a real socket to attach to.
	// ADR-0006 §1' — daemon owns docker socket; agent reaches it
	// only via the langgraph-only UDS mount.
	sock, err := infracontrol.EnsureRunning()
	if err != nil {
		// Non-fatal for backwards compatibility with stacks that
		// have not yet adopted the docker-compose.opscontrol.yml
		// override: agent ops_* tools will return a "daemon
		// unreachable" diagnostic rather than crashing the boot.
		ui.Warning("infracontrol daemon not started: " + err.Error())
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
	if desktopRuntime != nil {
		overlay, err := writeDesktopRuntimeOverlay(home, desktopRuntime)
		if err != nil {
			return err
		}
		defer os.Remove(overlay)
		c.OverlayFile = overlay
		c.ExtraEnv = []string{"DECEPTICON_DESKTOP_MODEL=" + desktopRuntime.model}
		if version != "" && version != "dev" {
			c.ExtraEnv = append(c.ExtraEnv, "DECEPTICON_VERSION="+strings.TrimPrefix(version, "v"))
		}
		if desktopRuntime.secret != "" {
			c.ExtraEnv = append(c.ExtraEnv, "DECEPTICON_DESKTOP_API_KEY="+desktopRuntime.secret)
		}
	}

	if existingEngagement != "" && serveBrowserDescriptor != "" {
		overlay, err := writeDesktopBrowserOverlay(home, serveBrowserDescriptor, existingEngagement, c.OverlayFile)
		if err != nil {
			return err
		}
		defer os.Remove(overlay)
		c.OverlayFile = overlay
	}
	if existingEngagement != "" && serveBrowserRegistrations != "" {
		overlay, err := writeDesktopBrowserRegistrationsOverlay(home, serveBrowserRegistrations, c.OverlayFile)
		if err != nil {
			return err
		}
		defer os.Remove(overlay)
		c.OverlayFile = overlay
	}

	if terminalOwner != nil {
		overlay, err := writeDesktopTerminalOverlay(home, c.OverlayFile, terminalOwner)
		if err != nil {
			return err
		}
		defer os.Remove(overlay)
		c.OverlayFile = overlay
	}
	ui.Info("[4/5] Services · starting and waiting for readiness")
	startServices := func() error { return c.Up(compose.Profiles.CLI) }
	if existingEngagement != "" && !attachInteractive {
		startServices = c.UpDesktop
	}
	if err := startServices(); err != nil {
		return fmt.Errorf("start services: %w; inspect with `decepticon doctor` and `decepticon logs` (services kept)", err)
	}

	// 5. Health checks
	if err := waitForStartupServices(cmd.Context(), c, env, existingEngagement); err != nil {
		return fmt.Errorf("service readiness: %w; inspect `decepticon doctor` and `decepticon logs` (services kept)", err)
	}

	// The desktop agent service ends here — it serves host clients and has
	// no terminal. Attach keeps going: its whole point is the interactive
	// CLI on the existing engagement.
	if existingEngagement != "" && !attachInteractive {
		if terminalOwner != nil {
			receipt, err := terminalOwner.receipt()
			if err != nil {
				return err
			}
			encoded, err := json.Marshal(receipt)
			if err != nil {
				return err
			}
			fmt.Println("DECEPTICON_DESKTOP_TERMINAL=" + string(encoded))
		}
		ui.Success("Desktop agent service ready for " + choice.Engagement)
		fmt.Println("DECEPTICON_DESKTOP_ASSISTANT=" + choice.AssistantID)
		return nil
	}

	// 6. Launch CLI
	fmt.Println()
	ui.Info("[5/5] Connect · launching the conversation")

	cliEnv := map[string]string{
		"DECEPTICON_VERSION":        version,
		"DECEPTICON_ASSISTANT_ID":   choice.AssistantID,
		"DECEPTICON_RUN_MODE":       choice.Mode,
		"DECEPTICON_ENGAGEMENT":     choice.Engagement,
		"DECEPTICON_WORKSPACE_PATH": "/workspace",
	}
	for key, value := range workflowEnv {
		cliEnv[key] = value
	}
	for key, value := range controlsEnv {
		cliEnv[key] = value
	}
	if modelFlag, _ := cmd.Flags().GetString("model"); modelFlag != "" {
		cliEnv["DECEPTICON_MODEL_OVERRIDE"] = modelFlag
	}
	applyResumeEnv(cliEnv, thread, continueThread)
	if choice.Target != "" {
		cliEnv["DECEPTICON_TARGET"] = choice.Target
		cliEnv["DECEPTICON_TARGET_TYPE"] = "direct_scope"
	}
	if choice.AuthorizationConfirmed {
		cliEnv["DECEPTICON_AUTHORIZATION_CONFIRMED"] = "true"
	}
	if port := config.Get(env, "WEB_PORT", "3000"); port != "" {
		cliEnv["WEB_PORT"] = port
	}

	// Background mode: services are up and the engagement workspace is bound;
	// skip the interactive CLI and let the operator attach later.
	if backgroundRun {
		ui.Success("Engagement " + choice.Engagement + " ready in the background (thread: " + threadOrPickerHint(thread) + ").")
		ui.DimText("Attach with 'decepticon attach " + choice.Engagement + "'. Services kept running.")
		return nil
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

// Interactive sessions consume the API from the CLI container. Service-only
// launches serve host clients and must continue to verify the published route.
func waitForStartupServices(ctx context.Context, c *compose.Compose, env map[string]string, existingEngagement string) error {
	probe := c.ProbeCLI
	if existingEngagement != "" {
		probe = func(ctx context.Context) error { return health.ProbeLangGraph(ctx, env) }
	}
	return health.WaitForServices(ctx, probe)
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

func threadOrPickerHint(thread string) string {
	if thread != "" && thread != "picker" {
		return thread
	}
	return "created on first attach"
}
