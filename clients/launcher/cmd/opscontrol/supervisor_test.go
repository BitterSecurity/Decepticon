package opscontrol

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestComposeOverlayRequiresExistingSocket(t *testing.T) {
	home := t.TempDir()
	t.Setenv("DECEPTICON_HOME", home)
	if err := writeComposeOverlay(); err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile(filepath.Join(home, "docker-compose.opscontrol.yml"))
	if err != nil {
		t.Fatal(err)
	}
	for _, required := range []string{
		"type: bind", "source: ${DECEPTICON_OPSCONTROL_SOCK_HOST}",
		"target: /var/run/decepticon-ops.sock", "create_host_path: false",
	} {
		if !strings.Contains(string(body), required) {
			t.Errorf("socket overlay missing %q: %s", required, body)
		}
	}
}
