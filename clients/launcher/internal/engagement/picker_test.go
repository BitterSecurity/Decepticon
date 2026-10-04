package engagement

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func mkPlan(t *testing.T, home, slug string, files ...string) {
	t.Helper()
	plan := filepath.Join(home, "workspace", slug, "plan")
	if err := os.MkdirAll(plan, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range files {
		if err := os.WriteFile(filepath.Join(plan, name), []byte("{}"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

func mkBareDir(t *testing.T, home, name string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(home, "workspace", name), 0o755); err != nil {
		t.Fatal(err)
	}
}

func TestScanEngagements_NoWorkspace(t *testing.T) {
	dir := t.TempDir()
	got, err := ScanEngagements(dir)
	if err != nil {
		t.Fatalf("ScanEngagements: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("expected empty result, got %v", got)
	}
}

func TestScanEngagements_ReadyAndInProgress(t *testing.T) {
	home := t.TempDir()

	// Full bundle → ready.
	mkPlan(t, home, "alpha", "roe.json", "conops.json", "deconfliction.json")
	// Only roe → in progress.
	mkPlan(t, home, "bravo", "roe.json")
	// Empty engagement directory (no plan/) → still in progress.
	mkBareDir(t, home, "charlie")

	got, err := ScanEngagements(home)
	if err != nil {
		t.Fatalf("ScanEngagements: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("expected 3 entries, got %d (%v)", len(got), got)
	}
	byName := map[string]engagementEntry{got[0].Slug: got[0], got[1].Slug: got[1], got[2].Slug: got[2]}
	if !byName["alpha"].Ready {
		t.Errorf("alpha should be ready, got %v", byName["alpha"])
	}
	if byName["bravo"].Ready {
		t.Errorf("bravo (only roe) should not be ready")
	}
	if byName["charlie"].Ready {
		t.Errorf("charlie (empty dir) should not be ready")
	}
}

func TestScanEngagements_IgnoresHiddenWorkspaceDirs(t *testing.T) {
	home := t.TempDir()
	mkBareDir(t, home, "visible-engagement")
	mkBareDir(t, home, ".sessions")
	mkBareDir(t, home, ".scratch")

	got, err := ScanEngagements(home)
	if err != nil {
		t.Fatalf("ScanEngagements: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("expected 1 visible engagement, got %d (%v)", len(got), got)
	}
	if got[0].Slug != "visible-engagement" {
		t.Fatalf("expected visible-engagement, got %q", got[0].Slug)
	}
}

func TestScanEngagements_ReadyEngagementsBubbleUp(t *testing.T) {
	home := t.TempDir()
	now := time.Now()

	// Bare in-progress dir, MOST recent.
	mkBareDir(t, home, "fresh-incomplete")
	if err := os.Chtimes(
		filepath.Join(home, "workspace", "fresh-incomplete"),
		now, now,
	); err != nil {
		t.Fatal(err)
	}

	// Ready, OLDEST.
	mkPlan(t, home, "old-ready", "roe.json", "conops.json", "deconfliction.json")
	old := now.Add(-2 * time.Hour)
	if err := os.Chtimes(
		filepath.Join(home, "workspace", "old-ready", "plan", "roe.json"),
		old, old,
	); err != nil {
		t.Fatal(err)
	}

	got, err := ScanEngagements(home)
	if err != nil {
		t.Fatalf("ScanEngagements: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(got))
	}
	if got[0].Slug != "old-ready" {
		t.Errorf("expected ready engagement first; got %v", got)
	}
	if got[1].Slug != "fresh-incomplete" {
		t.Errorf("expected in-progress engagement second; got %v", got)
	}
}

func TestIsReady(t *testing.T) {
	home := t.TempDir()
	mkPlan(t, home, "complete", "roe.json", "conops.json", "deconfliction.json")
	mkPlan(t, home, "partial", "roe.json", "conops.json")

	if !isReady(home, "complete") {
		t.Errorf("complete should be ready")
	}
	if isReady(home, "partial") {
		t.Errorf("partial should not be ready")
	}
	if isReady(home, "missing") {
		t.Errorf("nonexistent dir should not be ready")
	}
}

func TestIsRedApprovedRequiresMatchingBundle(t *testing.T) {
	home := t.TempDir()
	mkPlan(t, home, "reviewed", planningDocuments...)
	marker := filepath.Join(home, "workspace", "reviewed", ".red-approved")
	if err := os.WriteFile(marker, []byte("23bb0ad057e96e5098d6c8ef731ce1afadd9e1957f6adf6b720196ad35409418"), 0o600); err != nil {
		t.Fatal(err)
	}
	if !isRedApproved(home, "reviewed") {
		t.Fatal("approved bundle was not selected for Red")
	}
	if err := os.WriteFile(filepath.Join(home, "workspace", "reviewed", "plan", "roe.json"), []byte(`{"changed":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if isRedApproved(home, "reviewed") {
		t.Fatal("changed bundle retained Red approval")
	}
}

func TestValidateSlug_AcceptsReasonableSlugs(t *testing.T) {
	home := t.TempDir()
	for _, slug := range []string{
		"acme-external-2026",
		"q1-internal",
		"engagement-001",
		"abc123",
	} {
		if err := validateSlug(home, slug); err != nil {
			t.Errorf("expected %q valid, got %v", slug, err)
		}
	}
}

func TestValidateSlug_RejectsBadShape(t *testing.T) {
	home := t.TempDir()
	tests := []struct {
		name string
		slug string
	}{
		{"too short", "ab"},
		{"too long", "a" + string(make([]byte, 64)) + "b"},
		{"uppercase", "Acme-2026"},
		{"underscore", "acme_2026"},
		{"leading hyphen", "-acme"},
		{"trailing hyphen", "acme-"},
		{"empty", ""},
		{"path traversal", "../etc"},
		{"slash", "acme/2026"},
		{"unicode", "acme™"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if err := validateSlug(home, tt.slug); err == nil {
				t.Errorf("expected %q (%s) to be rejected", tt.slug, tt.name)
			}
		})
	}
}

func TestValidateSlug_RejectsCollisionWithExistingDir(t *testing.T) {
	home := t.TempDir()
	// Even a partial / orphan engagement directory should block reuse.
	if err := os.MkdirAll(filepath.Join(home, "workspace", "acme-2026"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := validateSlug(home, "acme-2026"); err == nil {
		t.Error("expected collision rejection")
	}
}
