package infracontrol

import (
	"sync"
	"time"
)

// registryEntry is one in-memory record of a workload the daemon has
// touched in this session. The map is process-local — Sprint 1 does
// not persist across daemon restart.
type registryEntry struct {
	State        WorkloadState
	EngagementID string
	UpdatedAt    time.Time
}

// registry tracks workload→entry. Sprint 1's only consumer of
// EngagementID is `/v1/profiles` for now; the engagement-scoped bulk
// cleanup tool ships with Sprint 2 (orchestrator prompt update).
//
// The registry also owns the per-workload mutex used to serialize
// concurrent start/stop on the same workload (P3 from the design
// review). Two `ops_start("ad")` calls land on the same mutex; the
// second blocks until the first finishes, observes the registry
// already records `running`, and the backend returns the existing
// handle without re-running compose up.
type registry struct {
	mu      sync.Mutex
	entries map[string]*registryEntry
	locks   map[string]*sync.Mutex
}

func newRegistry() *registry {
	return &registry{
		entries: map[string]*registryEntry{},
		locks:   map[string]*sync.Mutex{},
	}
}

// lockFor returns the per-workload mutex, creating it on first
// reference. Callers must Lock/Unlock around their backend op.
func (r *registry) lockFor(workload string) *sync.Mutex {
	r.mu.Lock()
	defer r.mu.Unlock()
	m, ok := r.locks[workload]
	if !ok {
		m = &sync.Mutex{}
		r.locks[workload] = m
	}
	return m
}

func (r *registry) set(workload string, state WorkloadState, engagementID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	e, ok := r.entries[workload]
	if !ok {
		e = &registryEntry{}
		r.entries[workload] = e
	}
	e.State = state
	if engagementID != "" {
		e.EngagementID = engagementID
	}
	e.UpdatedAt = time.Now().UTC()
}

func (r *registry) get(workload string) (registryEntry, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	e, ok := r.entries[workload]
	if !ok {
		return registryEntry{}, false
	}
	return *e, true
}

func (r *registry) snapshot() []WorkloadStatus {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]WorkloadStatus, 0, len(r.entries))
	for workload, e := range r.entries {
		out = append(out, WorkloadStatus{
			Workload:     workload,
			State:        e.State,
			EngagementID: e.EngagementID,
			Since:        e.UpdatedAt.Format(time.RFC3339),
		})
	}
	return out
}

// workloadsForEngagement returns the names of every workload currently
// associated with engagementID and not already in StateStopped. The
// caller is responsible for taking per-workload mutexes before
// stopping each one; this method is read-only.
func (r *registry) workloadsForEngagement(engagementID string) []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := []string{}
	for workload, e := range r.entries {
		if e.EngagementID == engagementID && e.State != StateStopped {
			out = append(out, workload)
		}
	}
	return out
}
