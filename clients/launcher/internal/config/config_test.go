package config

import (
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
)

func TestParseEnvLine(t *testing.T) {
	tests := []struct {
		line    string
		wantKey string
		wantVal string
		wantOk  bool
	}{
		{"KEY=value", "KEY", "value", true},
		{"KEY=\"quoted value\"", "KEY", "quoted value", true},
		{"KEY='single quoted'", "KEY", "single quoted", true},
		{"KEY=", "KEY", "", true},
		{"# comment", "", "", false},
		{"", "", "", false},
		{"NOEQUALS", "", "", false},
		{"KEY=value with spaces", "KEY", "value with spaces", true},
	}
	for _, tt := range tests {
		key, val, ok := parseEnvLine(tt.line)
		if key != tt.wantKey || val != tt.wantVal || ok != tt.wantOk {
			t.Errorf("parseEnvLine(%q) = (%q, %q, %v), want (%q, %q, %v)",
				tt.line, key, val, ok, tt.wantKey, tt.wantVal, tt.wantOk)
		}
	}
}

func TestLoadEnv(t *testing.T) {
	dir := t.TempDir()
	envFile := filepath.Join(dir, ".env")
	content := `# Comment
ANTHROPIC_API_KEY=sk-ant-real-key
OPENAI_API_KEY=your-openai-key-here
DECEPTICON_MODEL_PROFILE=eco

# Another comment
DECEPTICON_AUTH_PRIORITY=anthropic_api
`
	if err := os.WriteFile(envFile, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	env, err := LoadEnv(envFile)
	if err != nil {
		t.Fatalf("LoadEnv() error: %v", err)
	}

	if env["ANTHROPIC_API_KEY"] != "sk-ant-real-key" {
		t.Errorf("ANTHROPIC_API_KEY = %q, want %q", env["ANTHROPIC_API_KEY"], "sk-ant-real-key")
	}
	if env["DECEPTICON_MODEL_PROFILE"] != "eco" {
		t.Errorf("DECEPTICON_MODEL_PROFILE = %q, want %q", env["DECEPTICON_MODEL_PROFILE"], "eco")
	}
	if len(env) != 4 {
		t.Errorf("len(env) = %d, want 4", len(env))
	}
}

func TestIsPlaceholder(t *testing.T) {
	if !IsPlaceholder("your-anthropic-key-here") {
		t.Error("expected placeholder for 'your-anthropic-key-here'")
	}
	if !IsPlaceholder("your-openai-key-here") {
		t.Error("expected placeholder for 'your-openai-key-here'")
	}
	if IsPlaceholder("sk-ant-api03-real-key") {
		t.Error("did not expect placeholder for real key")
	}
	if !IsPlaceholder("") {
		t.Error("expected placeholder for empty string")
	}
}

func TestValidateAPIKeys(t *testing.T) {
	// All placeholders → error
	env := map[string]string{
		"ANTHROPIC_API_KEY": "your-anthropic-key-here",
		"OPENAI_API_KEY":    "your-openai-key-here",
	}
	if err := ValidateAPIKeys(env); err == nil {
		t.Error("expected error for all-placeholder keys")
	}

	// One real, well-formed key → ok
	env["ANTHROPIC_API_KEY"] = "sk-ant-api03-realkeythatislongenough"
	if err := ValidateAPIKeys(env); err != nil {
		t.Errorf("unexpected error: %v", err)
	}

	// Empty env → error
	if err := ValidateAPIKeys(map[string]string{}); err == nil {
		t.Error("expected error for empty env")
	}
}

func TestValidateAPIKeys_RejectsBadFormat(t *testing.T) {
	tests := []struct {
		name string
		env  map[string]string
	}{
		{"missing prefix", map[string]string{"ANTHROPIC_API_KEY": "no-prefix-key-of-decent-length"}},
		{"too short", map[string]string{"OPENAI_API_KEY": "sk-short"}},
		{"google missing prefix", map[string]string{"GEMINI_API_KEY": "sk-wrongprefix-key-long-enough-here"}},
		{"openrouter missing prefix", map[string]string{"OPENROUTER_API_KEY": "sk-wrongprefix-key-long-enough-here"}},
		{"nvidia missing prefix", map[string]string{"NVIDIA_API_KEY": "sk-wrongprefix-key-long-enough-here"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if err := ValidateAPIKeys(tt.env); err == nil {
				t.Errorf("expected error for %s", tt.name)
			}
		})
	}
}

// TestValidateAPIKeys_AcceptsAllProviders covers issue #105: keys for
// providers added past the original four (Anthropic/OpenAI/Gemini/MiniMax)
// were silently dropped because APIKeyNames + keyFormatRules only knew
// about the original four. Each provider must now satisfy the gate on
// its own.
func TestValidateAPIKeys_AcceptsAllProviders(t *testing.T) {
	tests := []struct {
		name string
		env  map[string]string
	}{
		{"openrouter", map[string]string{"OPENROUTER_API_KEY": "sk-or-realkeythatislongenough"}},
		{"nvidia", map[string]string{"NVIDIA_API_KEY": "nvapi-realkeythatislongenough"}},
		{"deepseek", map[string]string{"DEEPSEEK_API_KEY": "sk-realkeythatislongenough"}},
		{"xai", map[string]string{"XAI_API_KEY": "xai-realkeythatislongenough"}},
		{"mistral_no_prefix", map[string]string{"MISTRAL_API_KEY": "any-shape-key-of-sufficient-length"}},
		{"minimax_no_prefix", map[string]string{"MINIMAX_API_KEY": "eyJ-shaped-or-not-just-long-enough"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if err := ValidateAPIKeys(tt.env); err != nil {
				t.Errorf("expected %s to pass validation, got: %v", tt.name, err)
			}
		})
	}
}

// TestValidateAuth_OAuthSubscriptions verifies each custom subscription handler
// accepts any of its supported credential surfaces — env token or tokens.json.
func TestValidateAuth_OAuthSubscriptions(t *testing.T) {
	cases := []struct {
		toggle    string
		envName   string
		configDir string
		fileFmt   string
	}{
		{"DECEPTICON_AUTH_GEMINI", "GEMINI_SESSION_COOKIES", "gemini", "tokens.json"},
		{"DECEPTICON_AUTH_COPILOT", "COPILOT_REFRESH_TOKEN", "copilot", "tokens.json"},
		{"DECEPTICON_AUTH_GROK", "GROK_SESSION_TOKEN", "grok", "tokens.json"},
		{"DECEPTICON_AUTH_PERPLEXITY", "PERPLEXITY_SESSION_TOKEN", "perplexity", "tokens.json"},
	}
	for _, c := range cases {
		t.Run(c.envName+" via env", func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("HOME", home)
			t.Setenv("USERPROFILE", home) // Windows: os.UserHomeDir reads USERPROFILE
			env := map[string]string{c.toggle: "true", c.envName: "anything-not-empty"}
			if err := ValidateAuth(env); err != nil {
				t.Errorf("expected env-token to satisfy %s: %v", c.toggle, err)
			}
		})
		t.Run(c.envName+" via tokens.json", func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("HOME", home)
			t.Setenv("USERPROFILE", home) // Windows: os.UserHomeDir reads USERPROFILE
			dir := filepath.Join(home, ".config", c.configDir)
			if err := os.MkdirAll(dir, 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(dir, c.fileFmt), []byte("{}"), 0o600); err != nil {
				t.Fatal(err)
			}
			env := map[string]string{c.toggle: "true"}
			if err := ValidateAuth(env); err != nil {
				t.Errorf("expected tokens.json to satisfy %s: %v", c.toggle, err)
			}
		})
		t.Run(c.envName+" toggle on but no creds fails", func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("HOME", home)
			t.Setenv("USERPROFILE", home) // Windows: os.UserHomeDir reads USERPROFILE
			env := map[string]string{c.toggle: "true"}
			err := ValidateAuth(env)
			if err == nil {
				t.Errorf("expected %s with no creds to fail", c.toggle)
			}
		})
	}
}

