package opscontrol

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
	internal "github.com/PurpleAILAB/Decepticon/clients/launcher/internal/opscontrol"
)

// composeOverlayBody is the docker-compose overlay that bind-mounts
// the opscontrol UDS into the langgraph container. It is
// LAUNCHER-OWNED: written by EnsureRunning on every `decepticon start`,
// never shipped via the release manifest, never hand-edited. Keeping
// it embedded in the launcher binary collapses the previous
// "release.yml manifest must list every config file" failure mode
// (which caused the v1.1.10 upgrade warning) into a single binary —
// the overlay is now versioned in lockstep with the launcher.
const composeOverlayBody = `# docker-compose.opscontrol.yml — LAUNCHER-MANAGED runtime overlay (ADR-0006).
# Do NOT hand-edit. The launcher rewrites this file on every ` + "`decepticon start`" + `
# (cmd/opscontrol/supervisor.go:writeComposeOverlay) so any manual edits are
# erased on the next boot.
#
# DECEPTICON_OPSCONTROL_SOCK_HOST is REQUIRED at compose interpolation
# time. There is no '/dev/null' fallback: a missing socket path means
# the daemon wiring is broken, and we'd rather fail at boot than mount
# a character device into the container and surface ECONNREFUSED
# mid-engagement.

services:
  langgraph:
    volumes:
      - type: bind
        source: ${DECEPTICON_OPSCONTROL_SOCK_HOST}
        target: /var/run/decepticon-ops.sock
        bind:
          create_host_path: false
`

// writeComposeOverlay materializes the launcher-managed compose overlay
// at $DECEPTICON_HOME/docker-compose.opscontrol.yml. Idempotent — same
// content every call. Removed the previous design where this file was
// downloaded via the release manifest; that made every compose-overlay
// change a forced point release with sync warnings.
func writeComposeOverlay() error {
	target := filepath.Join(config.DecepticonHome(), "docker-compose.opscontrol.yml")
	return os.WriteFile(target, []byte(composeOverlayBody), 0o644)
}

// EnsureRunning is the launcher-side entry point: `decepticon start`
// calls it before `compose up`. It returns the host socket path so
// the caller can export it to docker compose (used by
// docker-compose.opscontrol.yml).
//
// Three modes, picked in order:
//
//  1. **Managed-service mode** (Alt A, preferred): a systemd user
//     unit or launchd LaunchAgent is installed and active. The
//     daemon lifecycle is owned by the init system — survives
//     reboot, restarts on crash. The launcher does not start or
//     stop the daemon; it only waits for the socket to be ready
//     (in case the service was just installed and is still
//     warming up).
//
//  2. **Managed-service-but-inactive**: the unit is installed but
//     stopped (e.g., operator ran `decepticon opscontrol stop` by
//     hand). EnsureRunning asks the manager to start it.
//
//  3. **Launcher-spawn fallback**: no service manager is available
//     on this host (Windows, WSL2 without systemd, Linux without
//     per-user systemd). The launcher forks a detached daemon and
//     writes a PID file the way it did pre-Alt-A.
//
// All three modes are idempotent — calling EnsureRunning twice in
// quick succession is safe.
func EnsureRunning() (socketPath string, err error) {
	if err := internal.EnsureRunDir(); err != nil {
		return "", err
	}
	// Write the docker-compose overlay BEFORE the daemon starts; the
	// compose layer in `decepticon start` calls baseArgs immediately
	// after, and baseArgs only attaches `-f docker-compose.opscontrol.yml`
	// when the file exists on disk. Embedded body + idempotent write
	// = the overlay is always current with the launcher binary.
	if err := writeComposeOverlay(); err != nil {
		return "", fmt.Errorf("opscontrol: write compose overlay: %w", err)
	}
	socketPath = internal.HostSocketPath()

	mgr := internal.DetectServiceManager()
	if mgr.Available() {
		installed, err := mgr.Installed()
		if err != nil {
			return "", err
		}
		if installed {
			active, err := mgr.Active()
			if err != nil {
				return "", err
			}
			if !active {
				if err := mgr.Start(); err != nil {
					return "", err
				}
			}
			if err := waitForSocket(socketPath, 5*time.Second); err != nil {
				return "", fmt.Errorf("opscontrol: managed service did not bind socket: %w "+
					"(check `decepticon opscontrol status`)", err)
			}
			return socketPath, nil
		}
		// Service manager is available but no unit installed yet —
		// fall through to launcher-spawn. The user can opt into
		// managed-service mode later with `decepticon opscontrol
		// install`.
	}

	// Fallback path: launcher-spawn. Same logic as the original
	// Sprint 1 supervisor.
	return ensureRunningLauncherSpawn(socketPath)
}

