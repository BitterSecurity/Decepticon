package config

import (
	"bufio"
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

//go:embed env.example
var EnvTemplate string

const (
	DefaultHome       = ".decepticon"
	EnvFileName       = ".env"
	EnvExampleName    = ".env.example"
	PlaceholderSuffix = "-key-here"
)

// Config holds the Decepticon launcher configuration.
type Config struct {
	Home string
	Env  map[string]string
}

// DecepticonHome returns the resolved DECEPTICON_HOME path.
func DecepticonHome() string {
	if h := os.Getenv("DECEPTICON_HOME"); h != "" {
		return h
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return filepath.Join("~", DefaultHome)
	}
	return filepath.Join(home, DefaultHome)
}

// EnvPath returns the full path to the .env file.
func EnvPath() string {
	return filepath.Join(DecepticonHome(), EnvFileName)
}

// EnvExists checks whether .env exists.
func EnvExists() bool {
	_, err := os.Stat(EnvPath())
	return err == nil
}

// LoadEnv reads a .env file and returns key-value pairs.
// It handles comments (#), empty lines, and optional quoting.
func LoadEnv(path string) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open env file: %w", err)
	}
	defer f.Close()

	env := make(map[string]string)
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, val, ok := parseEnvLine(line)
		if !ok {
			continue
		}
		env[key] = val
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read env file: %w", err)
	}
	return env, nil
}

// parseEnvLine splits "KEY=VALUE" handling optional quotes.
func parseEnvLine(line string) (key, val string, ok bool) {
	idx := strings.IndexByte(line, '=')
	if idx < 1 {
		return "", "", false
	}
	key = strings.TrimSpace(line[:idx])
	val = strings.TrimSpace(line[idx+1:])
	// Strip surrounding quotes
	if len(val) >= 2 {
		if (val[0] == '"' && val[len(val)-1] == '"') ||
			(val[0] == '\'' && val[len(val)-1] == '\'') {
			val = val[1 : len(val)-1]
		}
	}
	return key, val, true
}

// WriteEnvFromEmbed writes key-value pairs into a .env file using the embedded template.
func WriteEnvFromEmbed(outputPath string, values map[string]string) error {
	return writeEnvFromString(EnvTemplate, outputPath, values)
}

// WriteEnv writes key-value pairs into a .env file using a template file.
func WriteEnv(templatePath, outputPath string, values map[string]string) error {
	tmpl, err := os.ReadFile(templatePath)
	if err != nil {
		return fmt.Errorf("read template: %w", err)
	}
	return writeEnvFromString(string(tmpl), outputPath, values)
}

func writeEnvFromString(tmpl string, outputPath string, values map[string]string) error {
	lines := strings.Split(tmpl, "\n")
	consumed := make(map[string]bool) // track which values were written
	var out []string
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			out = append(out, line)
			continue
		}
		// Try to match commented-out env lines: "# KEY=value" or "#KEY=value"
		if strings.HasPrefix(trimmed, "#") {
			uncommented := strings.TrimSpace(strings.TrimPrefix(trimmed, "#"))
			key, _, ok := parseEnvLine(uncommented)
			if ok {
				if newVal, exists := values[key]; exists {
					out = append(out, key+"="+newVal)
					consumed[key] = true
					continue
				}
			}
			// Plain comment or no matching value — keep as-is
			out = append(out, line)
			continue
		}
		key, _, ok := parseEnvLine(trimmed)
		if !ok {
			out = append(out, line)
			continue
		}
		if newVal, exists := values[key]; exists {
			out = append(out, key+"="+newVal)
			consumed[key] = true
		} else {
			out = append(out, line)
		}
	}
	// Append any values that didn't match a template line (e.g. AWS_REGION_NAME)
	for key, val := range values {
		if !consumed[key] {
			out = append(out, key+"="+val)
		}
	}
	if err := os.MkdirAll(filepath.Dir(outputPath), 0o755); err != nil {
		return fmt.Errorf("create directory: %w", err)
	}
	return writePrivateFile(outputPath, []byte(strings.Join(out, "\n")))
}

