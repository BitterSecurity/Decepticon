package cmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/PurpleAILAB/Decepticon/clients/launcher/internal/config"
)

func TestMigrateLegacyCredentialsPreservesConfiguredDatabasePasswords(t *testing.T) {
	home := t.TempDir()
	t.Setenv("DECEPTICON_HOME", home)
	path := filepath.Join(home, ".env")
	seed := "LITELLM_MASTER_KEY=sk-decepticon-master\nLITELLM_SALT_KEY=sk-decepticon-salt-change-me\nPOSTGRES_PASSWORD=custom-postgres\nNEO4J_PASSWORD=custom-neo4j\nDECEPTICON_AUTH_PRIORITY=openai_oauth\n"
	if err := os.WriteFile(path, []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}
	env, err := config.LoadEnv(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := migrateLegacyCredentials(env); err != nil {
		t.Fatal(err)
	}
	if err := migrateLegacyCredentials(env); err != nil {
		t.Fatalf("second start should not repeat migration: %v", err)
	}
	if env["LITELLM_MASTER_KEY"] == "sk-decepticon-master" || env[legacySaltMarker] != "true" {
		t.Fatal("master key was not rotated or legacy salt was not marked")
	}
	if env["POSTGRES_PASSWORD"] != "custom-postgres" || env["NEO4J_PASSWORD"] != "custom-neo4j" {
		t.Fatal("working database passwords were changed")
	}
	if err := checkDefaultCredentials(env); err != nil {
		t.Fatal(err)
	}
	saved, err := config.LoadEnv(path)
	if err != nil || !reflect.DeepEqual(env, saved) {
		t.Fatal("on-disk configuration differs from running configuration")
	}
}

func TestLegacyCredentialStateSurvivesInterruptedUpgrade(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".credential-migration.json")
	env := map[string]string{
		"LITELLM_MASTER_KEY": "sk-decepticon-master",
		"LITELLM_SALT_KEY":   "sk-decepticon-salt-change-me",
		"POSTGRES_PASSWORD":  "decepticon",
		"NEO4J_PASSWORD":     "decepticon-graph",
	}
	first, err := loadOrCreateCredentialState(path, env)
	if err != nil {
		t.Fatal(err)
	}
	second, err := loadOrCreateCredentialState(path, env)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(first, second) {
		t.Fatal("restart generated different passwords after a partial upgrade")
	}
	for _, key := range []string{"LITELLM_MASTER_KEY", "POSTGRES_PASSWORD", "NEO4J_PASSWORD"} {
		if first.Updates[key] == "" || first.Updates[key] == env[key] {
			t.Fatalf("%s was not rotated", key)
		}
	}
	if first.Updates[legacySaltMarker] != "true" || first.Updates["LITELLM_SALT_KEY"] != "" {
		t.Fatal("legacy salt must remain available to decrypt stored provider credentials")
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("migration state must be private: %v", err)
	}
}

func TestLegacyCredentialStateMaterializesMissingSaltFallback(t *testing.T) {
	env := map[string]string{
		"LITELLM_MASTER_KEY": "sk-custom-master",
		"POSTGRES_PASSWORD":  "custom-postgres",
		"NEO4J_PASSWORD":     "custom-neo4j",
	}
	state, err := loadOrCreateCredentialState(filepath.Join(t.TempDir(), ".credential-migration.json"), env)
	if err != nil {
		t.Fatal(err)
	}
	if state.Updates["LITELLM_SALT_KEY"] != "sk-decepticon-salt-change-me" || state.Updates[legacySaltMarker] != "true" {
		t.Fatal("missing salt must be materialized before the credential gate")
	}
}

func TestLegacySaltMarkerAllowsRestartAfterUpgrade(t *testing.T) {
	env := map[string]string{
		"LITELLM_MASTER_KEY": "sk-custom-master",
		"LITELLM_SALT_KEY":   "sk-decepticon-salt-change-me",
		"POSTGRES_PASSWORD":  "custom-postgres",
		"NEO4J_PASSWORD":     "custom-neo4j",
		legacySaltMarker:     "true",
	}
	if needsLegacyCredentialMigration(env) {
		t.Fatal("completed upgrade should not run again")
	}
	if err := checkDefaultCredentials(env); err != nil {
		t.Fatalf("migrated installation must start: %v", err)
	}
	delete(env, legacySaltMarker)
	if err := checkDefaultCredentials(env); err == nil {
		t.Fatal("unmarked public salt must still be refused")
	}
	env[legacySaltMarker] = "true"
	env["LITELLM_SALT_KEY"] = ""
	if err := checkDefaultCredentials(env); err == nil {
		t.Fatal("missing salt must never be accepted")
	}
}

func TestRotateNeo4jPasswordChecksResultAndResumes(t *testing.T) {
	oldPassword, newPassword := "decepticon-graph", "new-secret"
	currentPassword := oldPassword
	rotations := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		username, password, ok := r.BasicAuth()
		if !ok || username != "neo4j" || password != currentPassword {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		var request struct {
			Statements []struct {
				Statement  string            `json:"statement"`
				Parameters map[string]string `json:"parameters"`
			} `json:"statements"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil || len(request.Statements) != 1 {
			t.Errorf("invalid Neo4j transaction request: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		statement := request.Statements[0]
		switch statement.Statement {
		case "SHOW CURRENT USER":
		case "ALTER CURRENT USER SET PASSWORD FROM $old TO $new":
			if statement.Parameters["old"] != oldPassword || statement.Parameters["new"] != newPassword {
				t.Error("password rotation parameters were incorrect")
			}
			currentPassword = newPassword
			rotations++
		default:
			t.Errorf("unexpected Neo4j command: %s", statement.Statement)
		}
		_, _ = w.Write([]byte(`{"results":[{}],"errors":[]}`))
	}))
	defer server.Close()
	parsed, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	env := map[string]string{"NEO4J_HTTP_PORT": parsed.Port(), "NEO4J_PASSWORD": oldPassword}
	for i := 0; i < 2; i++ {
		if err := rotateNeo4jPassword(env, newPassword); err != nil {
			t.Fatalf("migration attempt %d: %v", i+1, err)
		}
	}
	if rotations != 1 {
		t.Fatalf("rotated Neo4j %d times, want once", rotations)
	}
}

func TestNeo4jStatementRejectsSuccessHTTPWithCypherError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"results":[],"errors":[{"code":"Neo.ClientError.Statement.SemanticError"}]}`))
	}))
	defer server.Close()
	_, err := neo4jStatement(server.Client(), server.URL, "password", "SHOW CURRENT USER", nil)
	if err == nil || !strings.Contains(err.Error(), "SemanticError") {
		t.Fatalf("HTTP 200 with a Cypher error must fail, got %v", err)
	}
}

func TestBackupLegacyEnvKeepsFirstCopyPrivate(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".env")
	if err := os.WriteFile(path, []byte("POSTGRES_PASSWORD=decepticon\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := backupLegacyEnv(path); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("POSTGRES_PASSWORD=custom\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := backupLegacyEnv(path); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path + ".before-credential-migration")
	if err != nil || !strings.Contains(string(data), "decepticon") {
		t.Fatal("backup was overwritten during retry")
	}
	info, err := os.Stat(path + ".before-credential-migration")
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatal("backup must be private")
	}
}
