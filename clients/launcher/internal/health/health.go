package health

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/config"
	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/ui"
)

const (
	DefaultLangGraphPort = "2024"
	DefaultLiteLLMPort   = "4000"
	DefaultNeo4jHTTPPort = "7474"
	MaxCatalogBytes      = 1 << 20
)

// Timeouts are var (not const) so tests can shrink them.
var (
	// LangGraph healthcheck (compose) only verifies /ok responds. The graph may
	// still be compiling — this functional probe waits for the decepticon
	// assistant to appear in /assistants/search.
	LangGraphTimeout = 60 * time.Second

	// Reserved for ad-hoc diagnostics commands.
	LiteLLMTimeout = 90 * time.Second
	Neo4jTimeout   = 90 * time.Second

	PollInterval = 2 * time.Second
)

// CheckLangGraph polls /assistants/search until the response references the
// decepticon assistant. Compose's healthcheck only verifies that /ok serves;
// graph compilation can still be in progress, so this functional probe is the
// only signal that the agent is actually callable.
//
// DECEPTICON_STARTUP_TIMEOUT_SECONDS overrides LangGraphTimeout for slow
// environments where graph compile is the long pole.
func CheckLangGraph(env map[string]string) error {
	return WaitForReadiness(context.Background(), func(ctx context.Context) error {
		return ProbeLangGraph(ctx, env)
	})
}

// WaitForReadiness bounds retries and requests for the selected consumer route.
func WaitForReadiness(parent context.Context, probe func(context.Context) error) error {
	timeout := LangGraphTimeout
	if v := os.Getenv("DECEPTICON_STARTUP_TIMEOUT_SECONDS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			timeout = time.Duration(n) * time.Second
		}
	}
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	var lastErr error
	for {
		err := probe(ctx)
		if err == nil {
			return nil
		}
		if ctx.Err() == nil || lastErr == nil {
			lastErr = err
		}
		timer := time.NewTimer(PollInterval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return fmt.Errorf("LangGraph readiness timed out after %s; last observation: %w; inspect the selected consumer route and `decepticon doctor --json`", timeout, lastErr)
		case <-timer.C:
		}
	}
}

func ProbeLangGraph(ctx context.Context, env map[string]string) error {
	port := config.Get(env, "LANGGRAPH_PORT", DefaultLangGraphPort)
	// Match Compose interpolation: the process environment overrides --env-file,
	// including an explicit empty value, which selects ${LANGGRAPH_PORT:-2024}.
	if processPort, set := os.LookupEnv("LANGGRAPH_PORT"); set {
		port = processPort
		if port == "" {
			port = DefaultLangGraphPort
		}
	}
	portNumber, err := strconv.Atoi(port)
	if err != nil || portNumber < 1 || portNumber > 65535 {
		return fmt.Errorf("LangGraph invalid LANGGRAPH_PORT %q: expected an integer from 1 to 65535", port)
	}
	baseURL := "http://127.0.0.1:" + strconv.Itoa(portNumber)
	client := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	for _, probe := range []struct{ method, path, body string }{
		{http.MethodGet, "/ok", ""},
		{http.MethodPost, "/assistants/search", `{"graph_id":"decepticon","limit":1}`},
	} {
		endpoint := baseURL + probe.path
		request, err := http.NewRequestWithContext(ctx, probe.method, endpoint, bytes.NewBufferString(probe.body))
		if err != nil {
			return fmt.Errorf("LangGraph invalid host endpoint %s: %w", endpoint, err)
		}
		if probe.body != "" {
			request.Header.Set("Content-Type", "application/json")
		}
		response, err := client.Do(request)
		if err != nil {
			return fmt.Errorf("LangGraph host request failed (%s %s): %w", probe.method, endpoint, err)
		}
		if response.StatusCode != http.StatusOK {
			response.Body.Close()
			return fmt.Errorf("LangGraph %s returned HTTP %d (%s)", probe.path, response.StatusCode, endpoint)
		}
		if probe.path == "/ok" {
			response.Body.Close()
			continue
		}
		body, readErr := io.ReadAll(io.LimitReader(response.Body, MaxCatalogBytes+1))
		response.Body.Close()
		if readErr != nil {
			return fmt.Errorf("LangGraph assistant catalog read failed (%s): %w", endpoint, readErr)
		}
		if len(body) > MaxCatalogBytes {
			return fmt.Errorf("LangGraph assistant catalog exceeds %d bytes (%s)", MaxCatalogBytes, endpoint)
		}
		if err := ValidateAssistantCatalog(body); err != nil {
			return fmt.Errorf("LangGraph %w (%s)", err, endpoint)
		}
		return nil
	}
	return nil
}

// ValidateAssistantCatalog is shared by host and CLI-container readiness probes.
func ValidateAssistantCatalog(body []byte) error {
	if len(body) > MaxCatalogBytes {
		return fmt.Errorf("assistant catalog exceeds %d bytes", MaxCatalogBytes)
	}
	var assistants []struct {
		GraphID string `json:"graph_id"`
	}
	if err := json.Unmarshal(body, &assistants); err != nil {
		return fmt.Errorf("invalid assistant catalog: %w", err)
	}
	for _, assistant := range assistants {
		if assistant.GraphID == "decepticon" {
			return nil
		}
	}
	return fmt.Errorf("/ok responds but decepticon assistant is not registered")
}

// CheckLiteLLM polls the LiteLLM health endpoint. Kept for diagnostics.
func CheckLiteLLM(env map[string]string) error {
	port := config.Get(env, "LITELLM_PORT", DefaultLiteLLMPort)
	url := fmt.Sprintf("http://localhost:%s/health/readiness", port)

	deadline := time.Now().Add(LiteLLMTimeout)
	client := &http.Client{Timeout: 5 * time.Second}

	for time.Now().Before(deadline) {
		resp, err := client.Get(url)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return nil
			}
		}
		time.Sleep(PollInterval)
	}

	return fmt.Errorf("LiteLLM proxy not ready after %s (port %s)", LiteLLMTimeout, port)
}

// CheckNeo4j polls the Neo4j HTTP endpoint. Kept for diagnostics.
func CheckNeo4j(env map[string]string) error {
	port := config.Get(env, "NEO4J_HTTP_PORT", DefaultNeo4jHTTPPort)
	url := fmt.Sprintf("http://localhost:%s", port)

	deadline := time.Now().Add(Neo4jTimeout)
	client := &http.Client{Timeout: 5 * time.Second}

	for time.Now().Before(deadline) {
		resp, err := client.Get(url)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode >= 200 && resp.StatusCode < 500 {
				return nil
			}
		}
		time.Sleep(PollInterval)
	}

	return fmt.Errorf("Neo4j not ready after %s (port %s)", Neo4jTimeout, port)
}

// WaitForServices verifies the agent stack is functionally ready.
//
// Infrastructure readiness (postgres, neo4j, litellm, langgraph http server)
// is delegated to Docker Compose healthchecks via `compose up --wait`,
// so by the time this runs every container is already healthy. The remaining
// gap is graph-compile readiness inside LangGraph: /ok answers before the
// decepticon graph finishes compiling, so we probe /assistants/search until
// the assistant is registered.
func WaitForServices(ctx context.Context, probe func(context.Context) error) error {
	ui.Info("Verifying agent stack...")
	if err := WaitForReadiness(ctx, probe); err != nil {
		ui.Error(err.Error())
		return err
	}
	ui.Success("Agent stack ready")
	return nil
}
