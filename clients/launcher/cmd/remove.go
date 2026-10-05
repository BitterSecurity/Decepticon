package cmd

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"charm.land/huh/v2"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/compose"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/ui"
	"github.com/spf13/cobra"
)

var (
	removeYes               bool
	removePreserveWorkspace bool
)

var removeCmd = &cobra.Command{
	Use:     "remove",
	Aliases: []string{"uninstall"},
	Short:   "Uninstall Decepticon completely",
	RunE:    runRemove,
}

func init() {
	removeCmd.Flags().BoolVar(&removeYes, "yes", false, "Skip confirmation prompts")
	removeCmd.Flags().BoolVar(&removePreserveWorkspace, "preserve-workspace", false, "Back up workspace data before removing the installation")
	rootCmd.AddCommand(removeCmd)
}

func runRemove(cmd *cobra.Command, args []string) error {
	if !removeYes {
		var confirm bool
		form := huh.NewForm(
			huh.NewGroup(
				huh.NewConfirm().
					Title("Remove Decepticon?").
					Description("This will stop all services, remove Docker images, and delete configuration.").
					Affirmative("Yes, remove").
					Negative("Cancel").
					Value(&confirm),
			),
		)
		if err := form.Run(); err != nil || !confirm {
			ui.Info("Removal cancelled")
			return nil
		}
	}

	home := config.DecepticonHome()
	c := compose.New()
	preserveWorkspace := removePreserveWorkspace
	if !removeYes && !removePreserveWorkspace {
		form := huh.NewForm(huh.NewGroup(huh.NewConfirm().
			Title("Preserve workspace data?").
			Description(filepath.Join(home, "workspace")).
			Affirmative("Yes, keep my data").
			Negative("No, delete everything").
			Value(&preserveWorkspace)))
		_ = form.Run()
	}
	backupDir := ""
	workspaceToBackup := ""
	if preserveWorkspace {
		userHome, err := os.UserHomeDir()
		if err != nil {
			return fmt.Errorf("locate workspace backup directory: %w", err)
		}
		workspace := filepath.Join(home, "workspace")
		if _, err := os.Stat(workspace); err == nil {
			backupDir = filepath.Join(userHome, "decepticon-workspace-backup")
			if _, err := os.Lstat(backupDir); err == nil {
				return fmt.Errorf("workspace backup already exists: %s", backupDir)
			} else if !errors.Is(err, os.ErrNotExist) {
				return fmt.Errorf("inspect workspace backup: %w", err)
			}
			workspaceToBackup = workspace
		} else if !errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("inspect workspace before removal: %w", err)
		}
	}

	// Phase 1: Stop containers + drop named volumes (postgres / neo4j /
	// sliver). Down() alone leaves them behind, occupying GB of disk and
	// poisoning a subsequent reinstall with stale schema state.
	ui.Info("Stopping services and removing volumes...")
	if err := c.DownAndPurge(); err != nil {
		return fmt.Errorf("stop services before removal: %w", err)
	}
	c.RemoveOrphanedCLI()
	if workspaceToBackup != "" {
		ui.Info("Backing up workspace to " + backupDir)
		if err := backupWorkspace(workspaceToBackup, backupDir); err != nil {
			return fmt.Errorf("preserve workspace before removal: %w", err)
		}
	}

	// Phase 2: Remove Docker images
	ui.Info("Removing Docker images...")
	out, err := exec.Command("docker", "images", "--format", "{{.Repository}}:{{.Tag}}", "--filter", "reference=*decepticon*").Output()
	if err == nil {
		for _, img := range strings.Fields(strings.TrimSpace(string(out))) {
			_ = exec.Command("docker", "rmi", "-f", img).Run()
		}
	}

	// Phase 3: Remove config directory
	ui.Info("Removing " + home + "...")
	if err := os.RemoveAll(home); err != nil {
		return fmt.Errorf("remove installation directory %s: %w", home, err)
	}

	// Phase 4: Remove launcher binary
	execPath, _ := os.Executable()
	ui.Info("Removing launcher binary...")
	_ = os.Remove(execPath)

	// Phase 5: Clean PATH from shell rc files
	cleanShellRC()

	ui.Success("Decepticon has been removed")
	if backupDir != "" {
		ui.DimText("Workspace data preserved at " + backupDir)
	}
	return nil
}

// cleanShellRC removes PATH additions from shell config files.
func cleanShellRC() {
	home, err := os.UserHomeDir()
	if err != nil {
		return
	}

	rcFiles := []string{
		filepath.Join(home, ".bashrc"),
		filepath.Join(home, ".profile"),
		filepath.Join(home, ".zshrc"),
		filepath.Join(home, ".config", "fish", "config.fish"),
	}

	for _, rc := range rcFiles {
		cleanPathFromFile(rc)
	}
}

// cleanPathFromFile removes the exact two-line block install.sh appends:
//
//	# decepticon
//	export PATH="$HOME/.local/bin:$PATH"      # bash/zsh
//	fish_add_path $HOME/.local/bin            # fish
//
// Matching only this marker block avoids touching unrelated PATH lines
// the user may have written themselves. The previous heuristic looked for
// `decepticon` AND `.local/bin` on the same line, which never matched
// install.sh's actual output and left the export line behind on every
// uninstall.
func cleanPathFromFile(path string) {
	f, err := os.Open(path)
	if err != nil {
		return
	}
	var lines []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
	}
	f.Close()

	out := make([]string, 0, len(lines))
	changed := false
	i := 0
	for i < len(lines) {
		line := lines[i]
		if strings.TrimSpace(line) == "# decepticon" && i+1 < len(lines) && isInstallPathLine(lines[i+1]) {
			// Also drop a single preceding blank line install.sh inserts.
			if n := len(out); n > 0 && strings.TrimSpace(out[n-1]) == "" {
				out = out[:n-1]
			}
			i += 2
			changed = true
			continue
		}
		out = append(out, line)
		i++
	}

	if changed {
		_ = os.WriteFile(path, []byte(strings.Join(out, "\n")+"\n"), 0o644)
	}
}

// isInstallPathLine reports whether a line matches the PATH addition
// install.sh writes (bash/zsh export or fish_add_path with .local/bin).
func isInstallPathLine(line string) bool {
	s := strings.TrimSpace(line)
	if !strings.Contains(s, ".local/bin") {
		return false
	}
	return strings.HasPrefix(s, "export PATH=") || strings.HasPrefix(s, "fish_add_path ")
}

// backupWorkspace moves src to dst, falling back to copy + remove on
// cross-device or permission errors. Refuses to overwrite an existing dst
// so a previous backup is never silently clobbered.
func backupWorkspace(src, dst string) error {
	if _, err := os.Lstat(dst); err == nil {
		return fmt.Errorf("backup target already exists: %s", dst)
	}
	if err := os.Rename(src, dst); err == nil {
		return nil
	}
	// Cross-device or other rename failure: copy then remove.
	var err error
	if runtime.GOOS == "windows" {
		err = copyDirRecursive(src, dst)
	} else {
		_, err = exec.Command("cp", "-r", src, dst).CombinedOutput()
	}
	if err != nil {
		return fmt.Errorf("copy %s → %s: %w", src, dst, err)
	}
	return os.RemoveAll(src)

}

func copyDirRecursive(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, path)
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, info.Mode())
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, info.Mode())
	})
}
