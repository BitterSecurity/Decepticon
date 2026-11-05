//go:build !windows

package cmd

// scheduleSelfDelete is a no-op outside Windows: os.Remove can unlink the
// running binary directly (the inode stays alive until the process exits),
// so there is nothing to defer.
func scheduleSelfDelete(_ string) error {
	return nil
}