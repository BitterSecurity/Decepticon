package health

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestProbeLangGraphRejectsRedirectsAndInvalidPorts(t *testing.T) {
	for _, port := range []string{"0", "65536", "2024@other-host", "not-a-port"} {
		if err := ProbeLangGraph(context.Background(), map[string]string{"LANGGRAPH_PORT": port}); err == nil || !strings.Contains(err.Error(), "invalid LANGGRAPH_PORT") {
			t.Fatalf("invalid port %q accepted: %v", port, err)
		}
	}
	var redirected atomic.Bool
	destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { redirected.Store(true); w.WriteHeader(200) }))
	defer destination.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, destination.URL, http.StatusFound)
	}))
	defer server.Close()
	port := strings.TrimPrefix(server.URL, "http://127.0.0.1:")
	err := ProbeLangGraph(context.Background(), map[string]string{"LANGGRAPH_PORT": port})
	if err == nil || !strings.Contains(err.Error(), "HTTP 302") || redirected.Load() {
		t.Fatalf("redirect should be reported without following it: err=%v followed=%v", err, redirected.Load())
	}
}

func TestCheckLangGraphRetriesUntilExactGraphIsRegistered(t *testing.T) {
	t.Setenv("DECEPTICON_STARTUP_TIMEOUT_SECONDS", "")
	previousTimeout, previousPoll := LangGraphTimeout, PollInterval
	LangGraphTimeout, PollInterval = time.Second, 5*time.Millisecond
	t.Cleanup(func() { LangGraphTimeout, PollInterval = previousTimeout, previousPoll })
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ok" {
			w.WriteHeader(200)
			return
		}
		if calls.Add(1) < 3 {
			_, _ = w.Write([]byte(`[]`))
			return
		}
		_, _ = w.Write([]byte(`[{"graph_id":"decepticon"}]`))
	}))
	defer server.Close()
	port := strings.TrimPrefix(server.URL, "http://127.0.0.1:")
	if err := CheckLangGraph(map[string]string{"LANGGRAPH_PORT": port}); err != nil {
		t.Fatalf("graph registration did not recover: %v", err)
	}
}

func TestCheckLangGraphReportsFailurePhase(t *testing.T) {
	t.Setenv("DECEPTICON_STARTUP_TIMEOUT_SECONDS", "")
	previousTimeout, previousPoll := LangGraphTimeout, PollInterval
	LangGraphTimeout, PollInterval = 60*time.Millisecond, 5*time.Millisecond
	t.Cleanup(func() { LangGraphTimeout, PollInterval = previousTimeout, previousPoll })
	for _, tc := range []struct {
		name, catalog, want         string
		healthStatus, catalogStatus int
	}{
		{"health unavailable", `[{"graph_id":"decepticon"}]`, "/ok returned HTTP 503", 503, 200},
		{"catalog unauthorized", `[]`, "/assistants/search returned HTTP 401", 200, 401},
		{"graph missing", `[]`, "decepticon assistant is not registered", 200, 200},
		{"unrelated metadata", `[{"graph_id":"other","name":"decepticon"}]`, "decepticon assistant is not registered", 200, 200},
		{"invalid catalog", `not-json`, "invalid assistant catalog", 200, 200},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/ok" {
					w.WriteHeader(tc.healthStatus)
					return
				}
				w.WriteHeader(tc.catalogStatus)
				_, _ = w.Write([]byte(tc.catalog))
			}))
			defer server.Close()
			port := strings.TrimPrefix(server.URL, "http://127.0.0.1:")
			err := CheckLangGraph(map[string]string{"LANGGRAPH_PORT": port})
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("expected failure containing %q, got %v", tc.want, err)
			}
		})
	}
}

func TestCheckLangGraphBoundsUnresponsiveHostRequest(t *testing.T) {
	t.Setenv("DECEPTICON_STARTUP_TIMEOUT_SECONDS", "")
	previousTimeout := LangGraphTimeout
	LangGraphTimeout = 60 * time.Millisecond
	t.Cleanup(func() { LangGraphTimeout = previousTimeout })
	server := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(6 * time.Second):
		}
	}))
	defer server.Close()
	port := strings.TrimPrefix(server.URL, "http://127.0.0.1:")
	started := time.Now()
	err := CheckLangGraph(map[string]string{"LANGGRAPH_PORT": port})
	if err == nil || !strings.Contains(err.Error(), "host request failed") {
		t.Errorf("expected host connectivity diagnostic, got %v", err)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Errorf("60ms readiness deadline exceeded: %s", elapsed)
	}
}

