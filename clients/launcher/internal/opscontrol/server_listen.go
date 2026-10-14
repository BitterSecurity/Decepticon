package opscontrol

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

// Listen binds the UDS at socketPath and runs the HTTP server. The
// socket is removed on shutdown; stale sockets are removed before binding.
func (s *Server) Listen(ctx context.Context, socketPath string) error {
	// Stale socket file from a previous unclean shutdown would
	// otherwise fail the bind. Lstat keeps symlinks out of recovery.
	if info, err := os.Lstat(socketPath); err == nil {
		if info.IsDir() {
			// Older Compose short bind mounts created directories when the
			// daemon was unavailable. Preserve an empty collision before binding.
			entries, err := os.ReadDir(socketPath)
			if err != nil {
				return fmt.Errorf("opscontrol: inspect socket directory: %w", err)
			}
			if len(entries) != 0 {
				return fmt.Errorf("opscontrol: %s is a nonempty directory; move it aside before restarting", socketPath)
			}
			backupPath, err := os.MkdirTemp(filepath.Dir(socketPath), filepath.Base(socketPath)+".backup-")
			if err != nil {
				return fmt.Errorf("opscontrol: create socket directory backup: %w", err)
			}
			// Rename requires an absent destination; remove only our empty placeholder.
			if err := os.Remove(backupPath); err != nil {
				return fmt.Errorf("opscontrol: prepare socket directory backup: %w", err)
			}
			// Keep the same parent: Docker may own the directory, so we cannot update its ".." entry.
			if err := os.Rename(socketPath, backupPath); err != nil {
				return fmt.Errorf("opscontrol: preserve socket directory: %w", err)
			}
			s.Logger.Warn("opscontrol preserved empty socket directory", "path", socketPath, "backup", backupPath)
		} else if info.Mode()&os.ModeSocket == 0 {
			return fmt.Errorf("opscontrol: %s exists and is not a socket; refusing to overwrite", socketPath)
		} else if err := os.Remove(socketPath); err != nil {
			return fmt.Errorf("opscontrol: remove stale socket: %w", err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("opscontrol: inspect socket path: %w", err)
	}

	lis, err := net.Listen("unix", socketPath)
	if err != nil {
		return fmt.Errorf("opscontrol: listen unix %s: %w", socketPath, err)
	}
	// Tighten permissions so only the user that owns $DECEPTICON_HOME
	// (and root, and processes that share the bind-mount) can connect.
	if err := os.Chmod(socketPath, 0o600); err != nil {
		_ = lis.Close()
		return fmt.Errorf("opscontrol: chmod socket: %w", err)
	}

	s.Logger.Info("opscontrol daemon up",
		"socket", socketPath,
		"backend", s.Backend.Name(),
		"allowlist_size", len(s.Allowlist.Members()),
	)

	srv := &http.Server{
		Handler:           s.mux(),
		ReadHeaderTimeout: 5 * time.Second,
	}

	errc := make(chan error, 1)
	go func() {
		errc <- srv.Serve(lis)
	}()

	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
		_ = os.Remove(socketPath)
		return ctx.Err()
	case err := <-errc:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}
}
