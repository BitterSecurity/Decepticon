package opscontrol

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
)

// DockerComposeBackend shells out to `docker compose` to fulfill the
// Backend Protocol. ADR-0006 §5' chose shell-out over the docker SDK
// so profile resolution (depends_on chains, healthchecks, wait
// semantics) stays in upstream compose. This keeps a non-trivial
// resolver out of our codebase.
type DockerComposeBackend struct {
	// ComposeFile + EnvFile + (optional) extra files combine into the
	// `-f`/`--env-file` prefix every compose call uses. Defaults from
	// $DECEPTICON_HOME.
	ComposeFile string
	EnvFile     string
	ExtraFiles  []string
	// WaitTimeoutSeconds caps the `--wait` flag on `up`. ADR-0006
	// notes BHCE cold-start is ~30s; we default to 180s to absorb
	// goose migrations and dawgs index build.
	WaitTimeoutSeconds int
	runs               sync.Map
}

// NewDockerComposeBackend constructs a backend with the launcher's
// standard $DECEPTICON_HOME paths.
func NewDockerComposeBackend() *DockerComposeBackend {
	home := config.DecepticonHome()
	return &DockerComposeBackend{
		ComposeFile:        filepath.Join(home, "docker-compose.yml"),
		EnvFile:            filepath.Join(home, ".env"),
		WaitTimeoutSeconds: 180,
	}
}

func (b *DockerComposeBackend) Name() string { return "docker-compose" }

// baseArgs returns the shared prefix for every compose call. The
// `-p PROJECT` flag is explicit and shares the value with the
// launcher's compose.baseArgs() through ComposeProjectName(), so both
// sides own the same compose project — that guarantees the daemon
// can adopt containers the launcher already created (and vice versa)
// instead of getting a "container_name already in use" conflict from
// docker when the two project names accidentally drift apart.
func (b *DockerComposeBackend) baseArgs() []string {
	args := []string{
		"compose",
		"-p", ComposeProjectName(),
		"-f", b.ComposeFile,
		"--env-file", b.EnvFile,
	}
	for _, f := range b.ExtraFiles {
		args = append(args, "-f", f)
	}
	return args
}

type workloadRun struct {
	engagement string
	runID      string
}

func newRunID() (string, error) {
	var bytes [12]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		return "", fmt.Errorf("generate workload run id: %w", err)
	}
	return hex.EncodeToString(bytes[:]), nil
}

