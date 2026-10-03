package cmd

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/compose"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/ui"
)

const legacySaltMarker = "DECEPTICON_LEGACY_LITELLM_SALT"

var legacyCredentialDefaults = map[string][]string{
	"LITELLM_MASTER_KEY": {"sk-decepticon-master"},
	"LITELLM_SALT_KEY":   {"sk-decepticon-salt-change-me", "sk-decepticon-salt"},
	"POSTGRES_PASSWORD":  {"decepticon"},
	"NEO4J_PASSWORD":     {"decepticon-graph"},
}

type credentialMigrationState struct {
	Updates map[string]string `json:"updates"`
}

func isLegacyCredential(env map[string]string, key string) bool {
	value := strings.TrimSpace(env[key])
	if value == "" {
		return true
	}
	for _, fallback := range legacyCredentialDefaults[key] {
		if value == fallback {
			return true
		}
	}
	return false
}

func needsLegacyCredentialMigration(env map[string]string) bool {
	for key := range legacyCredentialDefaults {
		if key == "LITELLM_SALT_KEY" && env[legacySaltMarker] == "true" && strings.TrimSpace(env[key]) != "" {
			continue
		}
		if isLegacyCredential(env, key) {
			return true
		}
	}
	return false
}

func migrateLegacyCredentials(env map[string]string) error {
	if !needsLegacyCredentialMigration(env) {
		return nil
	}

	home := config.DecepticonHome()
	statePath := filepath.Join(home, ".credential-migration.json")
	state, err := loadOrCreateCredentialState(statePath, env)
	if err != nil {
		return err
	}
	if err := backupLegacyEnv(config.EnvPath()); err != nil {
		return err
	}

	c := compose.New()
	if _, postgres := state.Updates["POSTGRES_PASSWORD"]; postgres {
		if err := c.UpDatabases(); err != nil {
			return fmt.Errorf("start databases with existing credentials: %w", err)
		}
		if err := rotatePostgresPassword(c, state.Updates["POSTGRES_PASSWORD"]); err != nil {
			return fmt.Errorf("rotate PostgreSQL password: %w", err)
		}
	} else if _, neo4j := state.Updates["NEO4J_PASSWORD"]; neo4j {
		if err := c.UpDatabases(); err != nil {
			return fmt.Errorf("start databases with existing credentials: %w", err)
		}
	}
	if password, neo4j := state.Updates["NEO4J_PASSWORD"]; neo4j {
		if err := rotateNeo4jPassword(env, password); err != nil {
			return fmt.Errorf("rotate Neo4j password: %w", err)
		}
	}
	if err := config.SetEnvKeys(config.EnvPath(), state.Updates); err != nil {
		return fmt.Errorf("save upgraded credentials: %w", err)
	}
	for key, value := range state.Updates {
		env[key] = value
	}
	if err := os.Remove(statePath); err != nil {
		ui.Warning("Could not remove completed credential migration state: " + err.Error())
	}
	ui.Success("Legacy installation credentials upgraded; existing databases and workspace preserved.")
	ui.DimText("Original configuration backed up at " + config.EnvPath() + ".before-credential-migration")
	if env[legacySaltMarker] == "true" {
		ui.Warning("LiteLLM encryption salt was retained to keep stored provider credentials readable. Re-enter stored provider keys on a new installation to rotate it.")
	}
	return nil
}

