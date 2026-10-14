package updater

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestCompareVersions(t *testing.T) {
	tests := []struct {
		current, latest string
		want            bool
	}{
		{"1.0.0", "1.1.0", true},
		{"1.1.0", "1.0.0", false},
		{"1.0.0", "1.0.0", false},
		{"v1.0.0", "v1.1.0", true},
		{"dev", "1.0.0", false},
		{"", "1.0.0", false},
		// Numeric semver: 1.9 → 1.10 must trigger update
		{"1.9.0", "1.10.0", true},
		{"1.10.0", "1.9.0", false},
		{"2.0.0", "1.99.99", false},
		{"0.9.9", "1.0.0", true},
		// SemVer §11 prerelease precedence: a prerelease is LOWER than
		// its associated normal version. Matters for the `latest`
		// channel, which surfaces -rc builds.
		{"1.2.0-rc.1", "1.2.0", true},      // rc → final is an update
		{"1.2.0", "1.2.0-rc.1", false},     // final → rc is NOT an update (downgrade)
		{"1.2.0-rc.1", "1.2.0-rc.2", true}, // rc.1 → rc.2 is an update
		{"1.2.0-rc.2", "1.2.0-rc.1", false},
		{"1.2.0-rc.2", "1.2.0-rc.10", true}, // numeric identifiers compare numerically (10 > 2)
		{"1.2.0-rc.1", "1.2.0-rc.1", false},
		{"1.1.0", "1.2.0-rc.1", true}, // a prerelease of a higher version still beats a lower final
		{"1.2.0-rc.1", "1.1.0", false},
		{"v1.2.0-rc.1", "v1.2.0", true},      // tolerates the leading v
		{"1.2.0-alpha", "1.2.0-beta", true},  // alphanumeric identifiers compare lexically
		{"1.2.0-rc.1", "1.2.0-rc.1.1", true}, // more fields > fewer when the prefix is equal
	}
	for _, tt := range tests {
		got := CompareVersions(tt.current, tt.latest)
		if got != tt.want {
			t.Errorf("CompareVersions(%q, %q) = %v, want %v", tt.current, tt.latest, got, tt.want)
		}
	}
}

func TestResolveChannel(t *testing.T) {
	// Default + unrecognized → stable (Decepticon's conservative default;
	// soak semantics still match Claude Code, only the default differs).
	tests := map[string]Channel{
		"":          ChannelStable, // default
		"stable":    ChannelStable,
		"STABLE":    ChannelStable,
		"  stable ": ChannelStable,
		"latest":    ChannelLatest,
		"LATEST":    ChannelLatest,
		" latest":   ChannelLatest,
		"garbage":   ChannelStable, // unrecognized → safe default (stable)
	}
	for in, want := range tests {
		if got := ResolveChannel(in); got != want {
			t.Errorf("ResolveChannel(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestDisplayVersion(t *testing.T) {
	tests := map[string]string{
		"1.0.22":  "v1.0.22",
		"v1.0.22": "v1.0.22",
		"dev":     "dev",
		"":        "",
	}
	for input, want := range tests {
		if got := displayVersion(input); got != want {
			t.Errorf("displayVersion(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestFetchLatestRelease_Mock(t *testing.T) {
	release := Release{
		TagName: "v1.2.0",
		Assets: []Asset{
			{Name: "decepticon-linux-amd64", BrowserDownloadURL: "https://example.com/binary"},
		},
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(release)
	}))
	defer server.Close()

	// Can't easily test FetchLatestRelease without changing the URL,
	// so we test the JSON parsing directly
	resp, err := http.Get(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	var got Release
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}

	if got.TagName != "v1.2.0" {
		t.Errorf("TagName = %q, want v1.2.0", got.TagName)
	}
	if len(got.Assets) != 1 || got.Assets[0].Name != "decepticon-linux-amd64" {
		t.Errorf("Assets = %v", got.Assets)
	}
}

func TestFetchRelease_LatestUsesReleasesLatestEndpoint(t *testing.T) {
	// latest = newest FINAL release immediately. GitHub's /releases/latest
	// already excludes pre-releases and drafts.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/releases/latest" {
			t.Errorf("latest channel must call /releases/latest, got %q", r.URL.Path)
		}
		json.NewEncoder(w).Encode(Release{TagName: "v1.3.0"})
	}))
	defer srv.Close()
	defer withAPIBaseURL(srv.URL)()

	rel, err := FetchRelease(ChannelLatest)
	if err != nil {
		t.Fatal(err)
	}
	if rel.TagName != "v1.3.0" {
		t.Errorf("TagName = %q, want v1.3.0", rel.TagName)
	}
}

