package engagement

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestImportTargetCopiesCodeAndSkipsLocalSecrets(t *testing.T) {
	home := t.TempDir()
	workspace, err := CreateNamed(home, "local-code")
	if err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(t.TempDir(), "repository")
	if err := os.MkdirAll(filepath.Join(source, "src"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "src", "app.py"), []byte("print('ok')"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, ".env"), []byte("SECRET=value"), 0o600); err != nil {
		t.Fatal(err)
	}
	imported, err := ImportTarget(context.Background(), home, "local-code", source, "repository")
	if err != nil {
		t.Fatal(err)
	}
	if imported.Target != "/workspace/targets/repository" || imported.Files != 1 || imported.Skipped != 1 {
		t.Fatalf("unexpected import result: %#v", imported)
	}
	data, err := os.ReadFile(filepath.Join(workspace.WorkspacePath, "targets", "repository", "src", "app.py"))
	if err != nil || string(data) != "print('ok')" {
		t.Fatalf("copied code = %q, error = %v", data, err)
	}
	if _, err := os.Stat(filepath.Join(workspace.WorkspacePath, "targets", "repository", ".env")); !os.IsNotExist(err) {
		t.Fatal("local .env was copied into target snapshot")
	}
	if _, err := ImportTarget(context.Background(), home, "local-code", source, "repository"); err == nil {
		t.Fatal("existing target snapshot was overwritten")
	}
}

func TestImportTargetRejectsSymlinkAndCleansPartialSnapshot(t *testing.T) {
	home := t.TempDir()
	workspace, err := CreateNamed(home, "local-code")
	if err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(t.TempDir(), "repository")
	if err := os.Mkdir(source, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, "a.txt"), []byte("safe"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("/etc/passwd", filepath.Join(source, "z-link")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	_, err = ImportTarget(context.Background(), home, "local-code", source, "repository")
	if err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("symlink import error = %v", err)
	}
	if _, err := os.Stat(filepath.Join(workspace.WorkspacePath, "targets", "repository")); !os.IsNotExist(err) {
		t.Fatal("failed import left a partial target snapshot")
	}
}

func TestImportTargetRejectsSourceContainingDestination(t *testing.T) {
	home := t.TempDir()
	workspace, err := CreateNamed(home, "local-code")
	if err != nil {
		t.Fatal(err)
	}
	_, err = ImportTarget(context.Background(), home, "local-code", workspace.WorkspacePath, "repository")
	if err == nil || !strings.Contains(err.Error(), "contains its destination") {
		t.Fatalf("recursive target import error = %v", err)
	}
}

func TestImportTargetRejectsOversizedFile(t *testing.T) {
	home := t.TempDir()
	workspace, err := CreateNamed(home, "local-code")
	if err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(t.TempDir(), "repository")
	if err := os.Mkdir(source, 0o700); err != nil {
		t.Fatal(err)
	}
	file, err := os.Create(filepath.Join(source, "oversize.bin"))
	if err != nil {
		t.Fatal(err)
	}
	if err := file.Truncate(maxTargetFileBytes + 1); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := ImportTarget(context.Background(), home, "local-code", source, "repository"); err == nil {
		t.Fatal("oversized target was accepted")
	}
	if _, err := os.Stat(filepath.Join(workspace.WorkspacePath, "targets", "repository")); !os.IsNotExist(err) {
		t.Fatal("oversized import left a partial target snapshot")
	}
}
