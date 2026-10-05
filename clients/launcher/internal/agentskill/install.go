package agentskill

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var files = []string{"SKILL.md", "reference.md", "examples.md"}

type Options struct {
	Client  string
	Source  string
	Version string
	Force   bool
}

func Install(ctx context.Context, options Options) ([]string, error) {
	destinations, err := destinations(options.Client)
	if err != nil {
		return nil, err
	}
	bundle, err := load(ctx, options)
	if err != nil {
		return nil, err
	}
	installed := make([]string, 0, len(destinations))
	for _, destination := range destinations {
		if err := write(destination, bundle, options.Force); err != nil {
			return installed, err
		}
		installed = append(installed, destination)
	}
	return installed, nil
}

func destinations(client string) ([]string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, fmt.Errorf("resolve home directory: %w", err)
	}
	codeHome := os.Getenv("CODEX_HOME")
	if codeHome == "" {
		codeHome = filepath.Join(home, ".agents")
	}
	switch client {
	case "codex":
		return []string{filepath.Join(codeHome, "skills", "decepticon")}, nil
	case "claude":
		return []string{filepath.Join(home, ".claude", "skills", "decepticon")}, nil
	case "both":
		return []string{
			filepath.Join(codeHome, "skills", "decepticon"),
			filepath.Join(home, ".claude", "skills", "decepticon"),
		}, nil
	default:
		return nil, fmt.Errorf("client must be codex, claude, or both")
	}
}

func load(ctx context.Context, options Options) (map[string][]byte, error) {
	if options.Source != "" {
		bundle := make(map[string][]byte, len(files))
		for _, name := range files {
			data, err := os.ReadFile(filepath.Join(options.Source, name))
			if err != nil {
				return nil, fmt.Errorf("read %s: %w", name, err)
			}
			bundle[name] = data
		}
		return bundle, nil
	}
	version := strings.TrimPrefix(strings.TrimSpace(options.Version), "v")
	if version == "" || version == "dev" {
		return nil, fmt.Errorf("development builds require --from <skill-directory>")
	}
	client := &http.Client{Timeout: 20 * time.Second}
	return fetch(ctx, client, version,
		"https://raw.githubusercontent.com/BitterSecurity/Decepticon",
		"https://github.com/BitterSecurity/Decepticon/releases/download")
}

func fetch(ctx context.Context, client *http.Client, version, rawBase, releaseBase string) (map[string][]byte, error) {
	manifestURL := fmt.Sprintf("%s/v%s/agent-skill-checksums.txt", releaseBase, version)
	manifest, err := get(ctx, client, manifestURL, 16<<10)
	if err != nil {
		return nil, fmt.Errorf("fetch skill checksums: %w", err)
	}
	want := make(map[string]string, len(files))
	for _, line := range strings.Split(string(manifest), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 {
			want[fields[1]] = fields[0]
		}
	}
	bundle := make(map[string][]byte, len(files))
	for _, name := range files {
		path := "integrations/agent-skills/decepticon/" + name
		digest, ok := want[path]
		if !ok {
			return nil, fmt.Errorf("skill checksums missing %s", path)
		}
		data, err := get(ctx, client, fmt.Sprintf("%s/v%s/%s", rawBase, version, path), 256<<10)
		if err != nil {
			return nil, fmt.Errorf("fetch %s: %w", name, err)
		}
		actual := sha256.Sum256(data)
		if !strings.EqualFold(hex.EncodeToString(actual[:]), digest) {
			return nil, fmt.Errorf("checksum mismatch for %s", name)
		}
		bundle[name] = data
	}
	return bundle, nil
}

func get(ctx context.Context, client *http.Client, url string, maxBytes int64) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d from %s", response.StatusCode, url)
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, maxBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > maxBytes {
		return nil, fmt.Errorf("response exceeds %d bytes", maxBytes)
	}
	return data, nil
}

func write(destination string, bundle map[string][]byte, force bool) error {
	if info, err := os.Lstat(destination); err == nil {
		if !info.IsDir() {
			return fmt.Errorf("%s exists and is not a directory", destination)
		}
		equal := true
		for _, name := range files {
			data, readErr := os.ReadFile(filepath.Join(destination, name))
			if readErr != nil || !bytes.Equal(data, bundle[name]) {
				equal = false
				break
			}
		}
		if equal {
			return nil
		}
		if !force {
			return fmt.Errorf("%s already differs; use --force to back it up and replace it", destination)
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	parent := filepath.Dir(destination)
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return err
	}
	stage, err := os.MkdirTemp(parent, ".decepticon-skill-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	for _, name := range files {
		if err := os.WriteFile(filepath.Join(stage, name), bundle[name], 0o644); err != nil {
			return err
		}
	}
	backup := ""
	if _, err := os.Lstat(destination); err == nil {
		backup = destination + ".backup-" + time.Now().UTC().Format("20060102T150405.000000000")
		if err := os.Rename(destination, backup); err != nil {
			return err
		}
	}
	if err := os.Rename(stage, destination); err != nil {
		if backup != "" {
			_ = os.Rename(backup, destination)
		}
		return err
	}
	return nil
}