func TestFetchRelease_StableSoaksByPublishedAt(t *testing.T) {
	// stable = newest FINAL release that has baked for the soak window.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/releases/latest" {
			t.Errorf("stable channel must list /releases, not /releases/latest")
		}
		json.NewEncoder(w).Encode([]Release{
			{TagName: "v1.3.0", PublishedAt: "2026-06-10T00:00:00Z"},                        // 2d old — too fresh
			{TagName: "v1.2.0", PublishedAt: "2026-06-01T00:00:00Z"},                        // 11d old — soaked, newest soaked
			{TagName: "v1.1.0", PublishedAt: "2026-05-01T00:00:00Z"},                        // soaked but lower
			{TagName: "v1.4.0-rc.1", Prerelease: true, PublishedAt: "2026-05-01T00:00:00Z"}, // prerelease — excluded
			{TagName: "v2.0.0", Draft: true, PublishedAt: "2026-01-01T00:00:00Z"},           // draft — excluded
		})
	}))
	defer srv.Close()
	defer withAPIBaseURL(srv.URL)()
	defer withSoakClock(time.Date(2026, 6, 12, 0, 0, 0, 0, time.UTC), 7*24*time.Hour)()

	rel, err := FetchRelease(ChannelStable)
	if err != nil {
		t.Fatal(err)
	}
	if rel.TagName != "v1.2.0" {
		t.Errorf("stable TagName = %q, want v1.2.0 (newest final older than 7d)", rel.TagName)
	}
}

func TestFetchRelease_StableFallsBackToLatestWhenNoneSoaked(t *testing.T) {
	// Every release is younger than the soak window (brand-new project) —
	// stable must still resolve, by falling back to the newest final.
	hits := map[string]int{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits[r.URL.Path]++
		if r.URL.Path == "/releases/latest" {
			json.NewEncoder(w).Encode(Release{TagName: "v1.3.0"})
			return
		}
		json.NewEncoder(w).Encode([]Release{
			{TagName: "v1.3.0", PublishedAt: "2026-06-11T00:00:00Z"}, // 1d old
		})
	}))
	defer srv.Close()
	defer withAPIBaseURL(srv.URL)()
	defer withSoakClock(time.Date(2026, 6, 12, 0, 0, 0, 0, time.UTC), 7*24*time.Hour)()

	rel, err := FetchRelease(ChannelStable)
	if err != nil {
		t.Fatal(err)
	}
	if rel.TagName != "v1.3.0" {
		t.Errorf("fallback TagName = %q, want v1.3.0", rel.TagName)
	}
	if hits["/releases/latest"] == 0 {
		t.Errorf("expected a fallback to /releases/latest")
	}
}

