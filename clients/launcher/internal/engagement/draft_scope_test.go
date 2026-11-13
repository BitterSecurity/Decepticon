package engagement

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestWriteDraftScopeOverwritesDraftButLocksAfterStart(t *testing.T) {
	home := t.TempDir()
	slug := "acme-ext"
	root := filepath.Join(home, "workspace", slug)
	roe := filepath.Join(root, "plan", "roe.json")

	// Create the engagement (draft).
	if _, err := createDirectEngagement(home, slug, scopeLists{InScope: []string{"a.example.com"}}, time.Now()); err != nil {
		t.Fatalf("createDirectEngagement: %v", err)
	}

	// Draft: edit allowed and takes effect.
	if err := writeDraftScope(home, slug, scopeLists{InScope: []string{"b.example.com"}, OutOfScope: []string{"admin.example.com"}}, time.Now()); err != nil {
		t.Fatalf("writeDraftScope (draft): %v", err)
	}
	data, err := os.ReadFile(roe)
	if err != nil {
		t.Fatalf("read roe: %v", err)
	}
	s := string(data)
	if !strings.Contains(s, "b.example.com") || strings.Contains(s, "a.example.com") {
		t.Errorf("draft edit did not replace scope:\n%s", s)
	}
	if !strings.Contains(s, "admin.example.com") {
		t.Errorf("draft edit did not write out_of_scope:\n%s", s)
	}

	// Start the engagement → scope locks.
	if err := MarkStarted(root); err != nil {
		t.Fatalf("MarkStarted: %v", err)
	}
	err = writeDraftScope(home, slug, scopeLists{InScope: []string{"c.example.com"}}, time.Now())
	if err == nil {
		t.Fatal("writeDraftScope succeeded after start; expected lock error")
	}
	if !strings.Contains(err.Error(), "locked") {
		t.Errorf("unexpected error (want lock): %v", err)
	}
	// The locked scope must be unchanged.
	data2, _ := os.ReadFile(roe)
	if strings.Contains(string(data2), "c.example.com") {
		t.Errorf("locked scope was modified:\n%s", data2)
	}
}

func TestWriteDraftScopeRejectsEmptyInScope(t *testing.T) {
	home := t.TempDir()
	slug := "empty-scope"
	if _, err := createDirectEngagement(home, slug, scopeLists{InScope: []string{"x.example.com"}}, time.Now()); err != nil {
		t.Fatalf("create: %v", err)
	}
	if err := writeDraftScope(home, slug, scopeLists{}, time.Now()); err == nil {
		t.Fatal("expected error for empty in-scope")
	}
}
