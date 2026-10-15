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
		"${DECEPTICON_OPSCONTROL_SOCK_HOST}:/var/run/decepticon-ops.sock",
	} {
		if !strings.Contains(string(body), required) {
			t.Errorf("socket overlay missing %q: %s", required, body)
		}
	}
	// Long-form `type: bind` + `create_host_path: false` broke Docker
	// Desktop's macOS host-socket forwarder (#972, #971): it prechecks
	// `/socket_mnt/...` before the forwarder starts. Short-form is required.
	for _, forbidden := range []string{"type: bind", "create_host_path"} {
		if strings.Contains(string(body), forbidden) {
			t.Errorf("socket overlay must use short-form mount syntax, found %q: %s", forbidden, body)
		}
	}
}