func TestPickSoakedStable(t *testing.T) {
	now := time.Date(2026, 6, 12, 0, 0, 0, 0, time.UTC)
	soak := 7 * 24 * time.Hour
	got := pickSoakedStable([]Release{
		{TagName: "v1.3.0", PublishedAt: "2026-06-10T00:00:00Z"}, // 2d — too fresh
		{TagName: "v1.2.0", PublishedAt: "2026-06-01T00:00:00Z"}, // 11d — soaked, highest
		{TagName: "v1.1.0", PublishedAt: "2026-05-01T00:00:00Z"}, // soaked, lower
		{TagName: "v1.4.0-rc.1", Prerelease: true, PublishedAt: "2026-04-01T00:00:00Z"},
		{TagName: "v2.0.0", Draft: true, PublishedAt: "2026-04-01T00:00:00Z"},
		{TagName: "", PublishedAt: "2026-04-01T00:00:00Z"},
	}, now, soak)
	if got == nil || got.TagName != "v1.2.0" {
		t.Fatalf("pickSoakedStable = %+v, want v1.2.0", got)
	}
	// None soaked → nil (caller falls back).
	none := pickSoakedStable([]Release{
		{TagName: "v1.3.0", PublishedAt: "2026-06-11T00:00:00Z"},
	}, now, soak)
	if none != nil {
		t.Errorf("pickSoakedStable(all fresh) = %+v, want nil", none)
	}
	if pickSoakedStable(nil, now, soak) != nil {
		t.Errorf("pickSoakedStable(nil) should be nil")
	}
}

// withAPIBaseURL points the updater at a test server and returns a
// restore func.
func withAPIBaseURL(url string) func() {
	old := APIBaseURL
	APIBaseURL = url
	return func() { APIBaseURL = old }
}

// withSoakClock pins the updater's "now" and soak window for deterministic
// stable-channel soak math; returns a restore func.
func withSoakClock(now time.Time, soak time.Duration) func() {
	oldNow, oldSoak := nowFn, stableSoak
	nowFn = func() time.Time { return now }
	stableSoak = soak
	return func() { nowFn, stableSoak = oldNow, oldSoak }
}

func TestPromptIfUpdateAvailable_SkipsDevBuilds(t *testing.T) {
	// "dev" / empty version means a local build that does not track
	// published releases — no prompt, no GitHub round-trip.
	for _, v := range []string{"dev", ""} {
		applied, err := PromptIfUpdateAvailable(v, ChannelStable)
		if err != nil {
			t.Errorf("PromptIfUpdateAvailable(%q) err = %v", v, err)
		}
		if applied {
			t.Errorf("PromptIfUpdateAvailable(%q) applied=true, want false", v)
		}
	}
}

func TestPromptIfUpdateAvailable_SkipsNonInteractive(t *testing.T) {
	// Test runs are always non-interactive (stdin is the test harness
	// pipe), so PromptIfUpdateAvailable must fall straight through
	// without ever calling huh.Run. A non-zero version that would
	// otherwise fail the "is dev?" gate is safe — the function returns
	// silently on the TTY check before fetching anything.
	applied, err := PromptIfUpdateAvailable("0.0.0", ChannelStable)
	if err != nil {
		t.Errorf("PromptIfUpdateAvailable err = %v", err)
	}
	if applied {
		t.Errorf("PromptIfUpdateAvailable applied=true, want false (no TTY)")
	}
}

func TestApplyUpdate_NilRelease(t *testing.T) {
	if err := ApplyUpdate(nil, "main"); err == nil {
		t.Error("ApplyUpdate(nil, ...) err = nil, want non-nil")
	}
}

// ---- downloadFile ----

func TestDownloadFile_Success(t *testing.T) {
	want := "hello from mock server"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, want)
	}))
	defer server.Close()

	dst := filepath.Join(t.TempDir(), "out.txt")
	if err := downloadFile(&http.Client{}, server.URL, dst); err != nil {
		t.Fatalf("downloadFile: %v", err)
	}
	got, err := os.ReadFile(dst)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if string(got) != want {
		t.Errorf("content = %q, want %q", got, want)
	}
}

func TestDownloadFile_HTTPError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	err := downloadFile(&http.Client{}, server.URL, filepath.Join(t.TempDir(), "out.txt"))
	if err == nil {
		t.Fatal("expected error for HTTP 500, got nil")
	}
	if !strings.Contains(err.Error(), "HTTP 500") {
		t.Errorf("error %q should contain 'HTTP 500'", err)
	}
}