func TestValidateAuth_ChatGPTNativeOAuth(t *testing.T) {
	t.Run("toggle on allows native device login without session cookie", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home) // Windows: os.UserHomeDir reads USERPROFILE
		env := map[string]string{"DECEPTICON_AUTH_CHATGPT": "true"}
		if err := ValidateAuth(env); err != nil {
			t.Errorf("expected native ChatGPT OAuth to pass without launcher-side token input: %v", err)
		}
	})

	t.Run("uses codex auth.json path", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home) // Windows: os.UserHomeDir reads USERPROFILE
		got := subscriptionTokenPaths(map[string]string{}, home, oauthSubscriptions["chatgpt"])
		want := []string{filepath.Join(home, ".codex", "auth.json")}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("unexpected ChatGPT token paths: got %v want %v", got, want)
		}
	})
}

// TestValidateAuth_OllamaLocal covers issue #106: the user picks
// ollama_local and sets OLLAMA_API_BASE, no API key needed. The
// previous gate rejected this because it only checked API-key columns.
func TestValidateAuth_OllamaLocal(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home) // Windows: os.UserHomeDir reads USERPROFILE

	t.Run("priority+base passes", func(t *testing.T) {
		env := map[string]string{
			"DECEPTICON_AUTH_PRIORITY": "ollama_local",
			"OLLAMA_API_BASE":          "http://host.docker.internal:11434",
		}
		if err := ValidateAuth(env); err != nil {
			t.Errorf("expected ollama_local to pass without API key: %v", err)
		}
	})

	t.Run("priority alone without base fails with helpful message", func(t *testing.T) {
		env := map[string]string{"DECEPTICON_AUTH_PRIORITY": "ollama_local"}
		err := ValidateAuth(env)
		if err == nil {
			t.Fatal("expected error when ollama_local is selected but base url is missing")
		}
		if !strings.Contains(err.Error(), "OLLAMA_API_BASE") {
			t.Errorf("expected error mentioning OLLAMA_API_BASE, got: %v", err)
		}
	})

	t.Run("base url alone (no priority entry) is enough", func(t *testing.T) {
		// User edits .env directly with just OLLAMA_API_BASE — accept it
		// as an opt-in signal.
		env := map[string]string{"OLLAMA_API_BASE": "http://localhost:11434"}
		if err := ValidateAuth(env); err != nil {
			t.Errorf("expected bare OLLAMA_API_BASE to satisfy auth: %v", err)
		}
	})
}

