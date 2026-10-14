package cmd

import (
	"maps"
	"reflect"
	"testing"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/updater"
)

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

func withUpdateStub(t *testing.T) *int {
	t.Helper()
	promptCount := 0
	prevPrompt := promptUpdateFn
	promptUpdateFn = func(string, updater.Channel) (bool, error) { promptCount++; return false, nil }
	t.Cleanup(func() { promptUpdateFn = prevPrompt })
	return &promptCount
}

func TestCheckStartupUpdate_RoutingMatrix(t *testing.T) {
	cases := []struct {
		envValue    string
		wantPrompt  int
		description string
	}{
		// Given default or legacy truthy settings, require approval.
		{"", 1, "unset → interactive prompt"},
		{"  ", 1, "whitespace-only → interactive prompt"},
		{"true", 1, "true → interactive prompt"},
		{"1", 1, "1 → interactive prompt"},
		{"yes", 1, "yes → interactive prompt"},
		{"on", 1, "on → interactive prompt"},
		{"TRUE", 1, "case-insensitive: TRUE → interactive prompt"},
		{"prompt", 1, "prompt → interactive prompt"},
		{"ask", 1, "ask → interactive prompt"},
		{"interactive", 1, "interactive → interactive prompt"},
		{"PROMPT", 1, "case-insensitive: PROMPT → interactive prompt"},
		// Air-gapped / version-pinned: skip entirely.
		{"false", 0, "false → skip"},
		{"0", 0, "0 → skip"},
		{"no", 0, "no → skip"},
		{"off", 0, "off → skip"},
		// Unrecognized / garbage values default to prompt, not silent auto-update
		{"disabled", 1, "unrecognized value disabled → interactive prompt"},
		{"skip", 1, "unrecognized value skip → interactive prompt"},
		{"garbage", 1, "unrecognized value garbage → interactive prompt"},
	}
	for _, tc := range cases {
		t.Run(tc.description, func(t *testing.T) {
			prompt := withUpdateStub(t)
			// When startup checks for an update.
			checkStartupUpdate(map[string]string{"AUTO_UPDATE": tc.envValue}, "v0.0.0-test", false)
			// Then only the configured approval/disabled route may run.
			if *prompt != tc.wantPrompt {
				t.Errorf("AUTO_UPDATE=%q: got prompt=%d, want prompt=%d",
					tc.envValue, *prompt, tc.wantPrompt)
			}
		})
	}
}

func TestCheckStartupUpdate_NoUpdateFlagAlwaysWins(t *testing.T) {
	envValues := []string{"", "true", "1", "yes", "on", "prompt", "ask", "interactive"}
	for _, ev := range envValues {
		t.Run("AUTO_UPDATE="+ev, func(t *testing.T) {
			prompt := withUpdateStub(t)
			checkStartupUpdate(map[string]string{"AUTO_UPDATE": ev}, "v0.0.0-test", true)
			if *prompt != 0 {
				t.Errorf("--no-update should short-circuit: AUTO_UPDATE=%q got prompt=%d, want 0",
					ev, *prompt)
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