func TestDownloadFile_CreatesParentDirectory(t *testing.T) {
	want := "nested content"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, want)
	}))
	defer server.Close()

	// dst is three levels deep inside a dir that doesn't exist yet.
	dst := filepath.Join(t.TempDir(), "a", "b", "c", "out.txt")
	if err := downloadFile(&http.Client{}, server.URL, dst); err != nil {
		t.Fatalf("downloadFile: %v", err)
	}
	got, err := os.ReadFile(dst)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if string(got) != want {
		t.Errorf("content = %q, want %q", got, want)
	}
}

func TestDownloadFile_NetworkError(t *testing.T) {
	// Start then immediately close the server so the port is unreachable.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := server.URL
	server.Close()

	err := downloadFile(&http.Client{}, url, filepath.Join(t.TempDir(), "out.txt"))
	if err == nil {
		t.Fatal("expected error for closed server, got nil")
	}
}

// ---- SelfUpdate ----

func TestSelfUpdate_NoMatchingAsset(t *testing.T) {
	// A release whose only asset targets a different platform should
	// fail with an error that names the current GOOS/GOARCH so the
	// operator knows why the binary was not replaced.
	release := &Release{
		TagName: "v9.9.9",
		Assets:  []Asset{{Name: "decepticon-plan9-mips", BrowserDownloadURL: "http://localhost/plan9"}},
	}
	err := SelfUpdate(release)
	if err == nil {
		t.Fatal("SelfUpdate with no matching asset: expected error, got nil")
	}
	if !strings.Contains(err.Error(), runtime.GOOS) || !strings.Contains(err.Error(), runtime.GOARCH) {
		t.Errorf("error %q should mention GOOS=%s GOARCH=%s", err, runtime.GOOS, runtime.GOARCH)
	}
}

func TestSelfUpdate_DownloadHTTPError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer server.Close()

	assetName := fmt.Sprintf("decepticon-%s-%s", runtime.GOOS, runtime.GOARCH)
	release := &Release{
		TagName: "v9.9.9",
		Assets:  []Asset{{Name: assetName, BrowserDownloadURL: server.URL + "/binary"}},
	}
	if err := SelfUpdate(release); err == nil {
		t.Fatal("SelfUpdate with HTTP 403: expected error, got nil")
	}
}

func TestSelfUpdate_WritesAndRenames(t *testing.T) {
	// Verify that on a successful download the binary is written to
	// execPath and the .tmp file is removed.
	binaryContent := []byte("fake binary content for testing")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(binaryContent)
	}))
	defer server.Close()

	dir := t.TempDir()
	fakeBin := filepath.Join(dir, "decepticon")
	previousContent := []byte("previous binary content")
	if err := os.WriteFile(fakeBin, previousContent, 0o755); err != nil {
		t.Fatalf("seed current binary: %v", err)
	}

	// Redirect executableFn so SelfUpdate writes into our temp dir instead
	// of clobbering the running test binary. Matches the isWSLFn pattern.
	orig := executableFn
	executableFn = func() (string, error) { return fakeBin, nil }
	t.Cleanup(func() { executableFn = orig })

	assetName := fmt.Sprintf("decepticon-%s-%s", runtime.GOOS, runtime.GOARCH)
	release := &Release{
		TagName: "v9.9.9",
		Assets:  []Asset{{Name: assetName, BrowserDownloadURL: server.URL + "/binary"}},
	}
	if err := SelfUpdate(release); err != nil {
		t.Fatalf("SelfUpdate: %v", err)
	}

	got, err := os.ReadFile(fakeBin)
	if err != nil {
		t.Fatalf("ReadFile after SelfUpdate: %v", err)
	}
	if string(got) != string(binaryContent) {
		t.Errorf("binary content = %q, want %q", got, binaryContent)
	}
	// The temp file must be cleaned up by the rename.
	if _, statErr := os.Stat(fakeBin + ".tmp"); !os.IsNotExist(statErr) {
		t.Error(".tmp file should not exist after a successful rename")
	}
	if runtime.GOOS == "windows" {
		previous, readErr := os.ReadFile(fakeBin + ".old")
		if readErr != nil {
			t.Fatalf("read Windows backup: %v", readErr)
		}
		if string(previous) != string(previousContent) {
			t.Errorf("Windows backup content = %q, want %q", previous, previousContent)
		}
	}
}