func TestValidateAuth_OAuth(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home) // Windows: os.UserHomeDir reads USERPROFILE
	// OAuth requested, no API keys configured.
	env := map[string]string{"DECEPTICON_AUTH_CLAUDE_CODE": "true"}

	// OAuth path without credentials file → error
	if err := ValidateAuth(env); err == nil {
		t.Error("expected error when ~/.claude/.credentials.json is missing")
	}
	env["ANTHROPIC_OAUTH_TOKEN"] = "sk-ant-oat01-test-token-of-sufficient-length"
	if err := ValidateAuth(env); err != nil {
		t.Fatalf("setup-token should satisfy Claude OAuth: %v", err)
	}
	delete(env, "ANTHROPIC_OAUTH_TOKEN")

	credDir := filepath.Join(home, ".claude")
	if err := os.MkdirAll(credDir, 0o755); err != nil {
		t.Fatal(err)
	}
	credPath := filepath.Join(credDir, ".credentials.json")

	// malformed JSON → error
	if err := os.WriteFile(credPath, []byte("not-json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ValidateAuth(env); err == nil {
		t.Error("expected error for malformed credentials JSON")
	}

	// valid JSON but no access token → error
	if err := os.WriteFile(credPath, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ValidateAuth(env); err == nil {
		t.Error("expected error when credentials JSON has no access token")
	}

	// current nested format (claudeAiOauth.accessToken) → ok
	current := `{"claudeAiOauth":{"accessToken":"sk-ant-oat01-test-token-of-sufficient-length"}}`
	if err := os.WriteFile(credPath, []byte(current), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ValidateAuth(env); err != nil {
		t.Errorf("unexpected error for current format: %v", err)
	}

	// legacy top-level accessToken → ok
	legacy := `{"accessToken":"sk-ant-oat01-legacy-token"}`
	if err := os.WriteFile(credPath, []byte(legacy), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ValidateAuth(env); err != nil {
		t.Errorf("unexpected error for legacy accessToken format: %v", err)
	}

	// legacy oauthToken → ok
	legacyOAuth := `{"oauthToken":"sk-ant-oat01-emulator-token"}`
	if err := os.WriteFile(credPath, []byte(legacyOAuth), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ValidateAuth(env); err != nil {
		t.Errorf("unexpected error for legacy oauthToken format: %v", err)
	}
}

func TestValidateAuth_OAuthFallsBackToAPIKey(t *testing.T) {
	// OAuth requested but file missing; a valid API key satisfies the
	// "at least one method works" rule.
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home) // Windows: os.UserHomeDir reads USERPROFILE
	env := map[string]string{
		"DECEPTICON_AUTH_CLAUDE_CODE": "true",
		"ANTHROPIC_API_KEY":           "sk-ant-api03-realkeythatislongenough",
	}
	if err := ValidateAuth(env); err != nil {
		t.Errorf("expected fallback to API key when OAuth file missing: %v", err)
	}
}

func TestValidateAuth_NeitherConfigured(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home) // Windows: os.UserHomeDir reads USERPROFILE
	// No OAuth, no real API keys.
	env := map[string]string{
		"ANTHROPIC_API_KEY": "your-anthropic-key-here",
	}
	if err := ValidateAuth(env); err == nil {
		t.Error("expected error when neither OAuth nor any API key is configured")
	}
}

func TestWriteEnv(t *testing.T) {
	dir := t.TempDir()
	tmplPath := filepath.Join(dir, ".env.example")
	outPath := filepath.Join(dir, "out", ".env")

	template := `# Config
ANTHROPIC_API_KEY=your-anthropic-key-here
OPENAI_API_KEY=your-openai-key-here
DECEPTICON_MODEL_PROFILE=eco
`
	if err := os.WriteFile(tmplPath, []byte(template), 0o644); err != nil {
		t.Fatal(err)
	}

	values := map[string]string{
		"ANTHROPIC_API_KEY":        "sk-real-key",
		"DECEPTICON_MODEL_PROFILE": "max",
	}

	if err := WriteEnv(tmplPath, outPath, values); err != nil {
		t.Fatalf("WriteEnv() error: %v", err)
	}

	env, err := LoadEnv(outPath)
	if err != nil {
		t.Fatalf("LoadEnv() error: %v", err)
	}

	if env["ANTHROPIC_API_KEY"] != "sk-real-key" {
		t.Errorf("ANTHROPIC_API_KEY = %q, want %q", env["ANTHROPIC_API_KEY"], "sk-real-key")
	}
	if env["OPENAI_API_KEY"] != "your-openai-key-here" {
		t.Errorf("OPENAI_API_KEY should stay as template value")
	}
	if env["DECEPTICON_MODEL_PROFILE"] != "max" {
		t.Errorf("DECEPTICON_MODEL_PROFILE = %q, want %q", env["DECEPTICON_MODEL_PROFILE"], "max")
	}
}

func TestTelemetryConsentWritesEnv(t *testing.T) {
	// The onboard wizard writes the telemetry consent choice via the embedded
	// env.example. Verify the key is in the template and the choice lands.
	out := filepath.Join(t.TempDir(), ".env")
	if err := WriteEnvFromEmbed(out, map[string]string{"DECEPTICON_TELEMETRY": "research"}); err != nil {
		t.Fatalf("WriteEnvFromEmbed() error: %v", err)
	}
	env, err := LoadEnv(out)
	if err != nil {
		t.Fatalf("LoadEnv() error: %v", err)
	}
	if env["DECEPTICON_TELEMETRY"] != "research" {
		t.Errorf("DECEPTICON_TELEMETRY = %q, want %q", env["DECEPTICON_TELEMETRY"], "research")
	}
	if _, ok := env["DECEPTICON_TELEMETRY_ENDPOINT"]; !ok {
		t.Error("DECEPTICON_TELEMETRY_ENDPOINT missing from embedded template")
	}
}

func TestWriteEnvFromEmbedReplacesPermissiveFilePrivately(t *testing.T) {
	out := filepath.Join(t.TempDir(), ".env")
	// Given an existing configuration created with unsafe legacy permissions.
	if err := os.WriteFile(out, []byte("POSTGRES_PASSWORD=old\n"), 0o644); err != nil {
		t.Fatalf("seed .env: %v", err)
	}

	// When onboarding replaces it with generated credentials.
	if err := WriteEnvFromEmbed(out, map[string]string{"POSTGRES_PASSWORD": "generated-secret"}); err != nil {
		t.Fatalf("WriteEnvFromEmbed() error: %v", err)
	}

	// Then the replacement is private even though the old inode was not.
	info, err := os.Stat(out)
	if err != nil {
		t.Fatalf("stat .env: %v", err)
	}
	if got := info.Mode().Perm(); runtime.GOOS != "windows" && got != 0o600 {
		t.Fatalf(".env permissions = %04o, want 0600", got)
	}
}

func TestBackfillEnvFromEmbed(t *testing.T) {
	out := filepath.Join(t.TempDir(), ".env")
	// Simulate a pre-#706 .env: has telemetry mode but NOT the endpoint,
	// plus a real user value that must be preserved untouched.
	seed := "DECEPTICON_TELEMETRY=off\nANTHROPIC_API_KEY=sk-real-user-key\n"
	if err := os.WriteFile(out, []byte(seed), 0o600); err != nil {
		t.Fatalf("seed .env: %v", err)
	}

	added, err := BackfillEnvFromEmbed(out)
	if err != nil {
		t.Fatalf("BackfillEnvFromEmbed() error: %v", err)
	}
	if !strings.Contains(strings.Join(added, ","), "DECEPTICON_TELEMETRY_ENDPOINT") {
		t.Errorf("expected DECEPTICON_TELEMETRY_ENDPOINT in added keys, got %v", added)
	}

	env, err := LoadEnv(out)
	if err != nil {
		t.Fatalf("LoadEnv() error: %v", err)
	}
	// New key backfilled from the template default.
	if env["DECEPTICON_TELEMETRY_ENDPOINT"] == "" {
		t.Error("DECEPTICON_TELEMETRY_ENDPOINT not backfilled")
	}
	// Existing values preserved exactly (never overwritten).
	if env["DECEPTICON_TELEMETRY"] != "off" {
		t.Errorf("DECEPTICON_TELEMETRY = %q, want preserved %q", env["DECEPTICON_TELEMETRY"], "off")
	}
	if env["ANTHROPIC_API_KEY"] != "sk-real-user-key" {
		t.Errorf("ANTHROPIC_API_KEY = %q, want preserved real key", env["ANTHROPIC_API_KEY"])
	}
	// .bak snapshot of the pre-migration file.
	if _, err := os.Stat(out + ".bak"); err != nil {
		t.Errorf("expected .env.bak backup: %v", err)
	}

	// Idempotent: a second run adds nothing.
	added2, err := BackfillEnvFromEmbed(out)
	if err != nil {
		t.Fatalf("second BackfillEnvFromEmbed() error: %v", err)
	}
	if len(added2) != 0 {
		t.Errorf("second backfill should be a no-op, added %v", added2)
	}
}

func TestSetEnvKey(t *testing.T) {
	out := filepath.Join(t.TempDir(), ".env")
	if err := os.WriteFile(out, []byte("DECEPTICON_TELEMETRY=off\n# a comment\nFOO=bar\n"), 0o600); err != nil {
		t.Fatalf("seed: %v", err)
	}
	// Replace an existing key.
	if err := SetEnvKey(out, "DECEPTICON_TELEMETRY", "research"); err != nil {
		t.Fatalf("SetEnvKey replace: %v", err)
	}
	// Append a missing key.
	if err := SetEnvKey(out, "NEW_KEY", "val"); err != nil {
		t.Fatalf("SetEnvKey append: %v", err)
	}
	env, err := LoadEnv(out)
	if err != nil {
		t.Fatalf("LoadEnv: %v", err)
	}
	if env["DECEPTICON_TELEMETRY"] != "research" {
		t.Errorf("DECEPTICON_TELEMETRY = %q, want research", env["DECEPTICON_TELEMETRY"])
	}
	if env["NEW_KEY"] != "val" {
		t.Errorf("NEW_KEY = %q, want val", env["NEW_KEY"])
	}
	if env["FOO"] != "bar" {
		t.Errorf("FOO = %q, want preserved bar", env["FOO"])
	}
}

func TestSetEnvKeysReplacesCredentialsPrivatelyWithoutLosingOtherSettings(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".env")
	seed := "POSTGRES_PASSWORD=decepticon\nNEO4J_PASSWORD=decepticon-graph\n# keep comment\nDECEPTICON_AUTH_PRIORITY=openai_oauth\n"
	if err := os.WriteFile(path, []byte(seed), 0o644); err != nil {
		t.Fatal(err)
	}
	updates := map[string]string{"POSTGRES_PASSWORD": "new-postgres", "NEO4J_PASSWORD": "new-neo4j", "LITELLM_MASTER_KEY": "sk-new-master"}
	if err := SetEnvKeys(path, updates); err != nil {
		t.Fatal(err)
	}
	env, err := LoadEnv(path)
	if err != nil {
		t.Fatal(err)
	}
	for key, value := range updates {
		if env[key] != value {
			t.Fatalf("%s not updated", key)
		}
	}
	if env["DECEPTICON_AUTH_PRIORITY"] != "openai_oauth" {
		t.Fatal("existing auth method was lost")
	}
	data, err := os.ReadFile(path)
	if err != nil || !strings.Contains(string(data), "# keep comment") {
		t.Fatal("existing comments were lost")
	}
	info, err := os.Stat(path)
	if err != nil || runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatal("credential file is not private")
	}
}

