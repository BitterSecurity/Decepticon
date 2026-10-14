//go:build !windows

package opscontrol

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestServerListenRecoversEmptyDirectory(t *testing.T) {
	dir := socketTestDir(t)
	path := filepath.Join(dir, "ops.sock")
	if err := os.Mkdir(path, 0o555); err != nil {
		t.Fatal(err)
	}
	original, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	s, _ := newTestServer(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Listen(ctx, path) }()
	defer func() {
		cancel()
		select {
		case err := <-done:
			if !errors.Is(err, context.Canceled) {
				t.Errorf("Listen: %v", err)
			}
		case <-time.After(5 * time.Second):
			t.Error("Listen did not stop")
		}
	}()
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", path)
	}}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: time.Second}
	deadline := time.Now().Add(3 * time.Second)
	var response *http.Response
	for time.Now().Before(deadline) {
		response, err = client.Get("http://unix/v1/health")
		if err == nil {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err != nil {
		t.Fatalf("empty socket directory prevented health response: %v", err)
	}
	defer response.Body.Close()
	var health healthResponse
	if err := json.NewDecoder(response.Body).Decode(&health); err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK || !health.OK {
		t.Fatalf("unhealthy: %d %+v", response.StatusCode, health)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode()&os.ModeSocket == 0 || info.Mode().Perm() != 0o600 {
		t.Fatalf("socket mode: %v", info.Mode())
	}
	backups, err := filepath.Glob(path + ".backup-*")
	if err != nil || len(backups) != 1 {
		t.Fatalf("preserved directory: %v, %v", backups, err)
	}
	backup, err := os.Stat(backups[0])
	if err != nil {
		t.Fatal(err)
	}
	if !os.SameFile(original, backup) {
		t.Fatal("original directory inode was not preserved")
	}
}

func TestServerListenPreservesNonSocketPaths(t *testing.T) {
	for _, kind := range []string{"file", "nonempty-directory", "directory-symlink", "socket-symlink", "dangling-symlink"} {
		t.Run(kind, func(t *testing.T) {
			dir := socketTestDir(t)
			path := filepath.Join(dir, "ops.sock")
			target := filepath.Join(dir, "target")
			switch kind {
			case "file":
				if err := os.WriteFile(path, []byte("keep"), 0o600); err != nil {
					t.Fatal(err)
				}
			case "nonempty-directory":
				if err := os.Mkdir(path, 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(path, "keep"), []byte("keep"), 0o600); err != nil {
					t.Fatal(err)
				}
			case "directory-symlink":
				if err := os.Mkdir(target, 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(target, path); err != nil {
					t.Fatal(err)
				}
			case "socket-symlink":
				listener, err := net.Listen("unix", target)
				if err != nil {
					t.Fatal(err)
				}
				defer listener.Close()
				if err := os.Symlink(target, path); err != nil {
					t.Fatal(err)
				}
			case "dangling-symlink":
				if err := os.Symlink(target, path); err != nil {
					t.Fatal(err)
				}
			}
			original, err := os.Lstat(path)
			if err != nil {
				t.Fatal(err)
			}
			s, _ := newTestServer(t)
			ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
			defer cancel()
			if err := s.Listen(ctx, path); err == nil || errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("expected refused path, got %v", err)
			}
			preserved, err := os.Lstat(path)
			if err != nil {
				t.Fatal(err)
			}
			if !os.SameFile(original, preserved) {
				t.Fatal("existing inode replaced")
			}
		})
	}
}

func socketTestDir(t *testing.T) string {
	t.Helper()
	// Keep UDS paths below macOS's 104-byte limit, including subtest names.
	dir, err := os.MkdirTemp("", "ops-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(dir); err != nil {
			t.Error(err)
		}
	})
	return dir
}