func TestReplaceExecutable_WindowsRollsBackFailedInstall(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows executable replacement only")
	}

	dir := t.TempDir()
	execPath := filepath.Join(dir, "decepticon.exe")
	previousContent := []byte("previous binary content")
	if err := os.WriteFile(execPath, previousContent, 0o755); err != nil {
		t.Fatalf("seed current binary: %v", err)
	}

	err := replaceExecutable(filepath.Join(dir, "missing.tmp"), execPath)
	if err == nil {
		t.Fatal("replaceExecutable with missing temp file: expected error, got nil")
	}

	current, readErr := os.ReadFile(execPath)
	if readErr != nil {
		t.Fatalf("read current binary after rollback: %v", readErr)
	}
	if string(current) != string(previousContent) {
		t.Errorf("current binary after rollback = %q, want %q", current, previousContent)
	}
	if _, statErr := os.Stat(execPath + ".old"); !os.IsNotExist(statErr) {
		t.Errorf("backup should be restored after a failed install; stat error = %v", statErr)
	}
}

func TestCleanupOldBinary_WindowsRemovesStaleBackup(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows executable cleanup only")
	}

	dir := t.TempDir()
	fakeBin := filepath.Join(dir, "decepticon.exe")
	oldPath := fakeBin + ".old"
	if err := os.WriteFile(oldPath, []byte("stale binary"), 0o755); err != nil {
		t.Fatalf("seed stale backup: %v", err)
	}

	orig := executableFn
	executableFn = func() (string, error) { return fakeBin, nil }
	t.Cleanup(func() { executableFn = orig })

	CleanupOldBinary()

	if _, statErr := os.Stat(oldPath); !os.IsNotExist(statErr) {
		t.Errorf("stale backup should be removed; stat error = %v", statErr)
	}
}

// ---- WriteVersion ----

func TestWriteVersion_StripsVPrefix(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("DECEPTICON_HOME", dir)

	if err := WriteVersion("v2.3.4"); err != nil {
		t.Fatalf("WriteVersion: %v", err)
	}
	got, err := os.ReadFile(filepath.Join(dir, ".version"))
	if err != nil {
		t.Fatalf("ReadFile .version: %v", err)
	}
	if string(got) != "2.3.4" {
		t.Errorf(".version = %q, want %q", got, "2.3.4")
	}
}

func TestWriteVersion_NoPrefix(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("DECEPTICON_HOME", dir)

	if err := WriteVersion("1.0.0"); err != nil {
		t.Fatalf("WriteVersion: %v", err)
	}
	got, err := os.ReadFile(filepath.Join(dir, ".version"))
	if err != nil {
		t.Fatalf("ReadFile .version: %v", err)
	}
	if string(got) != "1.0.0" {
		t.Errorf(".version = %q, want %q", got, "1.0.0")
	}
}

// ---- ApplyUpdate ----

func TestApplyUpdate_SelfUpdateErrorPropagates(t *testing.T) {
	// SyncConfigFiles and compose.Pull failures are downgraded to warnings;
	// only SelfUpdate failure is returned as an error. A release with no
	// asset for the current platform forces SelfUpdate to fail so we can
	// verify the error is propagated and labelled correctly.
	release := &Release{
		TagName: "v9.9.9",
		Assets:  []Asset{{Name: "decepticon-plan9-mips", BrowserDownloadURL: "http://localhost/plan9"}},
	}
	err := ApplyUpdate(release, "v9.9.9")
	if err == nil {
		t.Fatal("ApplyUpdate with failing SelfUpdate: expected error, got nil")
	}
	if !strings.Contains(err.Error(), "binary update") {
		t.Errorf("error %q should contain 'binary update'", err)
	}
}
