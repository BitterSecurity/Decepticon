package opscontrol

import (
	"os"
	"path/filepath"
	"testing"
)

func TestEngagementWorkspaceBindsSelectedDirectory(t *testing.T) {
	home := t.TempDir()
	t.Setenv("DECEPTICON_HOME", home)
	wanted := filepath.Join(home, "workspace", "sample-engagement")
	if err := os.MkdirAll(wanted, 0o700); err != nil {
		t.Fatal(err)
	}
	got, err := engagementWorkspace("sample-engagement")
	if err != nil || got != wanted {
		t.Fatalf("engagementWorkspace() = %q, %v; want %q", got, err, wanted)
	}
}

func TestEngagementWorkspaceRejectsEscapeAndMissingDirectory(t *testing.T) {
	home := t.TempDir()
	t.Setenv("DECEPTICON_HOME", home)
	root := filepath.Join(home, "workspace")
	if err := os.MkdirAll(root, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(t.TempDir(), filepath.Join(root, "linked-engagement")); err != nil {
		t.Fatal(err)
	}
	for _, slug := range []string{"../outside", "linked-engagement", "missing-engagement"} {
		if _, err := engagementWorkspace(slug); err == nil {
			t.Errorf("engagementWorkspace(%q) accepted an invalid path", slug)
		}
	}
}
