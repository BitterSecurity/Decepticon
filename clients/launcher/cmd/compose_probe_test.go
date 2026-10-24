package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

func TestComposeProbeProcess(t *testing.T) {
	switch os.Getenv("DECEPTICON_COMPOSE_PROBE_TEST") {
	case "slow":
		time.Sleep(3200 * time.Millisecond)
		fmt.Println("Docker Compose version v5.3.1")
		os.Exit(0)
	case "missing":
		fmt.Fprintln(os.Stderr, "docker: unknown command: compose")
		os.Exit(1)
	case "blocked":
		time.Sleep(30 * time.Second)
		os.Exit(0)
	}
}

func composeProbeProcess(mode string) func(context.Context) ([]byte, error) {
	return func(ctx context.Context) ([]byte, error) {
		child := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestComposeProbeProcess$")
		child.Env = append(os.Environ(), "DECEPTICON_COMPOSE_PROBE_TEST="+mode)
		return child.CombinedOutput()
	}
}

func TestComposeProbeAcceptsSlowHealthyPlugin(t *testing.T) {
	// Reproduce the original deadline against the same real child process.
	oldCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if _, err := composeProbeProcess("slow")(oldCtx); err == nil || !errors.Is(oldCtx.Err(), context.DeadlineExceeded) {
		t.Fatalf("old cold-start reproduction did not reach its deadline: %v", err)
	}
	if err := checkComposeVersion(context.Background(), composeProbeProcess("slow")); err != nil {
		t.Fatal(err)
	}
}

func TestComposeProbeReportsDeadlineAndCancellation(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	err := checkComposeVersion(ctx, composeProbeProcess("blocked"))
	if !errors.Is(err, context.DeadlineExceeded) || errors.Is(err, errComposeProbeTimeout) {
		t.Fatalf("misleading deadline error: %v", err)
	}
	cancelled, stop := context.WithCancel(context.Background())
	stop()
	if err := checkComposeVersion(cancelled, composeProbeProcess("blocked")); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}

func TestComposeProbeStillRejectsMissingPlugin(t *testing.T) {
	err := checkComposeVersion(context.Background(), composeProbeProcess("missing"))
	if err == nil || !strings.Contains(err.Error(), "unknown command: compose") || !strings.Contains(err.Error(), "Compose V2 is required") {
		t.Fatal(err)
	}
}

func TestComposeProbeOwnDeadlineDefersToActualStartup(t *testing.T) {
	err := checkComposeVersion(context.Background(), composeProbeProcess("blocked"))
	if !errors.Is(err, errComposeProbeTimeout) || !errors.Is(err, context.DeadlineExceeded) || !strings.Contains(err.Error(), "docker compose version") {
		t.Fatalf("unexpected discovery result: %v", err)
	}
}
