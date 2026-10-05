package engagement

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"sort"
	"strings"
)

type Artifact struct {
	Path string `json:"path"`
	Size int64  `json:"size"`
}

type ArtifactList struct {
	Artifacts []Artifact `json:"artifacts"`
	Truncated bool       `json:"truncated"`
}

type ArtifactContent struct {
	Path      string `json:"path"`
	Content   string `json:"content"`
	Truncated bool   `json:"truncated"`
}

func planDigest(root *os.Root) (string, error) {
	digest := sha256.New()
	for _, filename := range planningDocuments {
		content, err := root.ReadFile(path.Join("plan", filename))
		if err != nil {
			return "", fmt.Errorf("read plan/%s: %w", filename, err)
		}
		digest.Write([]byte(filename))
		digest.Write([]byte{0})
		digest.Write(content)
		digest.Write([]byte{0})
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func ApproveRed(home, slug string) error {
	choice, err := SelectExisting(home, slug)
	if err != nil {
		return err
	}
	root, err := os.OpenRoot(choice.WorkspacePath)
	if err != nil {
		return err
	}
	defer root.Close()
	digest, err := planDigest(root)
	if err != nil {
		return err
	}
	draft, err := root.ReadFile(".planning-draft-ready")
	if err != nil || strings.TrimSpace(string(draft)) != digest {
		return fmt.Errorf("planning draft is missing or changed; complete validation again")
	}
	return root.WriteFile(".red-approved", []byte(digest), 0o600)
}

func allowedArtifact(name string) bool {
	if len(name) > 512 || !fs.ValidPath(name) {
		return false
	}
	for _, segment := range strings.Split(name, "/") {
		if strings.HasPrefix(segment, ".") {
			return false
		}
	}
	return name == "graph.json" || strings.HasPrefix(name, "plan/") ||
		strings.HasPrefix(name, "findings/") || strings.HasPrefix(name, "report/")
}

func ReadArtifact(home, slug, name string, maxBytes int) (ArtifactContent, error) {
	if !allowedArtifact(name) || maxBytes < 1 || maxBytes > 262144 {
		return ArtifactContent{}, fmt.Errorf("invalid artifact path or max_bytes")
	}
	choice, err := SelectExisting(home, slug)
	if err != nil {
		return ArtifactContent{}, err
	}
	root, err := os.OpenRoot(choice.WorkspacePath)
	if err != nil {
		return ArtifactContent{}, err
	}
	defer root.Close()
	segments := strings.Split(name, "/")
	for index := range segments {
		info, err := root.Lstat(path.Join(segments[:index+1]...))
		if err != nil {
			return ArtifactContent{}, err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return ArtifactContent{}, fmt.Errorf("artifact path contains a symlink")
		}
	}
	file, err := root.Open(name)
	if err != nil {
		return ArtifactContent{}, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return ArtifactContent{}, fmt.Errorf("artifact is not a regular file")
	}
	content, err := io.ReadAll(io.LimitReader(file, int64(maxBytes)+1))
	if err != nil {
		return ArtifactContent{}, err
	}
	truncated := len(content) > maxBytes
	if truncated {
		content = content[:maxBytes]
	}
	return ArtifactContent{Path: name, Content: string(content), Truncated: truncated}, nil
}

func ListArtifacts(home, slug string) (ArtifactList, error) {
	choice, err := SelectExisting(home, slug)
	if err != nil {
		return ArtifactList{}, err
	}
	root, err := os.OpenRoot(choice.WorkspacePath)
	if err != nil {
		return ArtifactList{}, err
	}
	defer root.Close()
	listing := ArtifactList{Artifacts: make([]Artifact, 0)}
scan:
	for _, dir := range []string{".", "plan", "findings", "report"} {
		folder, err := root.Open(dir)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return ArtifactList{}, err
		}
		entries, readErr := folder.ReadDir(101)
		folder.Close()
		if readErr != nil && readErr != io.EOF {
			return ArtifactList{}, readErr
		}
		for _, entry := range entries {
			name := entry.Name()
			if dir != "." {
				name = path.Join(dir, name)
			}
			if !allowedArtifact(name) || !entry.Type().IsRegular() {
				continue
			}
			info, err := entry.Info()
			if err != nil {
				return ArtifactList{}, err
			}
			listing.Artifacts = append(listing.Artifacts, Artifact{Path: name, Size: info.Size()})
			if len(listing.Artifacts) == 100 {
				listing.Truncated = true
				break scan
			}
		}
		if len(entries) == 101 {
			listing.Truncated = true
		}
	}
	sort.Slice(listing.Artifacts, func(i, j int) bool {
		return listing.Artifacts[i].Path < listing.Artifacts[j].Path
	})
	return listing, nil
}
