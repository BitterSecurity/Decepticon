package cmd

import (
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/compose"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/runtime"
)

func TestRemovalComposeHelperProcess(t *testing.T) {
	if os.Getenv("DECEPTICON_REMOVE_TEST_HELPER") != "1" {
		return
	}
	if slices.Contains(os.Args, "--volumes") {
		if err := os.Remove(os.Getenv("DECEPTICON_REMOVE_TEST_VOLUME")); err != nil {
			os.Exit(2)
		}
	} else if os.Getenv("DECEPTICON_REMOVE_TEST_STOP_FAIL") == "1" {
		os.Exit(1)
	}
	os.Exit(0)
}

func removalFixture(t *testing.T) (*compose.Compose, string, string, string) {
	t.Helper()
	dir := t.TempDir()
	workspace := filepath.Join(dir, "workspace")
	backup := filepath.Join(dir, "backup")
	volume := filepath.Join(dir, "database-volume")
	if err := os.Mkdir(workspace, 0o700); err != nil {
		t.Fatal(err)
	}
	for name, value := range map[string]string{
		filepath.Join(workspace, "evidence.txt"): "evidence",
		volume:                                   "database data",
	} {
		if err := os.WriteFile(name, []byte(value), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("DECEPTICON_REMOVE_TEST_HELPER", "1")
	t.Setenv("DECEPTICON_REMOVE_TEST_VOLUME", volume)
	t.Setenv("DECEPTICON_REMOVE_TEST_STOP_FAIL", "0")
	stack := &compose.Compose{
		Home: dir, ComposeFile: filepath.Join(dir, "compose.yaml"), EnvFile: filepath.Join(dir, ".env"),
		Runtime: runtime.Runtime{Bin: os.Args[0], ComposeArgs: []string{"-test.run=TestRemovalComposeHelperProcess", "--"}},
	}
	return stack, workspace, backup, volume
}

func TestBackupFailurePreservesDatabaseVolumes(t *testing.T) {
	stack, workspace, backup, volume := removalFixture(t)
	if err := os.Mkdir(backup, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := stopAndBackupWorkspace(stack, workspace, backup); err == nil {
		t.Fatal("backup failure was ignored")
	}
	if _, err := os.Stat(volume); err != nil {
		t.Fatalf("database volume was purged despite failed backup: %v", err)
	}
	if _, err := os.Stat(filepath.Join(workspace, "evidence.txt")); err != nil {
		t.Fatalf("workspace was removed despite failed backup: %v", err)
	}
}

func TestStopFailurePreservesDatabaseAndWorkspace(t *testing.T) {
	stack, workspace, backup, volume := removalFixture(t)
	t.Setenv("DECEPTICON_REMOVE_TEST_STOP_FAIL", "1")
	if err := stopAndBackupWorkspace(stack, workspace, backup); err == nil {
		t.Fatal("service-stop failure was ignored")
	}
	for _, name := range []string{volume, filepath.Join(workspace, "evidence.txt")} {
		if _, err := os.Stat(name); err != nil {
			t.Fatalf("data was removed despite failed stop: %v", err)
		}
	}
}

func TestSuccessfulBackupPrecedesVolumePurge(t *testing.T) {
	stack, workspace, backup, volume := removalFixture(t)
	if err := stopAndBackupWorkspace(stack, workspace, backup); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(filepath.Join(backup, "evidence.txt"))
	if err != nil || string(content) != "evidence" {
		t.Fatalf("backup = %q, error = %v", content, err)
	}
	if _, err := os.Stat(volume); !os.IsNotExist(err) {
		t.Fatalf("successful uninstall did not purge volume: %v", err)
	}
}
