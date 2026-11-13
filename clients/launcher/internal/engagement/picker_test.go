package engagement

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func mkApprovedScope(t *testing.T, home, slug string) {
	t.Helper()
	mkPlan(t, home, slug)
	root := filepath.Join(home, "workspace", slug)
	roe := []byte(`{"engagement_name":"` + slug + `","machine_enforcement":{"mode":"enforce","in_scope":["example.com"],"out_of_scope":[]}}` + "\n")
	if err := os.WriteFile(filepath.Join(root, "plan", "roe.json"), roe, 0o600); err != nil {
		t.Fatal(err)
	}
}

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

func TestScanEngagements_ApprovedAndUnapproved(t *testing.T) {
	home := t.TempDir()

	mkApprovedScope(t, home, "alpha")
	mkPlan(t, home, "bravo", "roe.json")
	mkBareDir(t, home, "charlie")

	got, err := ScanEngagements(home)
	if err != nil {
		t.Fatalf("ScanEngagements: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("expected 3 entries, got %d (%v)", len(got), got)
	}
	byName := map[string]EngagementEntry{got[0].Slug: got[0], got[1].Slug: got[1], got[2].Slug: got[2]}
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

func TestScanEngagements_ApprovedEngagementsBubbleUp(t *testing.T) {
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

	mkApprovedScope(t, home, "old-ready")
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

func TestUnapprovedBundleDoesNotEnableRed(t *testing.T) {
	home := t.TempDir()
	mkPlan(t, home, "complete", "roe.json", "conops.json", "deconfliction.json")
	mkPlan(t, home, "partial", "roe.json", "conops.json")

	for _, slug := range []string{"complete", "partial", "missing"} {
		if scopeReady(home, slug) {
			t.Errorf("machine-scope-less %s enabled Red", slug)
		}
	}
}

func TestScopeReadyRequiresEnforcedMachineScope(t *testing.T) {
	home := t.TempDir()
	// A pre-unification dual-form document with an object-form machine block
	// stays selectable; audit mode and human-only documents do not enable Red.
	root := filepath.Join(home, "workspace", "legacy")
	if err := os.MkdirAll(filepath.Join(root, "plan"), 0o755); err != nil {
		t.Fatal(err)
	}
	legacy := filepath.Join(root, "plan", "roe.json")
	legacyDoc := []byte(`{"in_scope":[{"target":"https://example.com"}],"authorization_reference":"signed contract 123","machine_enforcement":{"mode":"enforce","in_scope":[{"target":"https://example.com","type":"auto"}],"authorized_windows":[{"start":"2026-10-22T00:00:00Z","end":"2026-10-29T00:00:00Z"}]}}`)
	if err := os.WriteFile(legacy, legacyDoc, 0o600); err != nil {
		t.Fatal(err)
	}
	if !scopeReady(home, "legacy") {
		t.Fatal("legacy dual-form machine scope was not recognized")
	}
	if err := os.WriteFile(legacy, []byte(`{"in_scope":[{"target":"https://example.com"}],"authorization_reference":"contract"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if scopeReady(home, "legacy") {
		t.Fatal("human-only scope enabled Red")
	}
	// A legacy "mode":"audit" document: the post-unification runtime ignores
	// the mode key and enforces every machine block unconditionally, so the
	// launcher's readiness must match the runtime and report ready.
	audit := []byte(`{"machine_enforcement":{"mode":"audit","in_scope":["example.com"]}}`)
	if err := os.WriteFile(legacy, audit, 0o600); err != nil {
		t.Fatal(err)
	}
	if !scopeReady(home, "legacy") {
		t.Fatal("legacy audit-mode machine scope was not recognized as ready")
	}
}

func TestDirectScopePinsTargetAndRoE(t *testing.T) {
	home := t.TempDir()
	root := filepath.Join(home, "workspace", "direct")
	mkApprovedScope(t, home, "direct")
	if !scopeReady(home, "direct") {
		t.Fatal("machine scope was not recognized")
	}
	if summary := readScopeSummary(root); summary.Target != "example.com" || !summary.Enforced {
		t.Fatalf("scope summary was not restored: %+v", summary)
	}
	// A legacy dual-form document still yields its first human target.
	if err := os.WriteFile(filepath.Join(root, "plan", "roe.json"), []byte(`{"in_scope":[{"target":"https://legacy.example.com"}],"authorization_reference":"contract"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if summary := readScopeSummary(root); summary.Target != "https://legacy.example.com" || summary.Enforced {
		t.Fatalf("legacy scope summary was not restored: %+v", summary)
	}
	if err := os.WriteFile(filepath.Join(root, "plan", "roe.json"), []byte(`{"changed":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if scopeReady(home, "direct") {
		t.Fatal("machine-block-less document enabled Red")
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

func TestPromptNewEngagement_RejectsEndOfInputBeforeTarget(t *testing.T) {
	// Given a workspace name but no target URL.
	home := t.TempDir()
	t.Setenv("TERM", "dumb")
	input, err := os.CreateTemp(t.TempDir(), "name-input")
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	if _, err := input.WriteString("direct-test\n"); err != nil {
		t.Fatal(err)
	}
	if _, err := input.Seek(0, 0); err != nil {
		t.Fatal(err)
	}
	previousInput := os.Stdin
	os.Stdin = input
	t.Cleanup(func() { os.Stdin = previousInput })
	// When target input ends before the URL is supplied.
	_, _, _, err = promptNewEngagement(home)
	// Then no unscoped engagement can be submitted.
	if err == nil {
		t.Fatal("accepted a name without a target")
	}
}

func TestPromptNewEngagement_RejectsEndOfInputBeforeName(t *testing.T) {
	// Given an accessible terminal whose input closes before a name is entered.
	home := t.TempDir()
	t.Setenv("TERM", "dumb")
	input, err := os.Open(os.DevNull)
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	previousInput := os.Stdin
	os.Stdin = input
	t.Cleanup(func() { os.Stdin = previousInput })

	// When the form reaches EOF without a name.
	_, _, _, err = promptNewEngagement(home)
	// Then it must not create an engagement at the workspace root.
	if err == nil {
		t.Fatal("accepted EOF without an engagement name")
	}
}

func TestChoiceForEngagement_OpensRedWithScopeState(t *testing.T) {
	for _, state := range []string{"empty", "machine", "legacy", "human-only"} {
		t.Run(state, func(t *testing.T) {
			// Given a workspace whose roe.json carries the state's shape.
			home := t.TempDir()
			mkBareDir(t, home, state)
			if state != "empty" {
				mkPlan(t, home, state)
				roePath := filepath.Join(home, "workspace", state, "plan", "roe.json")
				var doc string
				switch state {
				case "machine":
					doc = `{"engagement_name":"machine","machine_enforcement":{"mode":"enforce","in_scope":["example.com"]}}`
				case "legacy":
					doc = `{"in_scope":[{"target":"https://legacy.example.com"}],"authorization_reference":"contract","machine_enforcement":{"mode":"enforce","in_scope":[{"target":"https://legacy.example.com","type":"auto"}]}}`
				case "human-only":
					doc = `{"in_scope":[{"target":"https://human.example.com"}],"authorization_reference":"contract"}`
				}
				if err := os.WriteFile(roePath, []byte(doc), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			// When the picker translates that engagement into launch context.
			choice := choiceForEngagement(home, state)
			// Then Red is selected independently from the scope shape.
			if choice.AssistantID != AssistantDecepticon || choice.Mode != "red" {
				t.Fatalf("expected Red, got %+v", choice)
			}
			if choice.AuthorizationConfirmed != (state != "empty") {
				t.Fatalf("unexpected authorization declaration: %+v", choice)
			}
			if choice.WorkspacePath != filepath.Join(home, "workspace", state) {
				t.Fatalf("wrong workspace: %+v", choice)
			}
			// Then only an enforced machine scope marks the engagement ready.
			if scopeReady(home, state) != (state == "machine" || state == "legacy") {
				t.Fatalf("unexpected readiness for %s", state)
			}
		})
	}
}

func TestPickerItem_Description_ShowsScopeSummary(t *testing.T) {
	draft := pickerItem{slug: "alpha", ready: true, scope: scopeSummary{Target: "example.com", InCount: 3, DenyCount: 1, Enforced: true}}
	if got := draft.Description(); got != "Red ready · 3 in scope · 1 denied · example.com · draft — edit scope before starting" {
		t.Fatalf("unexpected draft description: %q", got)
	}
	locked := pickerItem{slug: "alpha", ready: true, started: true, scope: scopeSummary{Target: "example.com", InCount: 3, DenyCount: 1, Enforced: true}}
	if got := locked.Description(); got != "Red ready · 3 in scope · 1 denied · example.com · locked — scope immutable (new engagement for a different scope)" {
		t.Fatalf("unexpected locked description: %q", got)
	}
	progress := pickerItem{slug: "bravo", inProgress: true}
	if got := progress.Description(); got == "" || got == "Authorized Red scope available" {
		t.Fatalf("unexpected in-progress description: %q", got)
	}
}
