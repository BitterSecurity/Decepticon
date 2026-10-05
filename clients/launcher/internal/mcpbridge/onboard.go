package mcpbridge

import (
	"context"
	"fmt"
	"regexp"
	"strings"
	"sync"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/migrate"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type onboardInput struct {
	Settings  map[string]string `json:"settings" jsonschema:"environment settings including authentication priority and credential; never returned by this tool"`
	Reset     bool              `json:"reset,omitempty" jsonschema:"update an existing installation without changing database credentials"`
	Confirmed bool              `json:"confirmed" jsonschema:"true only after the operator approves writing configuration"`
}

var envName = regexp.MustCompile(`^[A-Z][A-Z0-9_]*$`)

var serviceSecrets = map[string]bool{
	"LITELLM_MASTER_KEY": true, "LITELLM_SALT_KEY": true,
	"POSTGRES_PASSWORD": true, "NEO4J_PASSWORD": true,
}

var additionalOnboardKeys = map[string]bool{
	"ANTHROPIC_OAUTH_TOKEN": true, "GEMINI_SESSION_COOKIES": true,
	"GROK_SESSION_TOKEN": true, "COPILOT_REFRESH_TOKEN": true,
	"PERPLEXITY_SESSION_TOKEN": true, "OLLAMA_CLOUD_API_BASE": true,
	"OLLAMA_CLOUD_API_KEY": true, "OLLAMA_CLOUD_MODEL": true,
	"GITHUB_API_KEY": true, "KIMI_API_KEY": true,
}

func registerOnboardTool(server *mcp.Server, run CommandRunner, closedWorld bool) {
	var mu sync.Mutex
	destructive := true
	mcp.AddTool(server, &mcp.Tool{
		Name:        "decepticon_cli_onboard",
		Description: "Configure a new local installation headlessly, or reset settings while preserving existing service credentials. Supply DECEPTICON_AUTH_PRIORITY, authentication settings, and DECEPTICON_TELEMETRY explicitly. Never place secrets in prompts or responses.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive, OpenWorldHint: &closedWorld},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input onboardInput) (*mcp.CallToolResult, commandOutput, error) {
		mu.Lock()
		defer mu.Unlock()
		if !input.Confirmed {
			return nil, commandOutput{}, fmt.Errorf("onboarding requires operator confirmation")
		}
		output, err := configureHeadless(input)
		if err != nil {
			return nil, commandOutput{}, err
		}
		if _, err := run(ctx, "opscontrol", "install"); err != nil {
			output += "; opscontrol install skipped: " + err.Error()
		}
		return nil, commandOutput{Output: output}, nil
	})
}

func configureHeadless(input onboardInput) (string, error) {
	if len(input.Settings) == 0 {
		return "", fmt.Errorf("onboarding requires settings")
	}
	allowed := allowedOnboardKeys()
	for key, value := range input.Settings {
		if !allowed[key] || serviceSecrets[key] {
			return "", fmt.Errorf("unsupported onboarding setting %q", key)
		}
		if strings.ContainsAny(value, "\r\n\x00") {
			return "", fmt.Errorf("onboarding setting %q contains a line break or NUL", key)
		}
	}
	settings, err := startingSettings(input.Reset)
	if err != nil {
		return "", err
	}
	for key, value := range input.Settings {
		settings[key] = value
	}
	if settings["DECEPTICON_AUTH_PRIORITY"] == "" {
		return "", fmt.Errorf("DECEPTICON_AUTH_PRIORITY is required")
	}
	if settings["DECEPTICON_TELEMETRY"] != "off" && settings["DECEPTICON_TELEMETRY"] != "research" {
		return "", fmt.Errorf("DECEPTICON_TELEMETRY must be off or research")
	}
	if err := config.ValidateAuth(settings); err != nil {
		return "", err
	}
	if err := config.WriteEnvFromEmbed(config.EnvPath(), settings); err != nil {
		return "", err
	}
	if err := migrate.MarkAcked(config.DecepticonHome(), migrate.TelemetryPolicyID); err != nil {
		return "Configuration saved; telemetry acknowledgement could not be recorded: " + err.Error(), nil
	}
	return "Configuration saved at " + config.EnvPath(), nil
}

func startingSettings(reset bool) (map[string]string, error) {
	if config.EnvExists() {
		if !reset {
			return nil, fmt.Errorf("configuration already exists; reset requires explicit confirmation")
		}
		existing, err := config.LoadEnv(config.EnvPath())
		if err != nil {
			return nil, err
		}
		for key := range serviceSecrets {
			if legacyServiceSecret(key, existing[key]) {
				return nil, fmt.Errorf("existing %s is missing or default; back up the engagement and reinstall before resetting configuration", key)
			}
		}
		return existing, nil
	}
	secrets, err := config.NewInstallationSecrets()
	if err != nil {
		return nil, err
	}
	return map[string]string{
		"LITELLM_MASTER_KEY": secrets.LiteLLMMasterKey,
		"LITELLM_SALT_KEY":   secrets.LiteLLMSaltKey,
		"POSTGRES_PASSWORD":  secrets.PostgresPassword,
		"NEO4J_PASSWORD":     secrets.Neo4jPassword,
	}, nil
}

func allowedOnboardKeys() map[string]bool {
	keys := make(map[string]bool)
	for _, line := range strings.Split(config.EnvTemplate, "\n") {
		candidate := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(line), "#"))
		key, _, found := strings.Cut(candidate, "=")
		if found && envName.MatchString(key) {
			keys[key] = true
		}
	}
	for key := range additionalOnboardKeys {
		keys[key] = true
	}
	return keys
}

func legacyServiceSecret(key, value string) bool {
	if value == "" {
		return true
	}
	switch key {
	case "LITELLM_MASTER_KEY":
		return value == "sk-decepticon-master"
	case "LITELLM_SALT_KEY":
		return value == "sk-decepticon-salt-change-me" || value == "sk-decepticon-salt"
	case "POSTGRES_PASSWORD":
		return value == "decepticon"
	case "NEO4J_PASSWORD":
		return value == "decepticon-graph"
	}
	return false
}
