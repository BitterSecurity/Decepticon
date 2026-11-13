package openshell

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/runtime"
)

// TestLivePrepareGatewayPodman is a real-host integration check: it runs the
// actual PrepareGateway code to render the gateway TOML + export OPENSHELL_*,
// then brings up ONLY the openshell gateway service via `podman compose` using
// that output and asserts the gateway comes up healthy on the podman driver.
//
// Guarded: skips unless OPENSHELL_LIVE=1 and podman is present. Needs the repo
// docker-compose.yml, the podman user socket, and the gateway image pulled.
// Run on ra-trial:
//
//	OPENSHELL_LIVE=1 go test -run TestLivePrepareGatewayPodman -v ./internal/openshell/
func TestLivePrepareGatewayPodman(t *testing.T) {
	if os.Getenv("OPENSHELL_LIVE") != "1" {
		t.Skip("set OPENSHELL_LIVE=1 to run the live gateway integration test")
	}
	if _, err := exec.LookPath("podman"); err != nil {
		t.Skip("podman not on PATH")
	}

	repoCompose := findRepoFile(t, "docker-compose.yml")
	repoRoot := filepath.Dir(repoCompose)

	home := t.TempDir()
	// The launcher runs compose from DECEPTICON_HOME; mirror that by copying
	// the compose file in. Services it references by build context are not
	// started (we bring up only the gateway service), so a copy is enough.
	must(t, copyFile(repoCompose, filepath.Join(home, "docker-compose.yml")))
	must(t, os.WriteFile(filepath.Join(home, ".env"), nil, 0o600))

	// Detect podman exactly as the launcher does.
	rt, err := runtime.Detect()
	if err != nil || rt.Name != "podman" {
		t.Skipf("podman runtime not detected (got %q, err %v)", rt.Name, err)
	}

	// THE CODE UNDER TEST: render config + export OPENSHELL_* env.
	profile, label, err := PrepareGateway(home, rt)
	if err != nil {
		t.Fatalf("PrepareGateway: %v", err)
	}
	t.Logf("engine label=%q profile=%q", label, profile)

	// Bring up only the gateway service via the docker CLI pointed at the
	// podman socket (the proven compose-on-podman path). This verifies the
	// launcher-rendered TOML + exported OPENSHELL_* env, which is what W1
	// adds; the launcher's own compose-command selection is pre-existing.
	project := "dcp-w1-live"
	composeEnv := append(os.Environ(), "DOCKER_HOST=unix://"+os.Getenv("OPENSHELL_COMPUTE_SOCK"))
	up := exec.Command("docker", "compose", "-p", project,
		"--profile", profile, "up", "-d", "openshell-gateway")
	up.Dir = home
	up.Env = composeEnv
	if out, err := up.CombinedOutput(); err != nil {
		t.Fatalf("compose up gateway failed: %v\n%s", err, out)
	}
	t.Cleanup(func() {
		down := exec.Command("docker", "compose", "-p", project, "--profile", profile, "down")
		down.Dir = home
		down.Env = composeEnv
		_ = down.Run()
	})

	// Health: a bridge container reaches the host-net gateway via
	// host.containers.internal (the real host can't, under rootless podman).
	var healthy bool
	var lastOut string
	for i := 0; i < 60; i++ {
		// /readyz returns the status JSON ({"status":"healthy",...}); /healthz
		// is an empty 200, so assert against /readyz.
		probe := exec.Command("podman", "run", "--rm", "docker.io/curlimages/curl:latest",
			"-fsS", "--max-time", "3", "http://host.containers.internal:8081/readyz")
		probe.Env = up.Env
		out, err := probe.CombinedOutput()
		lastOut = string(out)
		if err == nil && strings.Contains(lastOut, "healthy") {
			healthy = true
			break
		}
		time.Sleep(time.Second)
	}
	if !healthy {
		logs := exec.Command("podman", "logs", "decepticon-openshell-gateway")
		logs.Env = up.Env
		lg, _ := logs.CombinedOutput()
		t.Fatalf("gateway not healthy after 30s; last probe=%q\n--- logs ---\n%s", lastOut, lg)
	}
	t.Logf("gateway healthy via host.containers.internal: %s", strings.TrimSpace(lastOut))

	// Confirm the podman compute driver connected (the launcher-rendered TOML).
	logs := exec.Command("podman", "logs", "decepticon-openshell-gateway")
	logs.Env = up.Env
	lg, _ := logs.CombinedOutput()
	if !strings.Contains(string(lg), "compute driver") || !strings.Contains(string(lg), "podman") {
		t.Errorf("gateway log does not confirm podman compute driver:\n%s", lg)
	}

	_ = repoRoot
}

func findRepoFile(t *testing.T, name string) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	for i := 0; i < 8; i++ {
		p := filepath.Join(dir, name)
		if _, err := os.Stat(p); err == nil {
			return p
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	t.Skipf("could not locate %s above %s", name, dir)
	return ""
}

func copyFile(src, dst string) error {
	b, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, b, 0o644)
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