var engagementSlug = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$`)

func engagementWorkspace(slug string) (string, error) {
	if !engagementSlug.MatchString(slug) {
		return "", fmt.Errorf("invalid engagement slug %q", slug)
	}
	root, err := filepath.EvalSymlinks(filepath.Join(config.DecepticonHome(), "workspace"))
	if err != nil {
		return "", fmt.Errorf("resolve workspace root: %w", err)
	}
	path, err := filepath.EvalSymlinks(filepath.Join(root, slug))
	if err != nil {
		return "", fmt.Errorf("resolve engagement workspace: %w", err)
	}
	relative, err := filepath.Rel(root, path)
	if err != nil || relative != slug {
		return "", fmt.Errorf("engagement workspace escaped workspace root")
	}
	info, err := os.Stat(path)
	if err != nil || !info.IsDir() {
		return "", fmt.Errorf("engagement workspace is unavailable")
	}
	return path, nil
}

func labeledComposeOverride(services []string, workload, engagement, runID string) (string, error) {
	override := map[string]map[string]map[string]map[string]string{"services": {}}
	for _, service := range services {
		override["services"][service] = map[string]map[string]string{
			"labels": {
				"decepticon.engagement": engagement,
				"decepticon.run":        runID,
				"decepticon.workload":   workload,
			},
		}
	}
	contents, err := json.Marshal(override)
	if err != nil {
		return "", fmt.Errorf("encode workload labels: %w", err)
	}
	file, err := os.CreateTemp("", "decepticon-ops-labels-*.json")
	if err != nil {
		return "", fmt.Errorf("create workload label override: %w", err)
	}
	if _, err := file.Write(contents); err != nil {
		_ = file.Close()
		_ = os.Remove(file.Name())
		return "", fmt.Errorf("write workload label override: %w", err)
	}
	if err := file.Close(); err != nil {
		_ = os.Remove(file.Name())
		return "", fmt.Errorf("close workload label override: %w", err)
	}
	return file.Name(), nil
}

// Start runs `docker compose --profile <workload> up -d --wait`. The
// caller (server.go) has already taken the per-workload mutex, so
// concurrent calls on the same workload are serialized.
//
// `--no-recreate` is load-bearing for the agent-driven flow. Without
// it, compose's incremental model rebuilds the merged config every
// time the daemon adds a profile on top of what the launcher already
// activated -- the resulting per-service config-hash differs from
// what the launcher wrote, and compose tags every live container
// "Recreate" mid-engagement. Workload spawn is purely ADDITIVE
// (langgraph / litellm / sandbox were correctly running BEFORE
// ops_start; they must keep running AFTER). `--no-recreate` tells
// compose to skip the hash diff and only create services the
// requested profile activates that are not already running.
func (b *DockerComposeBackend) Start(ctx context.Context, workload, engagementID string) (Handle, error) {
	if engagementID == "" {
		return Handle{}, fmt.Errorf("workload %s requires an engagement id", workload)
	}
	workspace := ""
	if workload == "reversing" {
		var err error
		workspace, err = engagementWorkspace(engagementID)
		if err != nil {
			return Handle{}, err
		}
	}
	services, err := b.profileExclusiveServices(ctx, workload)
	if err != nil {
		return Handle{}, err
	}
	runID, err := newRunID()
	if err != nil {
		return Handle{}, err
	}
	override, err := labeledComposeOverride(services, workload, engagementID, runID)
	if err != nil {
		return Handle{}, err
	}
	defer os.Remove(override)
	args := append(b.baseArgs(), "-f", override)
	args = append(args,
		"--profile", workload,
		"up", "-d", "--no-build", "--no-recreate", "--wait",
		"--wait-timeout", fmt.Sprintf("%d", b.WaitTimeoutSeconds),
	)
	cmd := exec.CommandContext(ctx, "docker", args...)
	cmd.Env = ComposeCommandEnv()
	if workspace != "" {
		cmd.Env = append(cmd.Env, "DECEPTICON_ENGAGEMENT_WORKSPACE="+workspace)
	}
	out, err := cmd.CombinedOutput()
	if err != nil {
		return Handle{}, fmt.Errorf("compose up --profile %s: %w: %s", workload, err, strings.TrimSpace(string(out)))
	}
	b.runs.Store(workload, workloadRun{engagement: engagementID, runID: runID})
	return Handle{Workload: workload, State: StateRunning, EngagementID: engagementID}, nil
}

// Stop terminates and removes only the services exclusive to the
// workload's compose profile, leaving the always-on management plane
// (litellm, postgres, neo4j, sandbox, skillogy, langgraph) untouched.
//
// The dynamic-spawn contract (ADR-0006) implies a full lifecycle:
// `ops_start("ad")` materializes the BHCE sidecars, `ops_stop("ad")`
// should leave the operator's `docker ps` exactly as it was before
// the spawn — otherwise `Exited (0)` BHCE / Neo4j / postgres-init
// containers accumulate across an engagement and the operator has to
// clean them by hand. Named volumes survive `rm` (compose only
// removes volumes when `down -v` is passed), so BHCE's Neo4j and
// Postgres data is preserved across stop/start cycles.
//
// Subtle: `docker compose --profile X stop` and `--profile X rm` do
// NOT operate on profile X exclusively. Per compose docs, "service
// without profile is always selected" — so the profile flag is
// additive (default + X) and naive `--profile X stop` would halt the
// always-on management plane too. We resolve the profile's
// exclusive service list once via set-difference and then issue
// `stop` / `rm -fs` with explicit service names so default services
// are never targeted.
//
// We also deliberately don't use `compose down`: it always targets
// the whole project regardless of `--profile`, so a single
// `ops_stop("ad")` would nuke the entire stack.
func (b *DockerComposeBackend) Stop(ctx context.Context, workload string) error {
	value, ok := b.runs.Load(workload)
	if !ok {
		return fmt.Errorf("workload %s has no run label; refusing unscoped teardown", workload)
	}
	run := value.(workloadRun)
	filterArgs := []string{
		"ps", "-aq",
		"--filter", "label=decepticon.engagement=" + run.engagement,
		"--filter", "label=decepticon.run=" + run.runID,
		"--filter", "label=decepticon.workload=" + workload,
	}
	psCmd := exec.CommandContext(ctx, "docker", filterArgs...)
	out, err := psCmd.Output()
	if err != nil {
		return fmt.Errorf("list workload containers: %w", err)
	}
	containers := strings.Fields(string(out))
	if len(containers) == 0 {
		b.runs.Delete(workload)
		return nil
	}
	stopCmd := exec.CommandContext(ctx, "docker", append([]string{"stop"}, containers...)...)
	if out, err := stopCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("stop workload containers: %w: %s", err, strings.TrimSpace(string(out)))
	}
	rmCmd := exec.CommandContext(ctx, "docker", append([]string{"rm", "-f"}, containers...)...)
	if out, err := rmCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("remove workload containers: %w: %s", err, strings.TrimSpace(string(out)))
	}
	b.runs.Delete(workload)
	return nil
}
func (b *DockerComposeBackend) profileExclusiveServices(ctx context.Context, workload string) ([]string, error) {
	withProfile, err := b.listServices(ctx, workload)
	if err != nil {
		return nil, fmt.Errorf("list services for profile %s: %w", workload, err)
	}
	defaults, err := b.listServices(ctx, "")
	if err != nil {
		return nil, fmt.Errorf("list default services: %w", err)
	}
	defaultSet := make(map[string]struct{}, len(defaults))
	for _, s := range defaults {
		defaultSet[s] = struct{}{}
	}
	var exclusive []string
	for _, s := range withProfile {
		if _, isDefault := defaultSet[s]; !isDefault {
			exclusive = append(exclusive, s)
		}
	}
	return exclusive, nil
}

// listServices runs `docker compose config --services [--profile X]`
// and returns the parsed service-name list.
func (b *DockerComposeBackend) listServices(ctx context.Context, profile string) ([]string, error) {
	args := b.baseArgs()
	if profile != "" {
		args = append(args, "--profile", profile)
	}
	args = append(args, "config", "--services")
	cmd := exec.CommandContext(ctx, "docker", args...)
	cmd.Env = ComposeCommandEnv()
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("compose config --services profile=%q: %w", profile, err)
	}
	var names []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if s := strings.TrimSpace(line); s != "" {
			names = append(names, s)
		}
	}
	return names, nil
}

// List delegates to the in-memory registry. ADR-0006 §5' allows the
// backend to be the source of truth, but a `docker compose ps` round
// trip for every `ops_status()` call is wasteful when the daemon
// already owns the lifecycle transitions that mutate state. The
// registry is the source of truth; backend.List exists so future
// non-daemon-driven backends (Kubernetes pod watch) can override it.
func (b *DockerComposeBackend) List(_ context.Context) ([]WorkloadStatus, error) {
	// The server passes its own registry snapshot in; this backend
	// returns nil to mean "use the registry".
	return nil, nil
}
