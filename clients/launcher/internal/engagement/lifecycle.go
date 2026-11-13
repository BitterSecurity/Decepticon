package engagement

import (
	"os"
	"path/filepath"
	"time"
)

// startedMarker lives next to roe.json and records that the engagement's run
// has begun. Its presence LOCKS the scope.
const startedMarker = ".started"

// MarkStarted records that the engagement run has begun, which locks its scope:
// the RoE is immutable from this point on, and a different scope requires a new
// engagement (the official stance). Called at the launcher→session handoff
// (TUI / desktop / background entry). Idempotent — re-entry or attach keeps the
// original marker and timestamp.
func MarkStarted(workspacePath string) error {
	plan := filepath.Join(workspacePath, "plan")
	marker := filepath.Join(plan, startedMarker)
	if _, err := os.Stat(marker); err == nil {
		return nil // already locked
	}
	if err := os.MkdirAll(plan, 0o700); err != nil {
		return err
	}
	return os.WriteFile(marker, []byte(time.Now().UTC().Format(time.RFC3339)+"\n"), 0o600)
}

// IsStarted reports whether the engagement scope is locked — i.e. its run has
// already begun. A draft engagement (not yet started) may still have its scope
// edited; a started one is immutable.
func IsStarted(workspacePath string) bool {
	_, err := os.Stat(filepath.Join(workspacePath, "plan", startedMarker))
	return err == nil
}
