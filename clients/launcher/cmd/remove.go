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
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/compose"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/config"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/ui"
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
	Long: `Uninstall Decepticon completely: stop services, remove Docker
images and containers, and delete the installation directory
(~/.decepticon) including configuration and engagement workspaces.

With --preserve-workspace the engagement workspace data is backed up
before the installation directory is removed. With --yes all
confirmation prompts are skipped (useful for scripted teardown).`,
	Example: `  decepticon remove                        Remove everything after confirmation
  decepticon remove --preserve-workspace   Back up workspace data before removing
  decepticon remove --yes                  Remove without confirmation prompts`,
	RunE: runRemove,
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
		).WithTheme(huh.ThemeFunc(ui.DecepticonTheme))
		if err := form.Run(); err != nil || !confirm {
			ui.Info("Removal cancelled")
			return nil
		}
	}

	home := config.DecepticonHome()
	c := compose.New()
	preserveWorkspace := removePreserveWorkspace
	if !removeYes && !removePreserveWorkspace {
		preserveWorkspace = true
		form := huh.NewForm(huh.NewGroup(huh.NewConfirm().
			Title("Preserve workspace data?").
			Description(filepath.Join(home, "workspace")).
			Affirmative("Yes, keep my data").
			Negative("No, delete everything").
			Value(&preserveWorkspace))).WithTheme(huh.ThemeFunc(ui.DecepticonTheme))
		if err := form.Run(); err != nil {
			return fmt.Errorf("workspace preservation selection cancelled: %w", err)
		}
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

	if err := stopAndBackupWorkspace(c, workspaceToBackup, backupDir); err != nil {
		return err
	}
	c.RemoveOrphanedCLI()

	// Phase 2: Remove Docker images
	ui.Info("Removing Docker images...")
	out, err := exec.Command("docker", "images", "--format", "{{.Repository}}:{{.Tag}}", "--filter", "reference=*decepticon*").Output()
	if err == nil {
		for _, img := range strings.Fields(strings.TrimSpace(string(out))) {
			_ = exec.Command("docker", "rmi", "-f", img).Run()
		}
	}

	// Resolve the launcher before touching the installation directory. On
	// the Windows layout the launcher lives INSIDE the installation
	// directory (~/.decepticon/bin/decepticon.exe), and Windows cannot
	// delete a running executable — RemoveAll would abort mid-way, leave
	// the locked launcher behind, and the next install would fail (#1138).
	// Rename works on a running exe: move it out of the tree first, then
	// schedule its deletion after this process exits.
	execPath, execErr := os.Executable()
	launcherInsideHome := execErr == nil && isInsidePath(execPath, home)
	var deferredLauncherDelete string
	if launcherInsideHome {
		temp := filepath.Join(
			os.TempDir(),
			fmt.Sprintf("decepticon-remove-%d%s", os.Getpid(), filepath.Ext(execPath)),
		)
		if err := os.Rename(execPath, temp); err == nil {
			deferredLauncherDelete = temp
		} else {
			ui.Warning(fmt.Sprintf("Could not move the running launcher out of %s — the reinstall may fail until it is deleted manually: %v", home, err))
		}
	}

	// Phase 3: Remove config directory
	ui.Info("Removing " + home + "...")
	if err := os.RemoveAll(home); err != nil {
		return fmt.Errorf("remove installation directory %s: %w", home, err)
	}
	if deferredLauncherDelete != "" {
		_ = scheduleSelfDelete(deferredLauncherDelete)
	}

	// Phase 4: Remove launcher binary outside the installation directory
	// (POSIX layout: ~/.local/bin/decepticon). Unlinking a running binary
	// is safe on POSIX. Surface failures instead of silently swallowing
	// them so a leftover launcher stays diagnosable.
	if !launcherInsideHome && execErr == nil {
		ui.Info("Removing launcher binary...")
		if err := os.Remove(execPath); err != nil {
			ui.Warning(fmt.Sprintf("Could not remove the launcher binary at %s: %v", execPath, err))
		}
	}

	// Phase 5: Clean PATH from shell rc files
	cleanShellRC()

	ui.Success("Decepticon has been removed")
	if backupDir != "" {
		ui.DimText("Workspace data preserved at " + backupDir)
	}
	return nil
}

// isInsidePath reports whether path is located inside dir (or dir itself).
func isInsidePath(path, dir string) bool {
	rel, err := filepath.Rel(dir, path)
	if err != nil {
		return false
	}
	return rel == "." || (!strings.HasPrefix(rel, ".."+string(filepath.Separator)) && rel != "..")
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

func stopAndBackupWorkspace(c *compose.Compose, workspace, backup string) error {
	if workspace != "" {
		if err := c.Down(); err != nil {
			return fmt.Errorf("stop services before workspace backup: %w", err)
		}
		if err := backupWorkspace(workspace, backup); err != nil {
			return fmt.Errorf("preserve workspace before removal: %w", err)
		}
	}
	if err := c.DownAndPurge(); err != nil {
		return fmt.Errorf("purge services before removal: %w", err)
	}
	return nil
}