// Stop is called by `decepticon stop` AFTER `compose down`. In
// managed-service mode the daemon is intentionally NOT killed — it
// lives across launcher sessions so `decepticon start` does not pay
// the daemon-warmup cost every time. Operators who want the daemon
// down call `decepticon opscontrol stop` explicitly or run
// `decepticon opscontrol uninstall`.
//
// In launcher-spawn fallback mode Stop sends SIGTERM and waits up to
// 5s for the daemon to exit, then unlinks the socket.
func Stop() error {
	mgr := internal.DetectServiceManager()
	if mgr.Available() {
		installed, err := mgr.Installed()
		if err != nil {
			return err
		}
		if installed {
			// Managed-service mode: the daemon stays up. Compose
			// teardown is already done by the caller; the daemon's
			// own workloads are gone with it.
			return nil
		}
	}
	return stopLauncherSpawn()
}

// ensureRunningLauncherSpawn implements the pre-Alt-A behavior. Kept
// as a fallback for environments without a recognized init system.
func ensureRunningLauncherSpawn(socketPath string) (string, error) {
	if pid, alive := readPID(); alive {
		// Daemon already running. Confirm the socket is too.
		if info, err := os.Stat(socketPath); err == nil && info.Mode()&os.ModeSocket != 0 {
			return socketPath, nil
		}
		// Daemon is alive but socket missing — unhealthy. Kill +
		// respawn.
		_ = terminateDaemon(pid)
		_ = os.Remove(internal.PIDFilePath())
	}

	exe, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("opscontrol: locate self: %w", err)
	}
	cmd := exec.Command(exe, "opscontrol", "daemon") //nolint:gosec // own binary
	cmd.Stdin = nil
	logf, lerr := os.OpenFile(daemonLogPath(), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if lerr == nil {
		cmd.Stdout = logf
		cmd.Stderr = logf
	}
	setDetached(cmd)
	cmd.Env = append(os.Environ(), "DECEPTICON_OPSCONTROL_CHILD=1")
	if err := cmd.Start(); err != nil {
		return "", fmt.Errorf("opscontrol: spawn daemon: %w", err)
	}
	_ = cmd.Process.Release()

	if err := waitForSocket(socketPath, 5*time.Second); err != nil {
		return "", fmt.Errorf("opscontrol: launcher-spawn daemon failed to bind socket: %w "+
			"(see %s)", err, daemonLogPath())
	}
	return socketPath, nil
}

func stopLauncherSpawn() error {
	pid, alive := readPID()
	if !alive {
		return nil
	}
	if err := terminateDaemon(pid); err != nil {
		return fmt.Errorf("opscontrol: signal daemon: %w", err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if _, alive := readPID(); !alive {
			_ = os.Remove(internal.HostSocketPath())
			return nil
		}
		time.Sleep(50 * time.Millisecond)
	}
	return errors.New("opscontrol: daemon did not exit within 5s")
}

// waitForSocket polls until the socket file appears as a real Unix
// socket or the deadline expires. 50ms cadence — the daemon binds in
// <100ms in practice, so this is one or two iterations on the happy
// path.
func waitForSocket(path string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if info, err := os.Stat(path); err == nil && info.Mode()&os.ModeSocket != 0 {
			return nil
		}
		time.Sleep(50 * time.Millisecond)
	}
	return fmt.Errorf("socket %s did not appear within %s", path, timeout)
}

// readPID returns the recorded daemon PID and whether the process is
// currently alive. A stale PID file (process gone) returns
// (pid, false), and callers usually treat that as "no daemon".
func readPID() (int, bool) {
	raw, err := os.ReadFile(internal.PIDFilePath())
	if err != nil {
		return 0, false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil || pid <= 0 {
		return 0, false
	}
	if !processAlive(pid) {
		return pid, false
	}
	return pid, true
}

func daemonLogPath() string {
	return internal.PIDFilePath() + ".log"
}