func loadOrCreateCredentialState(path string, env map[string]string) (credentialMigrationState, error) {
	if data, err := os.ReadFile(path); err == nil {
		var state credentialMigrationState
		if err := json.Unmarshal(data, &state); err != nil || len(state.Updates) == 0 {
			return credentialMigrationState{}, fmt.Errorf("invalid credential migration state at %s", path)
		}
		return state, nil
	} else if !os.IsNotExist(err) {
		return credentialMigrationState{}, err
	}
	secrets, err := config.NewInstallationSecrets()
	if err != nil {
		return credentialMigrationState{}, err
	}
	generated := map[string]string{
		"LITELLM_MASTER_KEY": secrets.LiteLLMMasterKey,
		"POSTGRES_PASSWORD":  secrets.PostgresPassword,
		"NEO4J_PASSWORD":     secrets.Neo4jPassword,
	}
	state := credentialMigrationState{Updates: map[string]string{}}
	for key, value := range generated {
		if isLegacyCredential(env, key) {
			state.Updates[key] = value
		}
	}
	if isLegacyCredential(env, "LITELLM_SALT_KEY") {
		state.Updates[legacySaltMarker] = "true"
		if strings.TrimSpace(env["LITELLM_SALT_KEY"]) == "" {
			state.Updates["LITELLM_SALT_KEY"] = "sk-decepticon-salt-change-me"
		}
	}
	data, err := json.Marshal(state)
	if err != nil {
		return credentialMigrationState{}, err
	}
	if err := writeExclusivePrivate(path, data); err != nil {
		return credentialMigrationState{}, err
	}
	return state, nil
}

func backupLegacyEnv(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	backup := path + ".before-credential-migration"
	if err := writeExclusivePrivate(backup, data); err != nil && !os.IsExist(err) {
		return fmt.Errorf("back up configuration: %w", err)
	}
	return nil
}

func writeExclusivePrivate(path string, data []byte) (retErr error) {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	defer func() {
		_ = f.Close()
		if retErr != nil {
			_ = os.Remove(path)
		}
	}()
	if _, err := f.Write(data); err != nil {
		return err
	}
	return f.Sync()
}

func rotatePostgresPassword(c *compose.Compose, password string) error {
	cmd := exec.Command(c.Runtime.Bin, "exec", "-i", compose.ContainerName("postgres"),
		"psql", "-U", "decepticon", "-d", "postgres", "-v", "ON_ERROR_STOP=1")
	cmd.Stdin = strings.NewReader("ALTER ROLE decepticon WITH PASSWORD '" + password + "';\n")
	_, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("psql failed: %w", err)
	}
	return nil
}

func rotateNeo4jPassword(env map[string]string, newPassword string) error {
	port := config.Get(env, "NEO4J_HTTP_PORT", "7474")
	portNumber, err := strconv.Atoi(port)
	if err != nil || portNumber < 1 || portNumber > 65535 {
		return fmt.Errorf("invalid NEO4J_HTTP_PORT")
	}
	endpoint := "http://127.0.0.1:" + port + "/db/system/tx/commit"
	oldPassword := config.Get(env, "NEO4J_PASSWORD", "decepticon-graph")
	client := &http.Client{Timeout: 20 * time.Second}
	if ok, err := neo4jStatement(client, endpoint, newPassword, "SHOW CURRENT USER", nil); err != nil {
		return err
	} else if ok {
		return nil
	}
	if ok, err := neo4jStatement(client, endpoint, oldPassword, "SHOW CURRENT USER", nil); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("neither existing nor pending Neo4j password authenticates; migration state retained")
	}
	parameters := map[string]string{"old": oldPassword, "new": newPassword}
	if ok, err := neo4jStatement(client, endpoint, oldPassword,
		"ALTER CURRENT USER SET PASSWORD FROM $old TO $new", parameters); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("Neo4j rejected the existing password")
	}
	if ok, err := neo4jStatement(client, endpoint, newPassword, "SHOW CURRENT USER", nil); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("Neo4j did not accept the upgraded password")
	}
	return nil
}

func neo4jStatement(client *http.Client, endpoint, password, statement string, parameters map[string]string) (bool, error) {
	body, err := json.Marshal(map[string]any{
		"statements": []any{map[string]any{"statement": statement, "parameters": parameters}},
	})
	if err != nil {
		return false, err
	}
	req, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return false, err
	}
	req.SetBasicAuth("neo4j", password)
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusUnauthorized {
		return false, nil
	}
	if resp.StatusCode != http.StatusOK {
		return false, fmt.Errorf("Neo4j returned HTTP %d", resp.StatusCode)
	}
	var result struct {
		Errors []struct {
			Code string `json:"code"`
		} `json:"errors"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&result); err != nil {
		return false, err
	}
	if len(result.Errors) > 0 {
		return false, fmt.Errorf("Neo4j returned %s", result.Errors[0].Code)
	}
	return true, nil
}