// writePrivateFile atomically replaces path with a file that is private from
// its first byte. os.WriteFile's mode is ignored when path already exists,
// which could leave credentials world-readable after onboard --reset.
func writePrivateFile(path string, data []byte) (retErr error) {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".env.tmp-*")
	if err != nil {
		return fmt.Errorf("create temporary env: %w", err)
	}
	tmpPath := tmp.Name()
	defer func() {
		_ = tmp.Close()
		if retErr != nil {
			_ = os.Remove(tmpPath)
		}
	}()
	if err := tmp.Chmod(0o600); err != nil {
		return fmt.Errorf("secure temporary env: %w", err)
	}
	if _, err := tmp.Write(data); err != nil {
		return fmt.Errorf("write temporary env: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close temporary env: %w", err)
	}
	if err := os.Rename(tmpPath, path); err != nil {
		return fmt.Errorf("replace env: %w", err)
	}
	return nil
}

// APIKeyNames lists the API key environment variable names to check.
// Order matters: a valid key in this list satisfies the "at least one
// credential" startup gate. Keep in sync with keyFormatRules below and
// with decepticon/llm/factory.py::_API_METHOD_ENV.
var APIKeyNames = []string{
	"ANTHROPIC_API_KEY",
	"OPENAI_API_KEY",
	"GEMINI_API_KEY",
	"MINIMAX_API_KEY",
	"OPENROUTER_API_KEY",
	"NVIDIA_API_KEY",
	"DEEPSEEK_API_KEY",
	"XAI_API_KEY",
	"MISTRAL_API_KEY",
	// Cloud gateways added in the OpenClaude provider migration.
	"GROQ_API_KEY",
	"TOGETHER_API_KEY",
	"FIREWORKS_API_KEY",
	"COHERE_API_KEY",
	"MOONSHOT_API_KEY",
	"KIMI_API_KEY",
	"ZAI_API_KEY",
	"DASHSCOPE_API_KEY",
	"GITHUB_TOKEN",
	// AWS Bedrock — IAM access key. Validation accepts the AKIA prefix.
	"AWS_ACCESS_KEY_ID",
	// Azure OpenAI deployment key.
	"AZURE_API_KEY",
	// Vertex AI uses a service-account JSON path. Treated as the
	// credential signal so an empty path doesn't pass startup gating.
	"GOOGLE_APPLICATION_CREDENTIALS",
	// Custom OpenAI-compatible endpoint key — paired with a base URL,
	// no fixed shape.
	"CUSTOM_OPENAI_API_KEY",
}

// IsPlaceholder checks if a value looks like a placeholder.
func IsPlaceholder(val string) bool {
	return strings.HasSuffix(val, PlaceholderSuffix) || val == ""
}

// keyFormatRules maps an API key env var to its expected prefix and human-readable hint.
// Format checks are intentionally lenient — providers occasionally evolve key shapes
// (OpenAI shipped sk-proj-* in 2024, Anthropic sk-ant-api03-* etc.). The check only
// rejects values that are obviously malformed (typos, missing prefix).
//
// An empty Prefix means "no fixed shape, skip the prefix check" (only the
// length floor still applies). MiniMax/Mistral keys ship without a
// universal prefix and would otherwise be rejected when the user pastes
// the right key.
var keyFormatRules = map[string]struct {
	Prefix string
	Hint   string
}{
	"ANTHROPIC_API_KEY":              {Prefix: "sk-", Hint: "Anthropic keys start with 'sk-'"},
	"OPENAI_API_KEY":                 {Prefix: "sk-", Hint: "OpenAI keys start with 'sk-'"},
	"GEMINI_API_KEY":                 {Prefix: "AIza", Hint: "Gemini keys start with 'AIza'"},
	"OPENROUTER_API_KEY":             {Prefix: "sk-or-", Hint: "OpenRouter keys start with 'sk-or-'"},
	"NVIDIA_API_KEY":                 {Prefix: "nvapi-", Hint: "NVIDIA keys start with 'nvapi-'"},
	"DEEPSEEK_API_KEY":               {Prefix: "sk-", Hint: "DeepSeek keys start with 'sk-'"},
	"XAI_API_KEY":                    {Prefix: "xai-", Hint: "xAI keys start with 'xai-'"},
	"MINIMAX_API_KEY":                {Prefix: "", Hint: ""},
	"MISTRAL_API_KEY":                {Prefix: "", Hint: ""},
	"GROQ_API_KEY":                   {Prefix: "gsk_", Hint: "Groq keys start with 'gsk_'"},
	"TOGETHER_API_KEY":               {Prefix: "", Hint: ""},
	"FIREWORKS_API_KEY":              {Prefix: "fw_", Hint: "Fireworks keys start with 'fw_'"},
	"COHERE_API_KEY":                 {Prefix: "", Hint: ""},
	"MOONSHOT_API_KEY":               {Prefix: "sk-", Hint: "Moonshot keys start with 'sk-'"},
	"KIMI_API_KEY":                   {Prefix: "sk-kimi-", Hint: "Kimi for Coding keys start with 'sk-kimi-'"},
	"ZAI_API_KEY":                    {Prefix: "", Hint: ""},
	"DASHSCOPE_API_KEY":              {Prefix: "sk-", Hint: "DashScope keys start with 'sk-'"},
	// GitHub fine-grained PATs start with github_pat_; classic PATs
	// start with ghp_. Accept either by skipping the prefix check.
	"GITHUB_TOKEN":                   {Prefix: "", Hint: ""},
	"AWS_ACCESS_KEY_ID":              {Prefix: "AKIA", Hint: "AWS access key IDs start with 'AKIA'"},
	"AZURE_API_KEY":                  {Prefix: "", Hint: ""},
	"GOOGLE_APPLICATION_CREDENTIALS": {Prefix: "", Hint: "must be an absolute path to a service-account JSON file"},

	"CUSTOM_OPENAI_API_KEY":          {Prefix: "", Hint: ""},
}

