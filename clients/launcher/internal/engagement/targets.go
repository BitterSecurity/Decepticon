package engagement

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"strings"
)

const (
	maxTargetFiles     = 10000
	maxTargetBytes     = 100 << 20
	maxTargetFileBytes = 16 << 20
	maxTargetDepth     = 32
)

type TargetImport struct {
	Engagement string `json:"engagement"`
	Source     string `json:"source"`
	Target     string `json:"target"`
	Files      int    `json:"files"`
	Bytes      int64  `json:"bytes"`
	Skipped    int    `json:"skipped"`
}

type targetCopier struct {
	ctx         context.Context
	source      *os.Root
	destination *os.Root
	directories int
	result      TargetImport
}

func ImportTarget(ctx context.Context, home, slug, source, name string) (TargetImport, error) {
	if !filepath.IsAbs(source) || !slugRe.MatchString(name) {
		return TargetImport{}, fmt.Errorf("source must be an absolute directory and name a 3-64 character slug")
	}
	info, err := os.Lstat(source)
	if err != nil {
		return TargetImport{}, fmt.Errorf("target source must be an existing directory: %w", err)
	}
	if !info.IsDir() {
		return TargetImport{}, fmt.Errorf("target source must be a directory")
	}
	choice, err := SelectExisting(home, slug)
	if err != nil {
		return TargetImport{}, err
	}
	canonicalSource, err := filepath.EvalSymlinks(source)
	if err != nil {
		return TargetImport{}, err
	}
	canonicalWorkspace, err := filepath.EvalSymlinks(choice.WorkspacePath)
	if err != nil {
		return TargetImport{}, err
	}
	containerPath := path.Join("/workspace/targets", name)
	newTarget := filepath.Join(canonicalWorkspace, "targets", name)
	if relative, err := filepath.Rel(canonicalSource, newTarget); err == nil &&
		(relative == "." || relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))) {
		return TargetImport{}, fmt.Errorf("source contains its destination")
	}
	sourceRoot, err := os.OpenRoot(canonicalSource)
	if err != nil {
		return TargetImport{}, err
	}
	defer sourceRoot.Close()
	workspaceRoot, err := os.OpenRoot(choice.WorkspacePath)
	if err != nil {
		return TargetImport{}, err
	}
	defer workspaceRoot.Close()
	if err := workspaceRoot.MkdirAll("targets", 0o700); err != nil {
		return TargetImport{}, err
	}
	destination := path.Join("targets", name)
	if err := workspaceRoot.Mkdir(destination, 0o700); err != nil {
		return TargetImport{}, fmt.Errorf("create target snapshot: %w", err)
	}
	copier := targetCopier{ctx: ctx, source: sourceRoot, destination: workspaceRoot,
		result: TargetImport{Engagement: slug, Source: canonicalSource, Target: containerPath}}
	if err := copier.copyDirectory(".", destination, 0); err != nil {
		if cleanupErr := workspaceRoot.RemoveAll(destination); cleanupErr != nil {
			return TargetImport{}, errors.Join(err, fmt.Errorf("remove partial target snapshot: %w", cleanupErr))
		}
		return TargetImport{}, err
	}
	return copier.result, nil
}

func (c *targetCopier) copyDirectory(sourceDir, destinationDir string, depth int) error {
	if err := c.ctx.Err(); err != nil {
		return err
	}
	if depth > maxTargetDepth {
		return fmt.Errorf("target directory depth exceeds %d", maxTargetDepth)
	}
	c.directories++
	if c.directories > maxTargetFiles {
		return fmt.Errorf("target snapshot exceeds directory count limit")
	}
	directory, err := c.source.Open(sourceDir)
	if err != nil {
		return err
	}
	defer directory.Close()
	for {
		entries, err := directory.ReadDir(256)
		if err != nil && err != io.EOF {
			return err
		}
		for _, entry := range entries {
			name := entry.Name()
			if excludedTargetName(name) {
				c.result.Skipped++
				continue
			}
			relative := path.Join(sourceDir, name)
			info, err := c.source.Lstat(relative)
			if err != nil {
				return err
			}
			if info.Mode()&os.ModeSymlink != 0 {
				return fmt.Errorf("target contains a symlink: %s", relative)
			}
			newPath := path.Join(destinationDir, name)
			if info.IsDir() {
				if err := c.destination.Mkdir(newPath, 0o700); err != nil {
					return err
				}
				if err := c.copyDirectory(relative, newPath, depth+1); err != nil {
					return err
				}
			} else if info.Mode().IsRegular() {
				if err := c.copyFile(relative, newPath, info); err != nil {
					return err
				}
			} else {
				return fmt.Errorf("target contains a special file: %s", relative)
			}
		}
		if err == io.EOF {
			return nil
		}
	}
}

func (c *targetCopier) copyFile(sourceFile, destinationFile string, info fs.FileInfo) error {
	if err := c.ctx.Err(); err != nil {
		return err
	}
	if c.result.Files >= maxTargetFiles || info.Size() > maxTargetFileBytes ||
		c.result.Bytes+info.Size() > maxTargetBytes {
		return fmt.Errorf("target snapshot exceeds file count or size limit at %s", sourceFile)
	}
	source, err := c.source.Open(sourceFile)
	if err != nil {
		return err
	}
	defer source.Close()
	destination, err := c.destination.OpenFile(destinationFile, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	defer destination.Close()
	bytes, err := io.Copy(destination, io.LimitReader(source, maxTargetFileBytes+1))
	if err != nil {
		return err
	}
	if bytes != info.Size() {
		return fmt.Errorf("target file changed during import: %s", sourceFile)
	}
	c.result.Files++
	c.result.Bytes += bytes
	return nil
}

func excludedTargetName(name string) bool {
	return name == ".git" || name == "node_modules" || name == ".venv" ||
		name == "__pycache__" || name == ".env" || strings.HasPrefix(name, ".env.")
}
