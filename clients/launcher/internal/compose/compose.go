package compose

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/opscontrol"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/runtime"
)

// Compose wraps Docker Compose commands for Decepticon services.
type Compose struct {
	Home        string
	ComposeFile string
	EnvFile     string
	// Runtime is the container runtime selected at construction time
	// (docker / podman / nerdctl). Stored so every call uses the same
	// binary + socket and we don't re-probe on every invocation.
	Runtime runtime.Runtime
}

// New creates a Compose instance using the Decepticon home directory.
// The container runtime is detected at construction time so every
// subsequent call uses the same binary. Falls back to "docker" if no
// runtime is reachable so existing tests and dev workflows that don't
// have Podman installed keep working unchanged.
func New() *Compose {
	home := config.DecepticonHome()
	rt, err := runtime.Detect()
	if err != nil {
		// No runtime available right now — fall back to assuming
		// docker. The user will get a clear error from the first
		// `docker` exec if it's really missing, and the onboard
		// System Check surfaces it well before reaching here.
		rt = runtime.Runtime{Name: "docker", Bin: "docker", ComposeArgs: []string{"compose"}}
	}
	return &Compose{
		Home:        home,
		ComposeFile: filepath.Join(home, "docker-compose.yml"),
		EnvFile:     filepath.Join(home, ".env"),
		Runtime:     rt,
	}
}

// Profiles defines the Docker Compose profile names this launcher
// recognizes. The shipped catalog (cli + every specialist workload)
// is the union — `cli` is always activated by `decepticon start`;
// the specialist workloads come up on demand through ADR-0006's
// opscontrol daemon. The launcher keeps the names here as constants
// only so `down` / `stop` can sweep every container regardless of
// which workloads the agent ended up spawning.
var Profiles = struct {
	CLI       string
	C2        string
	AD        string
	Reversing string
}{
	CLI:       "cli",
	C2:        "c2-sliver",
	AD:        "ad",
	Reversing: "reversing",
}

// AllProfiles returns every profile flag the launcher should pass
// to `docker compose down`. Workloads the agent never spawned are
// silently no-ops; the cost of listing them is one cli-arg per
// profile, which beats orphaning containers when an engagement
// brought up an ad-hoc combination of specialists.
func AllProfiles() []string {
	return []string{
		"--profile", Profiles.CLI,
		"--profile", Profiles.C2,
		"--profile", Profiles.AD,
		"--profile", Profiles.Reversing,
	}
}