func TestWriteEnv_CommentedOutLines(t *testing.T) {
	dir := t.TempDir()
	tmplPath := filepath.Join(dir, ".env.example")
	outPath := filepath.Join(dir, "out", ".env")

	// Template with commented-out AWS Bedrock vars (the bug in #674)
	template := `# Config
ANTHROPIC_API_KEY=your-anthropic-key-here
# --- AWS Bedrock ---
# AWS_ACCESS_KEY_ID=AKIA...
# AWS_SECRET_ACCESS_KEY=...
# AWS_REGION=us-east-1
`
	if err := os.WriteFile(tmplPath, []byte(template), 0o644); err != nil {
		t.Fatal(err)
	}

	values := map[string]string{
		"AWS_ACCESS_KEY_ID":     "AKIAIOSFODNN7EXAMPLE",
		"AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
		"AWS_REGION":            "us-west-2",
		"AWS_REGION_NAME":       "us-west-2", // not in template at all
	}

	if err := WriteEnv(tmplPath, outPath, values); err != nil {
		t.Fatalf("WriteEnv() error: %v", err)
	}

	env, err := LoadEnv(outPath)
	if err != nil {
		t.Fatalf("LoadEnv() error: %v", err)
	}

	// Commented-out lines should be uncommented and rewritten
	if env["AWS_ACCESS_KEY_ID"] != "AKIAIOSFODNN7EXAMPLE" {
		t.Errorf("AWS_ACCESS_KEY_ID = %q, want AKIAIOSFODNN7EXAMPLE", env["AWS_ACCESS_KEY_ID"])
	}
	if env["AWS_SECRET_ACCESS_KEY"] != "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" {
		t.Errorf("AWS_SECRET_ACCESS_KEY not written")
	}
	if env["AWS_REGION"] != "us-west-2" {
		t.Errorf("AWS_REGION = %q, want us-west-2", env["AWS_REGION"])
	}
	// AWS_REGION_NAME wasn't in the template — should be appended
	if env["AWS_REGION_NAME"] != "us-west-2" {
		t.Errorf("AWS_REGION_NAME = %q, want us-west-2 (should be appended)", env["AWS_REGION_NAME"])
	}
}

