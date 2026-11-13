// Package openshell prepares the standing OpenShell gateway compose service
// (profile: openshell) that the default `openshell` sandbox provider admits
// against. It picks the compute engine from the detected container runtime,
// renders the engine's gateway TOML, and exports the OPENSHELL_* environment
// the compose service reads.
//
// Why the launcher renders the TOML itself: SyncConfigFiles ships only
// docker-compose.yml + litellm.yaml, not containers/, so the gateway config
// would be absent on a real install. The launcher embeds the templates and
// writes a rendered copy to DECEPTICON_HOME, mounted by absolute path.
//
// Engine mapping (threat A boundary — see docs/deploy/openshell-gateway.md):
//   - podman detected  -> Podman rootless (recommended): user Podman socket,
//     --userns=keep-id, user-writable data dir, host.containers.internal.
//   - otherwise         -> Docker (supported): host Docker socket, data dir
//     /var/lib/openshell, host.docker.internal.
package openshell

import (
	_ "embed"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/runtime"
)

//go:embed gateway.docker.toml.tmpl
var dockerTOML string

//go:embed gateway.podman.toml.tmpl
var podmanTOML string

// dataPlaceholder is replaced in the templates with the resolved data dir.
const dataPlaceholder = "@DATA@"

// PrepareGateway resolves the compute engine from rt, renders the gateway
// TOML into DECEPTICON_HOME, creates the data dir when the launcher owns it,
// and exports the OPENSHELL_* env the compose `openshell` profile consumes.
//
// It returns the compose profile to activate ("openshell") and a short human
// label for the boundary banner. The caller must call this BEFORE compose up
// so the exported env reaches the compose subprocess (ComposeCommandEnv
// forwards os.Environ()).
func PrepareGateway(home string, rt runtime.Runtime) (profile, label string, err error) {
	engine := "docker"
	if rt.Name == "podman" {
		engine = "podman"
	}

	var (
		tmpl     string
		dataDir  string
		sock     string
		userns   string
		user     string
		endpoint string
	)

	switch engine {
	case "podman":
		tmpl = podmanTOML
		// User-writable: the gateway runs keep-id as this user and must
		// own the JWT keys + supervisor binary it extracts here.
		dataDir = filepath.Join(home, "openshell")
		if mkErr := os.MkdirAll(filepath.Join(dataDir, "tls"), 0o700); mkErr != nil {
			return "", "", fmt.Errorf("create openshell data dir: %w", mkErr)
		}
		sock = podmanSocket(rt)
		if sock == "" {
			return "", "", fmt.Errorf("podman runtime selected but no Podman API socket found (start podman.socket)")
		}
		userns = "keep-id"
		user = fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid())
		endpoint = "host.containers.internal:8080"
	default:
		tmpl = dockerTOML
		// Created by compose create_host_path as root; the gateway runs
		// as root (user 0) and writes it. /var/lib is the upstream
		// convention and need not be user-writable.
		dataDir = "/var/lib/openshell"
		sock = "/var/run/docker.sock"
		userns = "host"
		user = "0"
		endpoint = "host.docker.internal:8080"
	}

	// Render + write the TOML to a user-writable path in DECEPTICON_HOME
	// (the FILE lives here; the JWT/data paths inside point at dataDir).
	rendered := strings.ReplaceAll(tmpl, dataPlaceholder, dataDir)
	tomlPath := filepath.Join(home, "openshell-gateway.toml")
	if wErr := os.WriteFile(tomlPath, []byte(rendered), 0o600); wErr != nil {
		return "", "", fmt.Errorf("write gateway toml: %w", wErr)
	}

	podmanSock := ""
	if engine == "podman" {
		podmanSock = sock
	}
	for k, v := range map[string]string{
		"DECEPTICON_OPENSHELL_ENDPOINT": endpoint,
		"OPENSHELL_COMPUTE_SOCK":        sock,
		"OPENSHELL_PODMAN_SOCKET":       podmanSock,
		"OPENSHELL_GATEWAY_TOML":        tomlPath,
		"OPENSHELL_GATEWAY_USER":        user,
		"OPENSHELL_GATEWAY_USERNS":      userns,
		"OPENSHELL_DATA_DIR":            dataDir,
	} {
		if sErr := os.Setenv(k, v); sErr != nil {
			return "", "", fmt.Errorf("set %s: %w", k, sErr)
		}
	}

	if engine == "podman" {
		label = "Podman rootless (recommended)"
	} else {
		label = "Docker"
	}
	return "openshell", label, nil
}

// podmanSocket returns the Podman API socket path for the detected runtime:
// the DockerHost the runtime already resolved (stripped of unix://), else the
// well-known rootless path.
func podmanSocket(rt runtime.Runtime) string {
	if h := strings.TrimPrefix(rt.DockerHost, "unix://"); h != "" && h != rt.DockerHost {
		return h
	}
	if xdg := os.Getenv("XDG_RUNTIME_DIR"); xdg != "" {
		p := filepath.Join(xdg, "podman", "podman.sock")
		if _, statErr := os.Stat(p); statErr == nil {
			return p
		}
	}
	return ""
}