// validateKeyFormat returns an empty string if the key looks valid, or a reason if not.
func validateKeyFormat(name, val string) string {
	// File-path keys have different validation
	if name == "GOOGLE_APPLICATION_CREDENTIALS" {
		if _, err := os.Stat(val); err != nil {
			return "file not found: " + val
		}
		return ""
	}
	if len(val) < 20 {
		return "value is too short to be a valid API key"
	}
	if rule, ok := keyFormatRules[name]; ok {
		// Empty Prefix → provider has no fixed prefix, length floor is the
		// only signal. Skip the prefix check rather than rejecting valid keys.
		if rule.Prefix != "" && !strings.HasPrefix(val, rule.Prefix) {
			return rule.Hint
		}
	}
	return ""
}

// ValidateAPIKeys checks that at least one API key is set with a valid format.
// Returns a fatal error listing both unset keys and any format problems found.
func ValidateAPIKeys(env map[string]string) error {
	var validNames []string
	invalidReasons := make(map[string]string)

	for _, name := range APIKeyNames {
		val := env[name]
		if val == "" || IsPlaceholder(val) {
			continue
		}
		if reason := validateKeyFormat(name, val); reason != "" {
			invalidReasons[name] = reason
			continue
		}
		validNames = append(validNames, name)
	}

	if len(validNames) > 0 {
		return nil
	}

	var msg strings.Builder
	msg.WriteString("no valid API key found.")
	if len(invalidReasons) > 0 {
		msg.WriteString(" Detected malformed key(s):")
		for name, reason := range invalidReasons {
			msg.WriteString(fmt.Sprintf("\n  %s: %s", name, reason))
		}
	}
	msg.WriteString("\nRun 'decepticon onboard --reset' to reconfigure credentials.")
	return fmt.Errorf("%s", msg.String())
}

// subscriptionMethod groups the env signal + credential layout for one
// OAuth subscription handler so we don't repeat the validation logic.
// Resolution order matches each runtime provider exactly:
//
//  1. <PROVIDER>_ACCESS_TOKEN   — pre-extracted Bearer
//  2. <PROVIDER>_SESSION_TOKEN  — browser session cookie value
//  3. configured token file on disk
//
// Two providers diverge slightly:
//   - Gemini Advanced ships a multi-cookie value (GEMINI_SESSION_COOKIES)
//     instead of a single session token. accept either env name.
//   - Copilot Pro uses a refresh-token rotation (COPILOT_REFRESH_TOKEN)
//     instead of a session cookie. Same fall-through, different env name.
//   - ChatGPT uses Decepticon's auth/ handler reading the Codex CLI
//     credential store at ~/.codex/auth.json. The launcher mounts that
//     file into the container so a host-side `codex login` is visible
//     to the running proxy without rebuilding.
//
// AbsolutePath is set for handlers that store a single credential file
// at a fixed absolute path (rather than under ~/.config/<dir>/<file>).
// When set, ConfigDir / TokenFile are ignored.
type subscriptionMethod struct {
	Toggle                string   // DECEPTICON_AUTH_<X> boolean enabling this path
	TokenEnvs             []string // env vars that satisfy the path on their own
	ConfigDir             string   // ~/.config/<dir>/<token file> fallback
	TokenFile             string   // token file name; defaults to tokens.json
	DirEnv                string   // optional host-side token directory env var
	AbsolutePath          string   // optional fixed-relative-to-$HOME path (e.g. .codex/auth.json)
	LegacyDir             string   // optional legacy ~/.config/<dir>/<token file> fallback
	Label                 string   // human name for error messages
	AllowInteractiveLogin bool     // provider can bootstrap credentials at runtime
}

