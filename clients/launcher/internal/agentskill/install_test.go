package agentskill

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInstallFromSourcePreservesAndBacksUpExistingSkill(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("CODEX_HOME", filepath.Join(home, "custom-codex"))
	source := t.TempDir()
	for _, name := range files {
		if err := os.WriteFile(filepath.Join(source, name), []byte("original "+name), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	options := Options{Client: "both", Source: source}
	paths, err := Install(context.Background(), options)
	if err != nil {
		t.Fatal(err)
	}
	if len(paths) != 2 || paths[0] != filepath.Join(home, "custom-codex", "skills", "decepticon") || paths[1] != filepath.Join(home, ".claude", "skills", "decepticon") {
		t.Fatalf("unexpected skill paths: %v", paths)
	}
	for _, path := range paths {
		for _, name := range files {
			data, err := os.ReadFile(filepath.Join(path, name))
			if err != nil || string(data) != "original "+name {
				t.Fatalf("%s/%s = %q, %v", path, name, data, err)
			}
		}
	}
	if _, err := Install(context.Background(), options); err != nil {
		t.Fatalf("same version should be idempotent: %v", err)
	}
	modified := filepath.Join(paths[0], "SKILL.md")
	if err := os.WriteFile(modified, []byte("user changes"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Install(context.Background(), options); err == nil || !strings.Contains(err.Error(), "--force") {
		t.Fatalf("modified skill should require --force, got %v", err)
	}
	options.Force = true
	if _, err := Install(context.Background(), options); err != nil {
		t.Fatal(err)
	}
	backups, err := filepath.Glob(paths[0] + ".backup-*")
	if err != nil || len(backups) != 1 {
		t.Fatalf("expected one backup, got %v, %v", backups, err)
	}
	backup, err := os.ReadFile(filepath.Join(backups[0], "SKILL.md"))
	if err != nil || string(backup) != "user changes" {
		t.Fatalf("backup content = %q, %v", backup, err)
	}
}

func TestFetchVerifiesReleaseManifest(t *testing.T) {
	content := map[string][]byte{}
	manifest := ""
	for _, name := range files {
		path := "integrations/agent-skills/decepticon/" + name
		content[path] = []byte("content of " + name)
		digest := sha256.Sum256(content[path])
		manifest += fmt.Sprintf("%s  %s\n", hex.EncodeToString(digest[:]), path)
	}
	tamper := false
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if strings.HasSuffix(request.URL.Path, "agent-skill-checksums.txt") {
			fmt.Fprint(writer, manifest)
			return
		}
		for path, data := range content {
			if strings.HasSuffix(request.URL.Path, path) {
				if tamper && strings.HasSuffix(path, "SKILL.md") {
					data = []byte("tampered")
				}
				_, _ = writer.Write(data)
				return
			}
		}
		http.NotFound(writer, request)
	}))
	defer server.Close()
	bundle, err := fetch(context.Background(), server.Client(), "1.2.3", server.URL, server.URL)
	if err != nil || len(bundle) != len(files) {
		t.Fatalf("verified fetch = %d files, %v", len(bundle), err)
	}
	tamper = true
	if _, err := fetch(context.Background(), server.Client(), "1.2.3", server.URL, server.URL); err == nil || !strings.Contains(err.Error(), "checksum mismatch") {
		t.Fatalf("tampered skill should be rejected, got %v", err)
	}
}
