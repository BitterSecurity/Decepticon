package engagement

import (
	"os"
	"path/filepath"
	"testing"
)

func TestMarkStartedAndIsStarted(t *testing.T) {
	ws := t.TempDir()

	if IsStarted(ws) {
		t.Fatal("fresh workspace reported started")
	}

	if err := MarkStarted(ws); err != nil {
		t.Fatalf("MarkStarted: %v", err)
	}
	if !IsStarted(ws) {
		t.Fatal("workspace not reported started after MarkStarted")
	}

	marker := filepath.Join(ws, "plan", startedMarker)
	first, err := os.ReadFile(marker)
	if err != nil {
		t.Fatalf("read marker: %v", err)
	}
	if len(first) == 0 {
		t.Fatal("marker is empty; expected a timestamp")
	}

	// Idempotent: a second call keeps the original marker (timestamp) intact.
	if err := MarkStarted(ws); err != nil {
		t.Fatalf("MarkStarted (2nd): %v", err)
	}
	second, err := os.ReadFile(marker)
	if err != nil {
		t.Fatalf("read marker (2nd): %v", err)
	}
	if string(first) != string(second) {
		t.Errorf("marker changed on second MarkStarted: %q -> %q", first, second)
	}
}

func TestMarkStartedCreatesPlanDir(t *testing.T) {
	ws := t.TempDir()
	// No plan/ dir yet; MarkStarted must create it.
	if err := MarkStarted(ws); err != nil {
		t.Fatalf("MarkStarted: %v", err)
	}
	if _, err := os.Stat(filepath.Join(ws, "plan", startedMarker)); err != nil {
		t.Errorf("marker not created under plan/: %v", err)
	}
}