func TestCheckLangGraph_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" && r.URL.Path == "/ok" {
			w.WriteHeader(http.StatusOK)
			return
		}
		if r.Method == "POST" && r.URL.Path == "/assistants/search" {
			w.WriteHeader(http.StatusOK)
			w.Write([]byte(`[{"assistant_id": "abc", "graph_id": "decepticon"}]`))
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	port := server.Listener.Addr().String()
	env := map[string]string{"LANGGRAPH_PORT": port[len("127.0.0.1:"):]}

	err := CheckLangGraph(env)
	if err != nil {
		t.Errorf("CheckLangGraph() unexpected error: %v", err)
	}
}

// 200 with an empty array means the API booted but the graph isn't compiled.
// CheckLangGraph must reject this and keep polling until timeout.
func TestCheckLangGraph_RejectsEmptyBody(t *testing.T) {
	prev := LangGraphTimeout
	LangGraphTimeout = 1 * time.Second
	defer func() { LangGraphTimeout = prev }()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`[]`))
	}))
	defer server.Close()

	port := server.Listener.Addr().String()
	env := map[string]string{"LANGGRAPH_PORT": port[len("127.0.0.1:"):]}

	if err := CheckLangGraph(env); err == nil {
		t.Error("CheckLangGraph() should fail when body lacks decepticon assistant")
	}
}

func TestCheckLiteLLM_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health/readiness" {
			w.WriteHeader(http.StatusOK)
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer server.Close()

	port := server.Listener.Addr().String()
	env := map[string]string{"LITELLM_PORT": port[len("127.0.0.1:"):]}

	err := CheckLiteLLM(env)
	if err != nil {
		t.Errorf("CheckLiteLLM() unexpected error: %v", err)
	}
}

func TestCheckNeo4j_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	port := server.Listener.Addr().String()
	env := map[string]string{"NEO4J_HTTP_PORT": port[len("127.0.0.1:"):]}

	if err := CheckNeo4j(env); err != nil {
		t.Errorf("CheckNeo4j() unexpected error: %v", err)
	}
}

func TestProbeLangGraphUsesComposeProcessPort(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.URL.Path == "/assistants/search" {
			_, _ = w.Write([]byte(`[{"graph_id":"decepticon"}]`))
		}
	}))
	defer server.Close()
	port := strings.TrimPrefix(server.URL, "http://127.0.0.1:")
	t.Setenv("LANGGRAPH_PORT", port)
	// Compose uses the process environment before --env-file. A stale file
	// port must not redirect readiness away from the published endpoint.
	if err := ProbeLangGraph(context.Background(), map[string]string{"LANGGRAPH_PORT": "invalid-file-port"}); err != nil {
		t.Fatalf("process-selected published endpoint was not probed: %v", err)
	}
	if requests.Load() != 2 {
		t.Fatalf("expected health and assistant requests, got %d", requests.Load())
	}
	t.Setenv("LANGGRAPH_PORT", "invalid-process-port")
	if err := ProbeLangGraph(context.Background(), map[string]string{"LANGGRAPH_PORT": port}); err == nil || !strings.Contains(err.Error(), "invalid LANGGRAPH_PORT") {
		t.Fatalf("invalid Compose process port was ignored: %v", err)
	}
}

func TestProbeLangGraphEmptyProcessPortUsesComposeDefault(t *testing.T) {
	t.Setenv("LANGGRAPH_PORT", "")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := ProbeLangGraph(ctx, map[string]string{"LANGGRAPH_PORT": "invalid-file-port"})
	if err == nil || !strings.Contains(err.Error(), "http://127.0.0.1:2024/ok") {
		t.Fatalf("empty process value must select the Compose default: %v", err)
	}
}
