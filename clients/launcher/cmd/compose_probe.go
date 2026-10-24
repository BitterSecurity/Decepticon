package cmd

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"
)

var errComposeProbeTimeout = errors.New("Docker Compose version discovery timed out")

// A cold Docker CLI/plugin can exceed three seconds on a busy desktop/WSL host.
// Keep discovery bounded separately from the longer service-readiness deadline.
func checkComposeVersion(ctx context.Context, probe func(context.Context) ([]byte, error)) error {
	probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	out, err := probe(probeCtx)
	if err == nil {
		return nil
	}
	if ctx.Err() != nil {
		return fmt.Errorf("Docker Compose version check cancelled: %w", ctx.Err())
	}
	if errors.Is(probeCtx.Err(), context.DeadlineExceeded) {
		return fmt.Errorf("%w; run `docker compose version` to diagnose the Docker CLI/plugin: %w", errComposeProbeTimeout, probeCtx.Err())
	}
	if errors.Is(probeCtx.Err(), context.Canceled) {
		return fmt.Errorf("Docker Compose version check cancelled: %w", probeCtx.Err())
	}
	details := strings.TrimSpace(string(out))
	if details == "" {
		details = err.Error()
	}
	return fmt.Errorf("Docker Compose V2 is required. Got: %s. Upgrade Docker Desktop or install the compose plugin.", details)
}
