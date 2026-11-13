// Package inventory is a best-effort, postgres-backed asset inventory: passive
// scope-discovery results (CT / passive DNS / ...) are remembered across
// engagements so the launcher can reuse prior lookups instead of re-querying
// every time, per the CTEM asset-inventory model.
//
// It is BEST-EFFORT by design. The launcher's engagement/scope phase runs before
// (and independently of) `docker compose up`, so on a first run postgres is not
// up yet: Open fails and the caller proceeds with live discovery only. On later
// runs the stack is already up (services persist across CLI exits), so the
// inventory is available and accumulates. A nil *Store is valid and every method
// no-ops on it, so callers never branch on availability.
package inventory

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Asset is a passively-discovered scope asset remembered across engagements.
type Asset struct {
	Zone      string
	Host      string
	Source    string
	FirstSeen time.Time
	LastSeen  time.Time
}

// Store is a postgres-backed asset inventory. The zero value and a nil pointer
// are both valid no-op stores.
type Store struct {
	pool *pgxpool.Pool
}

// DSN builds the connection string for the stack postgres from its parts. The
// launcher publishes postgres on 127.0.0.1, so host is the loopback the compose
// `postgres` service is bound to.
func DSN(host string, port int, user, password, database string) string {
	if host == "" {
		host = "127.0.0.1"
	}
	if port == 0 {
		port = 5432
	}
	return fmt.Sprintf("postgres://%s:%s@%s:%d/%s?sslmode=disable&connect_timeout=3",
		user, password, host, port, database)
}

// Open connects to postgres and ensures the inventory schema. BEST-EFFORT: if
// postgres is unreachable (stack not up — common on a first run) it returns an
// error and the caller should proceed without an inventory.
func Open(ctx context.Context, dsn string) (*Store, error) {
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, fmt.Errorf("parse inventory dsn: %w", err)
	}
	cfg.MaxConns = 2
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("connect inventory: %w", err)
	}
	pingCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	if err := pool.Ping(pingCtx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping inventory: %w", err)
	}
	s := &Store{pool: pool}
	if err := s.ensureSchema(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return s, nil
}

// Close releases the pool. Safe on a nil store.
func (s *Store) Close() {
	if s != nil && s.pool != nil {
		s.pool.Close()
	}
}

func (s *Store) ensureSchema(ctx context.Context) error {
	const ddl = `
CREATE TABLE IF NOT EXISTS decepticon_asset_inventory (
    zone       text        NOT NULL,
    host       text        NOT NULL,
    source     text        NOT NULL DEFAULT 'passive',
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (zone, host)
)`
	if _, err := s.pool.Exec(ctx, ddl); err != nil {
		return fmt.Errorf("ensure inventory schema: %w", err)
	}
	return nil
}

// Record upserts discovered hosts for a zone, refreshing last_seen on repeats.
// No-ops on a nil store or empty input.
func (s *Store) Record(ctx context.Context, zone, source string, hosts []string) error {
	if s == nil || s.pool == nil || zone == "" || len(hosts) == 0 {
		return nil
	}
	if source == "" {
		source = "passive"
	}
	const q = `
INSERT INTO decepticon_asset_inventory (zone, host, source)
VALUES ($1, $2, $3)
ON CONFLICT (zone, host) DO UPDATE SET last_seen = now(), source = EXCLUDED.source`
	for _, h := range hosts {
		if h == "" {
			continue
		}
		if _, err := s.pool.Exec(ctx, q, zone, h, source); err != nil {
			return fmt.Errorf("record asset %q: %w", h, err)
		}
	}
	return nil
}

// Lookup returns remembered hosts for a zone, most-recently-seen first. No-ops
// (nil, nil) on a nil store.
func (s *Store) Lookup(ctx context.Context, zone string) ([]string, error) {
	if s == nil || s.pool == nil || zone == "" {
		return nil, nil
	}
	rows, err := s.pool.Query(ctx,
		`SELECT host FROM decepticon_asset_inventory WHERE zone = $1 ORDER BY last_seen DESC`, zone)
	if err != nil {
		return nil, fmt.Errorf("lookup zone %q: %w", zone, err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var h string
		if err := rows.Scan(&h); err != nil {
			return nil, err
		}
		out = append(out, h)
	}
	return out, rows.Err()
}
