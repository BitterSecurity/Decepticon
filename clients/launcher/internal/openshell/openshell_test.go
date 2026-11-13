package openshell

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/runtime"
)

var openshellEnvKeys = []string{
	"DECEPTICON_OPENSHELL_ENDPOINT",
	"OPENSHELL_COMPUTE_SOCK",
	"OPENSHELL_PODMAN_SOCKET",
	"OPENSHELL_GATEWAY_TOML",
	"OPENSHELL_GATEWAY_USER",
	"OPENSHELL_GATEWAY_USERNS",
	"OPENSHELL_DATA_DIR",
}

func clearOpenshellEnv(t *testing.T) {
	t.Helper()
	for _, k := range openshellEnvKeys {
		orig, had := os.LookupEnv(k)
		t.Cleanup(func() {
			if had {
				_ = os.Setenv(k, orig)
			} else {
				_ = os.Unsetenv(k)
			}
		})
		_ = os.Unsetenv(k)
	}
}

func TestPrepareGatewayDocker(t *testing.T) {
	clearOpenshellEnv(t)
	home := t.TempDir()

	profile, label, err := PrepareGateway(home, runtime.Runtime{Name: "docker"})
	if err != nil {
		t.Fatalf("PrepareGateway(docker) error: %v", err)
	}
	if profile != "openshell" {
		t.Errorf("profile = %q, want openshell", profile)
	}
	if label != "Docker" {
		t.Errorf("label = %q, want Docker", label)
	}

	want := map[string]string{
		"DECEPTICON_OPENSHELL_ENDPOINT": "host.docker.internal:8080",
		"OPENSHELL_COMPUTE_SOCK":        "/var/run/docker.sock",
		"OPENSHELL_PODMAN_SOCKET":       "",
		"OPENSHELL_GATEWAY_USER":        "0",
		"OPENSHELL_GATEWAY_USERNS":      "host",
		"OPENSHELL_DATA_DIR":            "/var/lib/openshell",
		"OPENSHELL_GATEWAY_TOML":        filepath.Join(home, "openshell-gateway.toml"),
	}
	for k, v := range want {
		if got := os.Getenv(k); got != v {
			t.Errorf("env %s = %q, want %q", k, got, v)
		}
	}

	raw, err := os.ReadFile(filepath.Join(home, "openshell-gateway.toml"))
	if err != nil {
		t.Fatalf("read rendered toml: %v", err)
	}
	toml := string(raw)
	if strings.Contains(toml, dataPlaceholder) {
		t.Errorf("rendered toml still contains %s", dataPlaceholder)
	}
	if !strings.Contains(toml, `compute_driver      = "docker"`) {
		t.Errorf("rendered toml missing docker compute_driver")
	}
	if !strings.Contains(toml, "/var/lib/openshell/tls/jwt/signing.pem") {
		t.Errorf("rendered toml jwt path not pointed at data dir:\n%s", toml)
	}
}

func TestPrepareGatewayPodman(t *testing.T) {
	clearOpenshellEnv(t)
	home := t.TempDir()
	sockPath := filepath.Join(t.TempDir(), "podman.sock")

	profile, label, err := PrepareGateway(home, runtime.Runtime{
		Name:       "podman",
		DockerHost: "unix://" + sockPath,
	})
	if err != nil {
		t.Fatalf("PrepareGateway(podman) error: %v", err)
	}
	if profile != "openshell" {
		t.Errorf("profile = %q, want openshell", profile)
	}
	if label != "Podman rootless (recommended)" {
		t.Errorf("label = %q, want Podman rootless (recommended)", label)
	}

	dataDir := filepath.Join(home, "openshell")
	want := map[string]string{
		"DECEPTICON_OPENSHELL_ENDPOINT": "host.containers.internal:8080",
		"OPENSHELL_COMPUTE_SOCK":        sockPath,
		"OPENSHELL_PODMAN_SOCKET":       sockPath,
		"OPENSHELL_GATEWAY_USERNS":      "keep-id",
		"OPENSHELL_GATEWAY_USER":        fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid()),
		"OPENSHELL_DATA_DIR":            dataDir,
	}
	for k, v := range want {
		if got := os.Getenv(k); got != v {
			t.Errorf("env %s = %q, want %q", k, got, v)
		}
	}

	if _, err := os.Stat(filepath.Join(dataDir, "tls")); err != nil {
		t.Errorf("podman data dir tls/ not created: %v", err)
	}

	raw, err := os.ReadFile(filepath.Join(home, "openshell-gateway.toml"))
	if err != nil {
		t.Fatalf("read rendered toml: %v", err)
	}
	toml := string(raw)
	if !strings.Contains(toml, `compute_driver      = "podman"`) {
		t.Errorf("rendered toml missing podman compute_driver")
	}
	if !strings.Contains(toml, filepath.Join(dataDir, "tls", "jwt", "signing.pem")) {
		t.Errorf("rendered toml jwt path not pointed at data dir:\n%s", toml)
	}
}
