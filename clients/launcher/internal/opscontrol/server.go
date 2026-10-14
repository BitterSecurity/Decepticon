package opscontrol

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"regexp"
	"strings"
)

// Server is the HTTP API the agent calls. It binds to a Unix-domain
// socket — never to TCP — and a langgraph-only bind-mount is the
// capability grant. ADR-0006 §1' and §6 (Anthropic Claude Code
// sandboxing, Vercel Sandbox, Fly Machines) converge on this shape.
type Server struct {
	Backend   Backend
	Allowlist *Allowlist
	Registry  *registry
	Logger    *slog.Logger
}

// NewServer assembles a server with the standard registry + an
// internal lock map for per-workload serialization.
func NewServer(backend Backend, allow *Allowlist, logger *slog.Logger) *Server {
	if logger == nil {
		logger = slog.New(slog.NewTextHandler(os.Stderr, nil))
	}
	return &Server{
		Backend:   backend,
		Allowlist: allow,
		Registry:  newRegistry(),
		Logger:    logger,
	}
}

func (s *Server) mux() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/health", s.handleHealth)
	mux.HandleFunc("GET /v1/profiles", s.handleList)
	mux.HandleFunc("POST /v1/profiles/{workload}/start", s.handleStart)
	mux.HandleFunc("POST /v1/profiles/{workload}/stop", s.handleStop)
	mux.HandleFunc("POST /v1/engagements/{engagement}/cleanup", s.handleCleanupEngagement)
	return mux
}

// healthResponse is the envelope `/v1/health` returns. The allowlist
// is included so a misconfigured allowlist (e.g., bad
// DECEPTICON_OPS_ALLOWLIST_EXTRA) is caught by operators without
// reading daemon logs.
type healthResponse struct {
	OK        bool     `json:"ok"`
	Backend   string   `json:"backend"`
	Allowlist []string `json:"allowlist"`
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, healthResponse{
		OK:        true,
		Backend:   s.Backend.Name(),
		Allowlist: s.Allowlist.Members(),
	})
}

func (s *Server) handleList(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.Registry.snapshot())
}

func (s *Server) handleStart(w http.ResponseWriter, r *http.Request) {
	workload := r.PathValue("workload")
	if !s.Allowlist.Permits(workload) {
		writeError(w, http.StatusBadRequest, "workload not in allowlist or contains illegal characters")
		return
	}
	engagementID := strings.TrimSpace(r.URL.Query().Get("engagement"))

	// Async control plane (ADR-0006 §1' converges with Fly Machines /
	// Modal Sandbox / Anthropic Bash run_in_background): the HTTP
	// handler returns IMMEDIATELY with the registry state. Long-running
	// compose calls (BHCE cold start measured 90-300s) run in a
	// goroutine bound to the daemon's own context, and the
	// OpsControlNotificationMiddleware delivers state transitions to
	// the agent through <system-reminder> blocks on its next turn.
	// Holding the HTTP request open for 5+ minutes invites
	// load-balancer timeouts, NAT eviction, and silent client
	// disconnects — none of which the agent loop can recover from.

	lock := s.Registry.lockFor(workload)
	lock.Lock()

	// State-aware fast paths run while holding the lock so a
	// race-in second request observes the in-flight Starting state
	// instead of duplicating the spawn.
	if existing, ok := s.Registry.get(workload); ok {
		switch existing.State {
		case StateRunning, StateStarting:
			lock.Unlock()
			writeJSON(w, http.StatusAccepted, Handle{
				Workload:     workload,
				State:        existing.State,
				EngagementID: existing.EngagementID,
			})
			return
		}
	}

	s.Registry.set(workload, StateStarting, engagementID)

	// The goroutine owns the lock for the full duration of the spawn.
	// We deliberately use a daemon-scoped context, not r.Context() —
	// the request returns in milliseconds, so a request-scoped
	// context would cancel compose mid-spawn the moment the client
	// disconnects.
	go func() {
		defer lock.Unlock()
		ctx := context.Background()
		handle, err := s.Backend.Start(ctx, workload, engagementID)
		if err != nil {
			s.Registry.set(workload, StateUnknown, engagementID)
			s.Logger.Error("opscontrol start failed",
				"workload", workload,
				"engagement", engagementID,
				"err", err,
			)
			return
		}
		s.Registry.set(workload, handle.State, engagementID)
		s.Logger.Info("opscontrol start ok",
			"workload", workload,
			"engagement", engagementID,
			"state", string(handle.State),
		)
	}()

	writeJSON(w, http.StatusAccepted, Handle{
		Workload:     workload,
		State:        StateStarting,
		EngagementID: engagementID,
	})
}