func TestDecepticonHome(t *testing.T) {
	// With DECEPTICON_HOME set
	t.Setenv("DECEPTICON_HOME", "/custom/path")
	if got := DecepticonHome(); got != "/custom/path" {
		t.Errorf("DecepticonHome() = %q, want /custom/path", got)
	}

	// Without DECEPTICON_HOME — falls back to ~/.decepticon
	t.Setenv("DECEPTICON_HOME", "")
	home := DecepticonHome()
	if !filepath.IsAbs(home) {
		t.Errorf("DecepticonHome() = %q, want absolute path", home)
	}
}

func TestGet(t *testing.T) {
	env := map[string]string{"KEY": "val"}
	if Get(env, "KEY", "default") != "val" {
		t.Error("expected val")
	}
	if Get(env, "MISSING", "default") != "default" {
		t.Error("expected default")
	}
}

func TestMigrateActiveComposeProfiles_RewritesActiveLine(t *testing.T) {
	dir := t.TempDir()
	envFile := filepath.Join(dir, ".env")
	content := `# Header
ANTHROPIC_API_KEY=sk-ant-key

# Default C2 server profile.
COMPOSE_PROFILES=c2-sliver

# Trailing comment
`
	if err := os.WriteFile(envFile, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	rewrote, err := MigrateActiveComposeProfiles(envFile)
	if err != nil {
		t.Fatalf("MigrateActiveComposeProfiles error: %v", err)
	}
	if !rewrote {
		t.Fatalf("expected rewrote=true")
	}

	out, err := os.ReadFile(envFile)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(out), "\nCOMPOSE_PROFILES=c2-sliver\n") {
		t.Errorf("active COMPOSE_PROFILES line survived migration:\n%s", out)
	}
	if !strings.Contains(string(out), "[v1.1.8 ADR-0006 migration]") {
		t.Errorf("expected migration comment, got:\n%s", out)
	}
	// Original value preserved inline for restorability.
	if !strings.Contains(string(out), "was: COMPOSE_PROFILES=c2-sliver") {
		t.Errorf("expected 'was: COMPOSE_PROFILES=c2-sliver' marker in:\n%s", out)
	}
	// Unrelated lines untouched.
	if !strings.Contains(string(out), "ANTHROPIC_API_KEY=sk-ant-key") {
		t.Errorf("unrelated lines lost; got:\n%s", out)
	}

	// Backup written with the original (pre-migration) content.
	bak, err := os.ReadFile(envFile + ".bak")
	if err != nil {
		t.Fatalf("backup not written: %v", err)
	}
	if string(bak) != content {
		t.Errorf("backup mismatch:\n got:%s\nwant:%s", bak, content)
	}
}

