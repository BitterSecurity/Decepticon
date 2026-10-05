package cmd

import (
	"maps"
	"reflect"
	"strings"
	"testing"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/updater"
)

func TestHeadlessStartRequiresExistingConfigAndEngagement(t *testing.T) {
	t.Setenv("DECEPTICON_HOME", t.TempDir())
	previousHeadless, previousEngagement := headlessStart, startEngagement
	t.Cleanup(func() {
		headlessStart, startEngagement = previousHeadless, previousEngagement
	})
	headlessStart = true
	startEngagement = ""
	if err := runStart(nil, nil); err == nil || !strings.Contains(err.Error(), "--engagement") {
		t.Fatalf("missing engagement error = %v", err)
	}
	startEngagement = "local-test"
	if err := runStart(nil, nil); err == nil || !strings.Contains(err.Error(), "onboard") {
		t.Fatalf("missing config error = %v", err)
	}
}

// withProbeStubs swaps the WSL detection function variables for the
// duration of one test, then restores them via t.Cleanup.
func withProbeStubs(t *testing.T, isWSL bool, wslIP string) {
	t.Helper()
	prevIsWSL := isWSLFn
	prevHostIP := wslHostIPFn
	isWSLFn = func() bool { return isWSL }
	wslHostIPFn = func() string { return wslIP }
	t.Cleanup(func() {
		isWSLFn = prevIsWSL
		wslHostIPFn = prevHostIP
	})
}

func TestCandidateProbeURLs_NonDockerHostPassesThrough(t *testing.T) {
	withProbeStubs(t, false, "")
	got := candidateProbeURLs("http://10.0.0.5:11434")
	want := []string{"http://10.0.0.5:11434"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("non-docker host should pass through; got %v want %v", got, want)
	}
}

func TestCandidateProbeURLs_NativeLinuxFallsBackToLoopback(t *testing.T) {
	withProbeStubs(t, false, "")
	got := candidateProbeURLs("http://host.docker.internal:11434")
	want := []string{
		"http://host.docker.internal:11434",
		"http://127.0.0.1:11434",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("native linux candidates wrong:\n got %v\nwant %v", got, want)
	}
}

func TestCandidateProbeURLs_WSLAddsResolvedHostThenLoopback(t *testing.T) {
	withProbeStubs(t, true, "172.29.176.1")
	got := candidateProbeURLs("http://host.docker.internal:11434")
	want := []string{
		"http://host.docker.internal:11434",
		"http://172.29.176.1:11434",
		"http://127.0.0.1:11434",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("WSL candidates wrong:\n got %v\nwant %v", got, want)
	}
}

func TestCandidateProbeURLs_WSLWithoutResolvedHostStillTriesLoopback(t *testing.T) {
	withProbeStubs(t, true, "")
	got := candidateProbeURLs("http://host.docker.internal:11434")
	want := []string{
		"http://host.docker.internal:11434",
		"http://127.0.0.1:11434",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("WSL without resolv.conf IP should still fall back to loopback:\n got %v\nwant %v", got, want)
	}
}

func TestCandidateProbeURLs_DedupesWhenResolvedHostIsLoopback(t *testing.T) {
	// A WSL2 setup where /etc/resolv.conf already points at 127.0.0.1
	// (e.g. systemd-resolved local stub on the distro) should not
	// produce two identical loopback entries.
	withProbeStubs(t, true, "127.0.0.1")
	got := candidateProbeURLs("http://host.docker.internal:11434")
	want := []string{
		"http://host.docker.internal:11434",
		"http://127.0.0.1:11434",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("expected dedup of duplicate loopback candidates:\n got %v\nwant %v", got, want)
	}
}

func TestCandidateProbeURLs_HostWithoutPortRewritesCleanly(t *testing.T) {
	withProbeStubs(t, false, "")
	got := candidateProbeURLs("http://host.docker.internal")
	want := []string{
		"http://host.docker.internal",
		"http://127.0.0.1",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("port-less URL should rewrite cleanly:\n got %v\nwant %v", got, want)
	}
}

func TestCandidateProbeURLs_PreservesScheme(t *testing.T) {
	withProbeStubs(t, false, "")
	got := candidateProbeURLs("https://host.docker.internal:11434/v1")
	want := []string{
		"https://host.docker.internal:11434/v1",
		"https://127.0.0.1:11434/v1",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("scheme/path should be preserved across candidates:\n got %v\nwant %v", got, want)
	}
}

// withUpdateStubs swaps the auto-update / prompt-update function variables
// for the duration of one test, restoring them via t.Cleanup. The stubs
// record which branch ran (and how many times) without touching GitHub.
func withUpdateStubs(t *testing.T) (auto, prompt *int) {
	t.Helper()
	autoCount, promptCount := 0, 0
	prevAuto := autoUpdateFn
	prevPrompt := promptUpdateFn
	autoUpdateFn = func(string, updater.Channel) (bool, error) { autoCount++; return false, nil }
	promptUpdateFn = func(string, updater.Channel) (bool, error) { promptCount++; return false, nil }
	t.Cleanup(func() {
		autoUpdateFn = prevAuto
		promptUpdateFn = prevPrompt
	})
	return &autoCount, &promptCount
}

