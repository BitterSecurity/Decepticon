package engagement

import (
	"os"
	"path/filepath"
	"testing"
)

func TestApproveRedRequiresCurrentDraft(t *testing.T) {
	home := t.TempDir()
	choice, err := CreateNamed(home, "review-test")
	if err != nil {
		t.Fatal(err)
	}
	for _, filename := range planningDocuments {
		if err := os.WriteFile(filepath.Join(choice.WorkspacePath, "plan", filename), []byte("{}"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := ApproveRed(home, "review-test"); err == nil {
		t.Fatal("approval without validated draft succeeded")
	}
	root, err := os.OpenRoot(choice.WorkspacePath)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close()
	digest, err := planDigest(root)
	if err != nil {
		t.Fatal(err)
	}
	if err := root.WriteFile(".planning-draft-ready", []byte(digest), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ApproveRed(home, "review-test"); err != nil {
		t.Fatal(err)
	}
	selected, err := SelectExisting(home, "review-test")
	if err != nil || selected.AssistantID != AssistantDecepticon {
		t.Fatalf("selected = %#v, %v", selected, err)
	}
	if err := root.WriteFile("plan/roe.json", []byte(`{"changed":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ApproveRed(home, "review-test"); err == nil {
		t.Fatal("changed plan was approved without revalidation")
	}
}

func TestArtifactReadsStayInWorkspaceAndAreBounded(t *testing.T) {
	home := t.TempDir()
	choice, err := CreateNamed(home, "artifact-test")
	if err != nil {
		t.Fatal(err)
	}
	for _, dir := range []string{"findings", "report"} {
		if err := os.Mkdir(filepath.Join(choice.WorkspacePath, dir), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	for name, content := range map[string]string{
		"plan/roe.json": "{}", "findings/FIND-1.md": "evidence", "report/summary.md": "summary",
		"graph.json": "{}", ".red-approved": "private",
	} {
		if err := os.WriteFile(filepath.Join(choice.WorkspacePath, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	listing, err := ListArtifacts(home, "artifact-test")
	if err != nil || len(listing.Artifacts) != 4 || listing.Truncated {
		t.Fatalf("listing = %#v, %v", listing, err)
	}
	read, err := ReadArtifact(home, "artifact-test", "findings/FIND-1.md", 3)
	if err != nil || read.Content != "evi" || !read.Truncated {
		t.Fatalf("read = %#v, %v", read, err)
	}
	for _, name := range []string{"../outside", "/etc/passwd", ".red-approved", "plan/.hidden/file", "plan"} {
		if _, err := ReadArtifact(home, "artifact-test", name, 100); err == nil {
			t.Fatalf("unsafe artifact path %q accepted", name)
		}
	}
	if _, err := ReadArtifact(home, "artifact-test", "graph.json", 0); err == nil {
		t.Fatal("zero byte limit accepted")
	}
	outside := filepath.Join(t.TempDir(), "outside.txt")
	if err := os.WriteFile(outside, []byte("private"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(choice.WorkspacePath, "report", "escape.md")
	if err := os.Symlink(outside, link); err == nil {
		if _, err := ReadArtifact(home, "artifact-test", "report/escape.md", 100); err == nil {
			t.Fatalf("escaped symlink read error = %v", err)
		}
	}
	internalLink := filepath.Join(choice.WorkspacePath, "report", "alias.md")
	if err := os.Symlink(filepath.Join("..", ".red-approved"), internalLink); err == nil {
		if _, err := ReadArtifact(home, "artifact-test", "report/alias.md", 100); err == nil {
			t.Fatal("internal symlink exposed a hidden workspace file")
		}
	}
}