func TestMigrateActiveComposeProfiles_LeavesCommentedLineAlone(t *testing.T) {
	dir := t.TempDir()
	envFile := filepath.Join(dir, ".env")
	content := `# COMPOSE_PROFILES=c2-sliver
ANTHROPIC_API_KEY=sk-ant-key
`
	if err := os.WriteFile(envFile, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	rewrote, err := MigrateActiveComposeProfiles(envFile)
	if err != nil {
		t.Fatalf("error: %v", err)
	}
	if rewrote {
		t.Errorf("expected rewrote=false on already-commented file")
	}
	// File unchanged.
	out, _ := os.ReadFile(envFile)
	if string(out) != content {
		t.Errorf("file modified despite no active line:\n got:%s\nwant:%s", out, content)
	}
	// No backup written.
	if _, err := os.Stat(envFile + ".bak"); !os.IsNotExist(err) {
		t.Errorf("backup created on no-op migration")
	}
}

func TestMigrateActiveComposeProfiles_Idempotent(t *testing.T) {
	dir := t.TempDir()
	envFile := filepath.Join(dir, ".env")
	content := `COMPOSE_PROFILES=c2-sliver,ad
ANTHROPIC_API_KEY=sk-ant-key
`
	if err := os.WriteFile(envFile, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	if rewrote, err := MigrateActiveComposeProfiles(envFile); err != nil || !rewrote {
		t.Fatalf("first migration: rewrote=%v err=%v; want (true, nil)", rewrote, err)
	}
	first, _ := os.ReadFile(envFile)

	if rewrote, err := MigrateActiveComposeProfiles(envFile); err != nil || rewrote {
		t.Fatalf("second migration: rewrote=%v err=%v; want (false, nil)", rewrote, err)
	}
	second, _ := os.ReadFile(envFile)
	if string(first) != string(second) {
		t.Errorf("file changed across idempotent migrations:\n first:%s\nsecond:%s", first, second)
	}
}

func TestMigrateActiveComposeProfiles_NoEnvFile(t *testing.T) {
	dir := t.TempDir()
	envFile := filepath.Join(dir, ".env")

	rewrote, err := MigrateActiveComposeProfiles(envFile)
	if err != nil {
		t.Fatalf("missing .env should not error: %v", err)
	}
	if rewrote {
		t.Errorf("rewrote=true on missing file")
	}
}

func TestMigrateActiveComposeProfiles_BackupNotOverwritten(t *testing.T) {
	dir := t.TempDir()
	envFile := filepath.Join(dir, ".env")
	backupPath := envFile + ".bak"

	original := "COMPOSE_PROFILES=c2-sliver\n"
	if err := os.WriteFile(envFile, []byte(original), 0o644); err != nil {
		t.Fatal(err)
	}
	// First migration creates the backup.
	if _, err := MigrateActiveComposeProfiles(envFile); err != nil {
		t.Fatal(err)
	}
	firstBackup, _ := os.ReadFile(backupPath)

	// Operator re-enables the active line and runs migration again.
	if err := os.WriteFile(envFile, []byte("COMPOSE_PROFILES=ad\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := MigrateActiveComposeProfiles(envFile); err != nil {
		t.Fatal(err)
	}
	secondBackup, _ := os.ReadFile(backupPath)
	// Backup must still match the very first pre-migration snapshot.
	if string(firstBackup) != string(secondBackup) {
		t.Errorf("backup was overwritten on second migration:\n first:%s\nsecond:%s", firstBackup, secondBackup)
	}
}