// baseArgs returns the common compose arguments for the detected
// runtime. For Docker this is ["compose", "-f", ...]; for Podman 4.4+
// it's the same shape; for the podman-compose wrapper the leading
// "compose" is omitted because the wrapper IS the compose tool.
//
// Defaults to ["compose"] when c.Runtime is the zero value so tests
// that instantiate Compose{} directly (without going through New())
// still produce the Docker-shaped argv.
func (c *Compose) baseArgs() []string {
	prefix := c.Runtime.ComposeArgs
	if prefix == nil && c.Runtime.Bin == "" {
		prefix = []string{"compose"}
	}
	args := append([]string{}, prefix...)
	// `-p` is explicit so the launcher's project name matches what the
	// opscontrol daemon uses on its own compose calls (DockerComposeBackend
	// reads the same helper). Without `-p`, compose defaults to the
	// sanitized basename of $DECEPTICON_HOME — that agrees by accident in
	// normal flows but breaks the moment any caller passes a different
	// `-p` (CI, manual debugging, …) and produces a "container_name in
	// use by another project" conflict on the next ops_start.
	args = append(args, "-p", opscontrol.ComposeProjectName())
	args = append(args, "-f", c.ComposeFile, "--env-file", c.EnvFile)
	// ADR-0006 Sprint 1: include the opscontrol overlay only when
	// the file exists AND the host socket has been bound. Attaching
	// the overlay without DECEPTICON_OPSCONTROL_SOCK_HOST exported
	// would mount /dev/null into langgraph (the old `:-/dev/null`
	// fallback in the overlay) — that's a wiring bug masquerading
	// as a "daemon unreachable" diagnostic at agent runtime,
	// which is much harder to debug than a missing overlay at boot.
	// EnsureRunning() exports the env var on success; the user-side
	// fallback (no service manager + spawn failed) keeps the overlay
	// out so the boot doesn't silently regress to the broken state.
	if override := filepath.Join(c.Home, "docker-compose.opscontrol.yml"); fileExists(override) &&
		os.Getenv("DECEPTICON_OPSCONTROL_SOCK_HOST") != "" {
		args = append(args, "-f", override)
	}
	return args
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// ContainerName builds the docker container name for a Decepticon
// service, mirroring the “${DECEPTICON_STACK_NAME:+-${DECEPTICON_STACK_NAME}}“
// template used by docker-compose.yml (#216). Unset/empty → today's
// “decepticon-<svc>“ name verbatim; “stack2“ → “decepticon-stack2-<svc>“.
// Keeps the Go launcher and YAML naming convention in lockstep so
// “docker exec“/“logs“/“stop“ resolve the right container in
// dual-stack runs.
func ContainerName(svc string) string {
	stack := strings.TrimSpace(os.Getenv("DECEPTICON_STACK_NAME"))
	if stack == "" {
		return "decepticon-" + svc
	}
	return "decepticon-" + stack + "-" + svc
}

// readVersion returns the installed version from $DECEPTICON_HOME/.version,
// or an empty string if the file is missing or unreadable. The launcher
// (install + explicit update) is the single writer; compose falls back to
// :stable (the conservative default channel tag) when the marker is absent.
func (c *Compose) readVersion() string {
	data, err := os.ReadFile(filepath.Join(c.Home, ".version"))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// composeEnv returns the parent environment with DECEPTICON_VERSION pinned
// from the .version file plus any runtime-derived env (DOCKER_HOST for
// Podman, etc.). docker compose treats the process environment as
// higher precedence than --env-file, so this overrides any stale value the
// user may have written into .env and avoids the silent `:stable` drift
// that occurs when the variable is unset.
func (c *Compose) composeEnv() []string {
	// Single source of truth for compose interpolation env (STACK_NAME +
	// COMPOSE_PROJECT). The daemon's DockerComposeBackend reads from the
	// same helper so the two never write DIFFERENT container_name
	// values into the SAME compose project. See
	// opscontrol.ComposeCommandEnv() for the rationale.
	env := opscontrol.ComposeCommandEnv()
	if v := c.readVersion(); v != "" {
		env = append(env, "DECEPTICON_VERSION="+imageTag(v))
	}
	// Inject DOCKER_HOST for Podman so nested Docker-API clients in
	// containers (testcontainers, kubectl-with-docker-shim) find the
	// Podman socket. No-op for Docker.
	env = c.Runtime.Apply(env)
	return env
}

// run executes a compose command via the detected runtime.
func (c *Compose) run(args []string, interactive bool) error {
	cmdArgs := append(c.baseArgs(), args...)
	cmd := exec.Command(c.Runtime.Bin, cmdArgs...)
	cmd.Env = c.composeEnv()
	if interactive {
		cmd.Stdin = os.Stdin
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
	} else {
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
	}
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%s compose %s: %w", c.Runtime.Name, strings.Join(args, " "), err)
	}
	return nil
}

// Up starts services in detached mode and blocks until healthchecks pass.
//
// `--wait` (Docker Compose 2.0+) makes `up` block until each service's compose
// healthcheck transitions to healthy, eliminating the need for the launcher to
// re-implement HTTP polling.
//
// `--wait-timeout` is the single user-facing patience knob. Override via
// DECEPTICON_STARTUP_TIMEOUT_SECONDS for slower hardware. Default 600s
// covers most environments after measuring 136s LiteLLM cold start in CI.
func (c *Compose) Up(profiles ...string) error {
	args := []string{}
	for _, p := range profiles {
		args = append(args, "--profile", p)
	}
	args = append(args, "up", "-d", "--no-build", "--wait", "--wait-timeout", startupTimeoutSeconds())
	return c.run(args, false)
}

func (c *Compose) UpDatabases() error {
	return c.run([]string{"up", "-d", "--no-build", "--wait", "--wait-timeout", startupTimeoutSeconds(), "postgres", "neo4j"}, false)
}

// startupTimeoutSeconds returns the --wait-timeout value as a string.
// User override via DECEPTICON_STARTUP_TIMEOUT_SECONDS; falls back to 600s.
func startupTimeoutSeconds() string {
	if v := os.Getenv("DECEPTICON_STARTUP_TIMEOUT_SECONDS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return strconv.Itoa(n)
		}
	}
	return "600"
}

// Down stops and removes containers using all profiles for clean teardown.
func (c *Compose) Down() error {
	args := AllProfiles()
	args = append(args, "down")
	return c.run(args, false)
}