func TestApplyAutoUpdate_RoutingMatrix(t *testing.T) {
	cases := []struct {
		envValue    string
		wantAuto    int
		wantPrompt  int
		description string
	}{
		// Default-on: unset triggers silent auto-update. This is the
		// key behaviour change from v1.1.12 — fix-shipped → fix-applied
		// no longer requires the user to act.
		{"", 1, 0, "unset → silent auto-update"},
		{"  ", 1, 0, "whitespace-only → silent auto-update"},
		{"true", 1, 0, "true → silent auto-update (backwards-compat)"},
		{"1", 1, 0, "1 → silent auto-update (backwards-compat)"},
		{"yes", 1, 0, "yes → silent auto-update (backwards-compat)"},
		{"on", 1, 0, "on → silent auto-update (backwards-compat)"},
		{"TRUE", 1, 0, "case-insensitive: TRUE → silent auto-update"},
		// Opt-in to the pre-v1.1.12 default of prompting on a TTY.
		{"prompt", 0, 1, "prompt → interactive prompt"},
		{"ask", 0, 1, "ask → interactive prompt"},
		{"interactive", 0, 1, "interactive → interactive prompt"},
		{"PROMPT", 0, 1, "case-insensitive: PROMPT → interactive prompt"},
		// Air-gapped / version-pinned: skip entirely.
		{"false", 0, 0, "false → skip"},
		{"0", 0, 0, "0 → skip"},
		{"no", 0, 0, "no → skip"},
		{"off", 0, 0, "off → skip"},
		// Unrecognized / garbage values default to prompt, not silent auto-update
		{"disabled", 0, 1, "unrecognized value disabled → interactive prompt"},
		{"skip", 0, 1, "unrecognized value skip → interactive prompt"},
		{"garbage", 0, 1, "unrecognized value garbage → interactive prompt"},
	}
	for _, tc := range cases {
		t.Run(tc.description, func(t *testing.T) {
			auto, prompt := withUpdateStubs(t)
			applyAutoUpdate(map[string]string{"AUTO_UPDATE": tc.envValue}, "v0.0.0-test", false)
			if *auto != tc.wantAuto || *prompt != tc.wantPrompt {
				t.Errorf("AUTO_UPDATE=%q: got auto=%d prompt=%d, want auto=%d prompt=%d",
					tc.envValue, *auto, *prompt, tc.wantAuto, tc.wantPrompt)
			}
		})
	}
}

// --no-update is a one-shot override that must beat the persistent
// .env setting in every direction — including the default-on path and
// the explicit AUTO_UPDATE=true path. Without this short-circuit a CI
// runner that pinned `decepticon --no-update` would still re-exec into
// a newer binary, defeating the pin.
func TestApplyAutoUpdate_NoUpdateFlagAlwaysWins(t *testing.T) {
	envValues := []string{"", "true", "1", "yes", "on", "prompt", "ask", "interactive"}
	for _, ev := range envValues {
		t.Run("AUTO_UPDATE="+ev, func(t *testing.T) {
			auto, prompt := withUpdateStubs(t)
			applyAutoUpdate(map[string]string{"AUTO_UPDATE": ev}, "v0.0.0-test", true)
			if *auto != 0 || *prompt != 0 {
				t.Errorf("--no-update should short-circuit: AUTO_UPDATE=%q got auto=%d prompt=%d, want 0/0",
					ev, *auto, *prompt)
			}
		})
	}
}

func TestCheckDefaultCredentials_RefusesDefaults(t *testing.T) {
	defaults := map[string][]string{
		"LITELLM_MASTER_KEY": {"sk-decepticon-master"},
		"LITELLM_SALT_KEY":   {"sk-decepticon-salt-change-me", "sk-decepticon-salt"},
		"POSTGRES_PASSWORD":  {"decepticon"},
		"NEO4J_PASSWORD":     {"decepticon-graph"},
	}
	custom := map[string]string{
		"LITELLM_MASTER_KEY": "sk-9f2c1a7b3d5e",
		"LITELLM_SALT_KEY":   "sk-salt-7c6b5a4d3e2f",
		"POSTGRES_PASSWORD":  "x9k2m4p7q1",
		"NEO4J_PASSWORD":     "n8v7c6x5z4",
	}
	for name, values := range defaults {
		// Given any single public fallback, when startup validates the env, then
		// that credential independently blocks the launcher path.
		for _, value := range values {
			env := maps.Clone(custom)
			env[name] = value
			if err := checkDefaultCredentials(env); err == nil {
				t.Fatalf("default %s=%q must be refused", name, value)
			}
		}
	}
}

func TestCheckDefaultCredentials_AllowsCustom(t *testing.T) {
	err := checkDefaultCredentials(map[string]string{
		"LITELLM_MASTER_KEY": "sk-9f2c1a7b3d5e",
		"LITELLM_SALT_KEY":   "sk-salt-7c6b5a4d3e2f",
		"POSTGRES_PASSWORD":  "x9k2m4p7q1",
		"NEO4J_PASSWORD":     "n8v7c6x5z4",
	})
	if err != nil {
		t.Fatalf("custom credentials must be allowed, got: %v", err)
	}
}

func TestCheckDefaultCredentials_RefusesMissingOrEmpty(t *testing.T) {
	custom := map[string]string{
		"LITELLM_MASTER_KEY": "sk-9f2c1a7b3d5e",
		"LITELLM_SALT_KEY":   "sk-salt-7c6b5a4d3e2f",
		"POSTGRES_PASSWORD":  "x9k2m4p7q1",
		"NEO4J_PASSWORD":     "n8v7c6x5z4",
	}
	for name := range custom {
		// Given one missing credential, when startup validates the env, then
		// the Compose public fallback cannot enter the supported launcher path.
		env := maps.Clone(custom)
		delete(env, name)
		if err := checkDefaultCredentials(env); err == nil {
			t.Fatalf("missing %s must be refused", name)
		}

		env[name] = "   "
		if err := checkDefaultCredentials(env); err == nil {
			t.Fatalf("empty %s must be refused", name)
		}
	}
}