var oauthSubscriptions = map[string]subscriptionMethod{
	"chatgpt": {
		Toggle:                "DECEPTICON_AUTH_CHATGPT",
		AbsolutePath:          ".codex/auth.json",
		Label:                 "ChatGPT",
		AllowInteractiveLogin: true,
	},
	"gemini": {
		Toggle:    "DECEPTICON_AUTH_GEMINI",
		TokenEnvs: []string{"GEMINI_ACCESS_TOKEN", "GEMINI_SESSION_COOKIES"},
		ConfigDir: "gemini",
		Label:     "Gemini Advanced",
	},
	"copilot": {
		Toggle:    "DECEPTICON_AUTH_COPILOT",
		TokenEnvs: []string{"COPILOT_ACCESS_TOKEN", "COPILOT_REFRESH_TOKEN"},
		ConfigDir: "copilot",
		Label:     "Copilot Pro",
	},
	"grok": {
		Toggle:    "DECEPTICON_AUTH_GROK",
		TokenEnvs: []string{"GROK_ACCESS_TOKEN", "GROK_SESSION_TOKEN"},
		ConfigDir: "grok",
		Label:     "SuperGrok",
	},
	"perplexity": {
		Toggle:    "DECEPTICON_AUTH_PERPLEXITY",
		TokenEnvs: []string{"PERPLEXITY_ACCESS_TOKEN", "PERPLEXITY_SESSION_TOKEN"},
		ConfigDir: "perplexity",
		Label:     "Perplexity Pro",
	},
}

// ValidateAuth ensures at least one valid AuthMethod is configured.
//
// OAuth paths:
//   - DECEPTICON_AUTH_CLAUDE_CODE=true requires a parseable
//     ~/.claude/.credentials.json. LiteLLM mounts that file read-only.
//   - DECEPTICON_AUTH_<X>=true (CHATGPT, GEMINI, COPILOT, GROK,
//     PERPLEXITY) is satisfied by a token env var or a token file at its
//     mounted token directory. ChatGPT uses LiteLLM native OAuth and is
//     allowed through so LiteLLM can run its device-code login flow.
//
// Local LLM path: ollama_local in DECEPTICON_AUTH_PRIORITY (or any
// OLLAMA_API_BASE configured) is treated as a valid credential. Ollama
// reachability is probed separately at startup (start.go).
//
// API path: at least one configured provider key (ANTHROPIC / OPENAI /
// GEMINI / MINIMAX / OPENROUTER / NVIDIA / DEEPSEEK / XAI / MISTRAL)
// must be non-placeholder and well-formed.
//
// At least one path must succeed. When OAuth is requested and its
// credentials are broken, the other paths are checked as fallbacks;
// if all fail, the OAuth error is surfaced first because that was the
// user's explicit choice.
func ValidateAuth(env map[string]string) error {
	claudeOAuth := isTruthy(Get(env, "DECEPTICON_AUTH_CLAUDE_CODE", ""))
	ollamaErr := validateOllamaCredentials(env)
	apiErr := ValidateAPIKeys(env)

	if claudeOAuth {
		if err := validateClaudeCredentials(); err == nil {
			return nil
		}
	}
	for _, sub := range oauthSubscriptions {
		if !isTruthy(Get(env, sub.Toggle, "")) {
			continue
		}
		if err := validateSubscriptionCredentials(env, sub); err == nil {
			return nil
		}
	}
	if ollamaErr == nil {
		return nil
	}
	if apiErr == nil {
		return nil
	}

	// All paths failed — surface the user's explicit choice first.
	if claudeOAuth {
		return validateClaudeCredentials()
	}
	for _, key := range []string{"chatgpt", "gemini", "copilot", "grok", "perplexity"} {
		sub := oauthSubscriptions[key]
		if isTruthy(Get(env, sub.Toggle, "")) {
			return validateSubscriptionCredentials(env, sub)
		}
	}
	if hasOllamaSelected(env) {
		return ollamaErr
	}
	return apiErr
}

