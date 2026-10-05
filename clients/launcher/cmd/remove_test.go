package cmd

import (
	"os"
	"path/filepath"
	"testing"
)

func TestBackupWorkspaceMovesDataAndRejectsExistingDestination(t *testing.T) {
	dir := t.TempDir()
	workspace := filepath.Join(dir, "workspace")
	backup := filepath.Join(dir, "backup")
	if err := os.Mkdir(workspace, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(workspace, "plan.json"), []byte("evidence"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := backupWorkspace(workspace, backup); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(backup, "plan.json"))
	if err != nil || string(data) != "evidence" {
		t.Fatalf("backup data = %q, error = %v", data, err)
	}
	if _, err := os.Stat(workspace); !os.IsNotExist(err) {
		t.Fatalf("workspace still exists after backup: %v", err)
	}
	if err := os.Mkdir(workspace, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := backupWorkspace(workspace, backup); err == nil {
		t.Fatal("existing backup was overwritten")
	}
}

func TestBackupWorkspaceRejectsDanglingDestinationSymlink(t *testing.T) {
	dir := t.TempDir()
	workspace := filepath.Join(dir, "workspace")
	backup := filepath.Join(dir, "backup")
	if err := os.Mkdir(workspace, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(dir, "missing"), backup); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := backupWorkspace(workspace, backup); err == nil {
		t.Fatal("dangling backup symlink was overwritten")
	}
}
