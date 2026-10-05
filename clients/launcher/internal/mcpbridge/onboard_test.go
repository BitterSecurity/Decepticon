package mcpbridge

import (
	"os"
	"strings"
	"testing"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
)

func TestConfigureHeadlessWritesPrivateConfigAndPreservesServiceSecrets(t *testing.T) {
	t.Setenv("DECEPTICON_HOME", t.TempDir())
	input := onboardInput{Settings: map[string]string{
		"DECEPTICON_AUTH_PRIORITY": "openai_api",
		"OPENAI_API_KEY":           "sk-123456789012345678901234",
		"DECEPTICON_TELEMETRY":     "off",
	}}
	output, err := configureHeadless(input)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(output, input.Settings["OPENAI_API_KEY"]) {
		t.Fatal("onboarding exposed an API key in tool output")
	}
	before, err := config.LoadEnv(config.EnvPath())
	if err != nil {
		t.Fatal(err)
	}
	if before["LITELLM_MASTER_KEY"] == "sk-decepticon-master" || before["POSTGRES_PASSWORD"] == "decepticon" {
		t.Fatal("onboarding wrote default service credentials")
	}
	info, err := os.Stat(config.EnvPath())
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("env file mode = %v", info.Mode())
	}
	input.Reset = true
	input.Settings["DECEPTICON_TELEMETRY"] = "research"
	if _, err := configureHeadless(input); err != nil {
		t.Fatal(err)
	}
	after, err := config.LoadEnv(config.EnvPath())
	if err != nil {
		t.Fatal(err)
	}
	for key := range serviceSecrets {
		if before[key] != after[key] {
			t.Fatalf("reset changed %s", key)
		}
	}
}

func TestConfigureHeadlessRejectsInvalidSettingsWithoutWriting(t *testing.T) {
	t.Setenv("DECEPTICON_HOME", t.TempDir())
	for _, settings := range []map[string]string{
		{"POSTGRES_PASSWORD": "injected"},
		{"OPENAI_API_KEY": "sk-valid\nDOCKER_HOST=evil"},
		{"UNSUPPORTED_SETTING": "value"},
		{"DECEPTICON_AUTH_PRIORITY": "openai_api", "DECEPTICON_TELEMETRY": "off"},
	} {
		if _, err := configureHeadless(onboardInput{Settings: settings}); err == nil {
			t.Fatalf("accepted invalid settings %v", settings)
		}
		if config.EnvExists() {
			t.Fatal("invalid onboarding wrote an env file")
		}
	}
}

func TestConfigureHeadlessRejectsDefaultServiceSecretsOnReset(t *testing.T) {
	t.Setenv("DECEPTICON_HOME", t.TempDir())
	if err := config.WriteEnvFromEmbed(config.EnvPath(), map[string]string{
		"DECEPTICON_AUTH_PRIORITY": "openai_api",
		"OPENAI_API_KEY":           "sk-123456789012345678901234",
		"DECEPTICON_TELEMETRY":     "off",
	}); err != nil {
		t.Fatal(err)
	}
	_, err := configureHeadless(onboardInput{Reset: true, Settings: map[string]string{"DECEPTICON_TELEMETRY": "research"}})
	if err == nil || !strings.Contains(err.Error(), "missing or default") {
		t.Fatalf("expected default credential rejection, got %v", err)
	}
}