// hasOllamaSelected returns true when the user has explicitly opted into
// the local-Ollama auth method (via the priority list or by setting
// OLLAMA_API_BASE without any other API key).
func hasOllamaSelected(env map[string]string) bool {
	priority := strings.ToLower(strings.TrimSpace(env["DECEPTICON_AUTH_PRIORITY"]))
	for _, m := range strings.Split(priority, ",") {
		if strings.TrimSpace(m) == "ollama_local" {
			return true
		}
	}
	return env["OLLAMA_API_BASE"] != ""
}

// validateOllamaCredentials accepts the Ollama path when the user has
// either listed ollama_local in DECEPTICON_AUTH_PRIORITY or set
// OLLAMA_API_BASE directly. Ollama itself has no API key — the
// only required signal is the base URL pointing at a running instance.
//
// We don't probe the URL here; the launcher runs on the host while the
// LiteLLM container talks to the URL from inside Docker, so a host-side
// reachability check would lie when the user (correctly) wired up
// host.docker.internal-style addressing.
func validateOllamaCredentials(env map[string]string) error {
	if !hasOllamaSelected(env) {
		return fmt.Errorf("ollama not configured")
	}
	base := strings.TrimSpace(env["OLLAMA_API_BASE"])
	if base == "" {
		return fmt.Errorf(
			"ollama_local selected but OLLAMA_API_BASE is empty.\n" +
				"Set OLLAMA_API_BASE=http://host.docker.internal:11434 (or your Ollama URL) " +
				"in ~/.decepticon/.env, or run 'decepticon onboard --reset' to reconfigure.",
		)
	}
	return nil
}

func isTruthy(s string) bool {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "true", "1", "yes", "on":
		return true
	}
	return false
}

// validateClaudeCredentials verifies ~/.claude/.credentials.json exists, is a regular
// file, parses as JSON, and carries an access token in one of the shapes the LiteLLM
// claude_code_handler accepts (claudeAiOauth.accessToken, top-level accessToken, or
// legacy oauthToken). Compose mounts this path into the LiteLLM container; if it's
// missing or empty, authentication fails opaquely on the first prompt instead of here.
func validateClaudeCredentials() error {
	home, err := os.UserHomeDir()
	if err != nil {
		return fmt.Errorf("locate home directory: %w", err)
	}
	path := filepath.Join(home, ".claude", ".credentials.json")
	info, err := os.Stat(path)
	if os.IsNotExist(err) {
		return fmt.Errorf("Claude Code credentials not found at %s\nRun 'claude /login' (Claude Code CLI) to authenticate, then retry.", path)
	}
	if err != nil {
		return fmt.Errorf("stat %s: %w", path, err)
	}
	if info.IsDir() {
		return fmt.Errorf("expected credentials file at %s but found a directory.\nRemove it and run 'claude /login' to re-authenticate.", path)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read %s: %w\nCheck file permissions and re-run.", path, err)
	}
	var creds map[string]any
	if err := json.Unmarshal(raw, &creds); err != nil {
		return fmt.Errorf("credentials file at %s is not valid JSON: %w\nRun 'claude /login' to re-authenticate.", path, err)
	}
	if extractClaudeAccessToken(creds) == "" {
		return fmt.Errorf("no access token found in %s\nRun 'claude /login' to re-authenticate.", path)
	}
	return nil
}

// validateSubscriptionCredentials verifies the user has at least one credential
// path wired up for an OAuth subscription handler. The runtime providers
// walk the same resolution order at runtime:
//
//  1. <PROVIDER>_ACCESS_TOKEN env (pre-extracted Bearer)
//  2. <PROVIDER>_SESSION_TOKEN / _SESSION_COOKIES / _REFRESH_TOKEN env
//  3. configured token file on disk
//
// We don't validate token shape — providers ship them in many formats and shapes
// drift across versions. We only catch the "toggled on in onboard but never
// pasted a token" case before the first agent prompt fails opaquely.
func validateSubscriptionCredentials(env map[string]string, sub subscriptionMethod) error {
	for _, name := range sub.TokenEnvs {
		if strings.TrimSpace(env[name]) != "" {
			return nil
		}
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return fmt.Errorf("locate home directory: %w", err)
	}
	paths := subscriptionTokenPaths(env, home, sub)
	for _, path := range paths {
		if _, err := os.Stat(path); err == nil {
			return nil
		}
	}
	if sub.AllowInteractiveLogin {
		return nil
	}
	hints := strings.Join(sub.TokenEnvs, " or ")
	return fmt.Errorf(
		"%s subscription token not configured.\n"+
			"Provide one of:\n"+
			"  - %s env var\n"+
			"  - %s on disk\n"+
			"Run 'decepticon onboard --reset' to (re)configure interactively.",
		sub.Label, hints, strings.Join(paths, " or "),
	)
}