// DownAndPurge tears down containers, networks, and named volumes. Used by
// `decepticon remove` so a full uninstall doesn't leave gigabytes of
// postgres/neo4j data behind.
func (c *Compose) DownAndPurge() error {
	args := AllProfiles()
	args = append(args, "down", "--volumes", "--remove-orphans")
	return c.run(args, false)
}

// Pull pulls images for services with a version tag. An explicit version
// argument overrides the .version file (used by the updater right after a
// new release lands). Empty version → fall back to whatever .version says.
func (c *Compose) Pull(version string) error {
	cmd := exec.Command(c.Runtime.Bin, append(c.baseArgs(), "pull")...)
	// Base off composeEnv so the stack-name + runtime injections apply
	// here too; an explicit version arg overrides the .version-derived
	// DECEPTICON_VERSION (later entries win in the child process env).
	env := c.composeEnv()
	if version != "" {
		env = append(env, "DECEPTICON_VERSION="+imageTag(version))
	}
	cmd.Env = env
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%s compose pull: %w", c.Runtime.Name, err)
	}
	return nil
}

func imageTag(version string) string {
	return strings.TrimPrefix(strings.TrimSpace(version), "v")
}

// Ps shows service status.
func (c *Compose) Ps() error {
	return c.run([]string{"ps"}, false)
}

// Logs follows service logs.
func (c *Compose) Logs(service string) error {
	args := []string{"logs", "-f"}
	if service != "" {
		args = append(args, service)
	}
	return c.run(args, false)
}

// Exec runs a command inside a running service container.
func (c *Compose) Exec(service string, command ...string) error {
	args := append([]string{"exec", "-T", service}, command...)
	return c.run(args, false)
}

// RunInteractive runs a one-off container with stdin attached.
func (c *Compose) RunInteractive(profiles []string, service string, env map[string]string, command ...string) error {
	cmdArgs := c.baseArgs()
	for _, p := range profiles {
		cmdArgs = append(cmdArgs, "--profile", p)
	}
	// Note: --no-build is intentionally absent. `docker compose run` does
	// not accept --no-build (only `up` does); passing it raises
	// "unknown flag: --no-build" on every Compose version. The original
	// concern (OSS users without source triggering a build) doesn't
	// apply here because the cli image is pulled at install time and
	// `run` only builds when the image is missing.
	cmdArgs = append(cmdArgs, "run", "--rm")
	for k, v := range env {
		cmdArgs = append(cmdArgs, "-e", k+"="+v)
	}
	cmdArgs = append(cmdArgs, service)
	cmdArgs = append(cmdArgs, command...)

	cmd := exec.Command(c.Runtime.Bin, cmdArgs...)
	cmd.Env = c.composeEnv()
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("%s compose run %s: %w", c.Runtime.Name, service, err)
	}
	return nil
}

// CleanScratch removes legacy root-level scratch/session directories inside
// the running sandbox. Current bash tooling writes these directories under
// each engagement workspace; this cleanup only retires leftovers from older
// versions.
func (c *Compose) CleanScratch() {
	cmd := exec.Command(
		c.Runtime.Bin,
		"exec",
		ContainerName("sandbox"),
		"rm",
		"-rf",
		"/workspace/.scratch",
		"/workspace/.sessions",
	)
	cmd.Env = c.composeEnv()
	cmd.Stdout = nil
	cmd.Stderr = nil
	_ = cmd.Run()
}

// RemoveOrphanedCLI removes any leftover CLI containers.
func (c *Compose) RemoveOrphanedCLI() {
	// Best-effort cleanup of orphaned CLI containers.
	// Anchor the filter to the stack naming convention used by ContainerName
	// so we don't accidentally match unrelated containers. Use the detected
	// runtime binary (docker / podman / nerdctl) and its env.
	stack := strings.TrimSpace(os.Getenv("DECEPTICON_STACK_NAME"))
	prefix := "decepticon"
	if stack != "" {
		prefix = "decepticon-" + stack
	}
	ps := exec.Command(c.Runtime.Bin, "ps", "-aq", "--filter", fmt.Sprintf("name=^%s-cli", prefix))
	ps.Env = c.composeEnv()
	out, err := ps.Output()
	if err != nil || len(out) == 0 {
		return
	}
	ids := strings.Fields(strings.TrimSpace(string(out)))
	for _, id := range ids {
		rm := exec.Command(c.Runtime.Bin, "rm", "-f", id)
		rm.Env = c.composeEnv()
		_ = rm.Run()
	}
}