func (s *Server) handleStop(w http.ResponseWriter, r *http.Request) {
	workload := r.PathValue("workload")
	if !s.Allowlist.Permits(workload) {
		writeError(w, http.StatusBadRequest, "workload not in allowlist or contains illegal characters")
		return
	}

	// Async control plane — same shape as handleStart. The HTTP
	// handler returns IMMEDIATELY with state: "stopping"; the
	// long-running `compose stop` + `compose rm -fs` (which can
	// take a minute+ on a BHCE Neo4j shutdown) runs in a goroutine
	// bound to a daemon-scoped context. The previous synchronous
	// implementation tied the compose call to `r.Context()`, so if
	// the agent's ops_stop tool-result chunked-response was cut
	// short the compose call was cancelled mid-shutdown and the
	// registry was never flipped to "stopped" — even though docker
	// itself had brought the containers down. The middleware then
	// missed the running->stopped transition and the agent waited
	// forever for an auto-notification that never came.

	lock := s.Registry.lockFor(workload)
	lock.Lock()

	// Preserve the engagement tag across stop so the cleanup audit
	// trail (workloadsForEngagement) and the middleware-injected
	// reminder still carry the originating engagement id.
	priorEngagement := ""
	if existing, ok := s.Registry.get(workload); ok {
		priorEngagement = existing.EngagementID
		switch existing.State {
		case StateStopped, StateStopping:
			lock.Unlock()
			writeJSON(w, http.StatusAccepted, Handle{
				Workload:     workload,
				State:        existing.State,
				EngagementID: existing.EngagementID,
			})
			return
		}
	}

	s.Registry.set(workload, StateStopping, priorEngagement)

	go func() {
		defer lock.Unlock()
		ctx := context.Background()
		if err := s.Backend.Stop(ctx, workload); err != nil {
			// Mark Unknown so the middleware emits a transition
			// reminder and the agent stops waiting for a clean
			// "stopped". The actual container state on the docker
			// daemon may be partially-down at this point; the
			// agent + operator can reconcile via `ops_status` or
			// `docker ps`.
			s.Registry.set(workload, StateUnknown, priorEngagement)
			s.Logger.Error("opscontrol stop failed",
				"workload", workload,
				"engagement", priorEngagement,
				"err", err,
			)
			return
		}
		s.Registry.set(workload, StateStopped, priorEngagement)
		s.Logger.Info("opscontrol stop ok",
			"workload", workload,
			"engagement", priorEngagement,
		)
	}()

	writeJSON(w, http.StatusAccepted, Handle{
		Workload:     workload,
		State:        StateStopping,
		EngagementID: priorEngagement,
	})
}

// cleanupResponse summarizes a bulk engagement teardown so the agent
// can confirm what was stopped (or surface which workloads errored
// out and stayed up).
type cleanupResponse struct {
	Engagement string            `json:"engagement"`
	Stopped    []string          `json:"stopped"`
	Errors     map[string]string `json:"errors,omitempty"`
}

// engagementName matches what RFC 1123-style identifiers and our
// engagement picker emit. We are intentionally lax (allow dots /
// underscores) because engagement IDs are operator-chosen and have
// no shell-injection blast radius — the daemon only uses the ID as a
// registry key, never as a shell argument.
var engagementName = regexp.MustCompile(`^[A-Za-z0-9._-]{1,128}$`)

func (s *Server) handleCleanupEngagement(w http.ResponseWriter, r *http.Request) {
	engagement := r.PathValue("engagement")
	if !engagementName.MatchString(engagement) {
		writeError(w, http.StatusBadRequest, "engagement id contains illegal characters")
		return
	}
	targets := s.Registry.workloadsForEngagement(engagement)
	resp := cleanupResponse{Engagement: engagement, Stopped: []string{}, Errors: map[string]string{}}
	for _, workload := range targets {
		lock := s.Registry.lockFor(workload)
		lock.Lock()
		err := s.Backend.Stop(r.Context(), workload)
		if err != nil {
			s.Logger.Error("opscontrol cleanup stop failed",
				"engagement", engagement, "workload", workload, "err", err)
			resp.Errors[workload] = err.Error()
			lock.Unlock()
			continue
		}
		s.Registry.set(workload, StateStopped, "")
		resp.Stopped = append(resp.Stopped, workload)
		lock.Unlock()
	}
	if len(resp.Errors) == 0 {
		resp.Errors = nil
	}
	writeJSON(w, http.StatusAccepted, resp)
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

type errorEnvelope struct {
	Error string `json:"error"`
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, errorEnvelope{Error: msg})
}