func subscriptionTokenPaths(env map[string]string, home string, sub subscriptionMethod) []string {
	var paths []string
	if sub.AbsolutePath != "" {
		// Single-file handler (e.g. ChatGPT → ~/.codex/auth.json).
		// ConfigDir / TokenFile / LegacyDir do not apply.
		paths = append(paths, filepath.Join(home, sub.AbsolutePath))
		return dedupeStrings(paths)
	}
	tokenFile := sub.TokenFile
	if tokenFile == "" {
		tokenFile = "tokens.json"
	}
	if sub.DirEnv != "" {
		if dir := strings.TrimSpace(Get(env, sub.DirEnv, os.Getenv(sub.DirEnv))); dir != "" {
			paths = append(paths, filepath.Join(dir, tokenFile))
		}
	}
	paths = append(paths, filepath.Join(home, ".config", sub.ConfigDir, tokenFile))
	if sub.LegacyDir != "" {
		paths = append(paths, filepath.Join(home, ".config", sub.LegacyDir, tokenFile))
	}
	return dedupeStrings(paths)
}

func dedupeStrings(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	out := make([]string, 0, len(values))
	for _, value := range values {
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	return out
}

// extractClaudeAccessToken walks the credentials JSON in the same resolution order as
// the LiteLLM handler (config/claude_code_handler.py): current nested format first,
// then legacy top-level keys. Returns "" if no usable token is present.
func extractClaudeAccessToken(creds map[string]any) string {
	if oauth, ok := creds["claudeAiOauth"].(map[string]any); ok {
		if tok, _ := oauth["accessToken"].(string); tok != "" {
			return tok
		}
	}
	if tok, _ := creds["accessToken"].(string); tok != "" {
		return tok
	}
	if tok, _ := creds["oauthToken"].(string); tok != "" {
		return tok
	}
	return ""
}

// AppendEnvLine appends a KEY=VALUE line to an existing .env file.
// If the key is already present, it is left unchanged.
func AppendEnvLine(path, key, value string) error {
	existing, _ := os.ReadFile(path)
	// If key already present, skip
	for _, line := range strings.Split(string(existing), "\n") {
		if k, _, ok := parseEnvLine(strings.TrimSpace(line)); ok && k == key {
			return nil // already set
		}
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = fmt.Fprintf(f, "\n%s=%s\n", key, value)
	return err
}

// BackfillEnvFromEmbed appends any ACTIVE key present in the embedded
// env.example template but missing from the user's .env, using the
// template's default value. It is the general "a new release added a
// config key, get it to already-onboarded users" primitive: those users
// never re-run the onboard wizard (cmd/start.go only runs it when .env is
// absent), so without this a key added to the template after their
// install would never reach their container (telemetry vars, for one,
// flow into langgraph purely via env_file:.env).
//
// Guarantees:
//   - Existing keys and their values are NEVER modified (only additions).
//   - Commented / optional template lines (e.g. "# LANGGRAPH_PORT=2024")
//     are ignored — only active defaults are backfilled.
//   - A one-time ".env.bak" snapshot is written before the first append.
//   - Idempotent: a second call with no new template keys is a no-op.
//
// Returns the list of keys added (nil when nothing changed).
func BackfillEnvFromEmbed(envPath string) ([]string, error) {
	data, err := os.ReadFile(envPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	present := make(map[string]bool)
	for _, line := range strings.Split(string(data), "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		if k, _, ok := parseEnvLine(trimmed); ok {
			present[k] = true
		}
	}

	var added []string
	var toAppend []string
	seen := make(map[string]bool)
	for _, line := range strings.Split(EnvTemplate, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		k, _, ok := parseEnvLine(trimmed)
		if !ok || present[k] || seen[k] {
			continue
		}
		seen[k] = true
		added = append(added, k)
		toAppend = append(toAppend, trimmed)
	}
	if len(toAppend) == 0 {
		return nil, nil
	}

	backupPath := envPath + ".bak"
	if _, statErr := os.Stat(backupPath); os.IsNotExist(statErr) {
		if err := os.WriteFile(backupPath, data, 0o600); err != nil {
			return nil, fmt.Errorf("write backup %s: %w", backupPath, err)
		}
	}

	var b strings.Builder
	b.Write(data)
	if len(data) > 0 && !strings.HasSuffix(string(data), "\n") {
		b.WriteByte('\n')
	}
	b.WriteString("\n# --- Backfilled by `decepticon start` (new keys from this release) ---\n")
	for _, line := range toAppend {
		b.WriteString(line)
		b.WriteByte('\n')
	}
	if err := os.WriteFile(envPath, []byte(b.String()), 0o600); err != nil {
		return nil, err
	}
	return added, nil
}

// SetEnvKey sets KEY=value in the .env at path, replacing the first active
// occurrence in place (preserving surrounding lines and comments) or
// appending the line when the key is absent. Unlike AppendEnvLine it
// overwrites an existing value, so it is the right primitive for a
// migration that must change a setting (e.g. flipping DECEPTICON_TELEMETRY
// after a re-consent prompt).
func SetEnvKey(path, key, value string) error {
	return SetEnvKeys(path, map[string]string{key: value})
}

// SetEnvKeys replaces several settings in one private, atomic write. A
// credential migration must never leave only some of its new passwords in
// .env when the process is interrupted.
func SetEnvKeys(path string, values map[string]string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	lines := strings.Split(string(data), "\n")
	replaced := make(map[string]bool, len(values))
	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		if k, _, ok := parseEnvLine(trimmed); ok {
			if value, found := values[k]; found {
				lines[i] = k + "=" + value
				replaced[k] = true
			}
		}
	}
	for key, value := range values {
		if !replaced[key] {
			lines = append(lines, key+"="+value)
		}
	}
	return writePrivateFile(path, []byte(strings.Join(lines, "\n")))
}

// Get returns a config value with a fallback default.
func Get(env map[string]string, key, fallback string) string {
	if val, ok := env[key]; ok && val != "" {
		return val
	}
	return fallback
}

// MigrateActiveComposeProfiles comments out any active "COMPOSE_PROFILES="
// line in the .env file.
//
// Before ADR-0006 (v1.1.7 and earlier) the OSS .env.example shipped with
// an active "COMPOSE_PROFILES=c2-sliver" line so `decepticon start`
// always brought the Sliver C2 container up. ADR-0006 routes every
// specialist workload (c2-sliver, ad, reversing, …) through the
// opscontrol daemon; the orchestrator decides when each plane comes up
// via `ops_start(...)`. A stale active COMPOSE_PROFILES from a v1.1.7-era
// .env forces those workloads to spawn on every `decepticon start`,
// defeating the dynamic-spawn design and re-introducing the idle-cost +
// attack-surface that ADR-0006 was meant to remove.
//
// Behaviour:
//   - rewrites every active "COMPOSE_PROFILES=…" line to a "# Migrated …"
//     comment, preserving the original value inline so the operator can
//     restore it by hand if they really want global activation
//   - commented-out lines are left untouched (idempotent on repeat starts)
//   - a single ".env.bak" backup is written next to the .env on the first
//     migration; subsequent migrations skip the backup if one already
//     exists, so a later edit-and-restart can't silently overwrite the
//     pre-migration snapshot
//   - returns (rewrote, error); rewrote=true is the signal for the caller
//     to surface a one-line notice to the user
func MigrateActiveComposeProfiles(envPath string) (bool, error) {
	data, err := os.ReadFile(envPath)
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, err
	}
	lines := strings.Split(string(data), "\n")
	rewritten := false
	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		if k, _, ok := parseEnvLine(trimmed); ok && k == "COMPOSE_PROFILES" {
			lines[i] = "# [v1.1.8 ADR-0006 migration] specialist workloads now spawn via ops_start; was: " + line
			rewritten = true
		}
	}
	if !rewritten {
		return false, nil
	}
	backupPath := envPath + ".bak"
	if _, statErr := os.Stat(backupPath); os.IsNotExist(statErr) {
		if err := os.WriteFile(backupPath, data, 0o600); err != nil {
			return false, fmt.Errorf("write backup %s: %w", backupPath, err)
		}
	}
	if err := os.WriteFile(envPath, []byte(strings.Join(lines, "\n")), 0o600); err != nil {
		return false, err
	}
	return true, nil
}
