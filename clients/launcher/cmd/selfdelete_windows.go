//go:build windows

package cmd

import (
	"os/exec"
	"syscall"

	"golang.org/x/sys/windows"
)

// scheduleSelfDelete deletes path after the current process exits.
//
// Windows locks running executables, so the caller must first MOVE the
// running launcher to path (rename succeeds on a running exe); this spawns
// a detached helper that waits briefly for this process to release the
// moved file, then deletes it.
func scheduleSelfDelete(path string) error {
	helper := exec.Command(
		"cmd.exe", "/C",
		"ping -n 3 127.0.0.1 > nul & del /F /Q "+path,
	)
	helper.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: windows.CREATE_NEW_PROCESS_GROUP | windows.DETACHED_PROCESS,
	}
	return helper.Start()
}