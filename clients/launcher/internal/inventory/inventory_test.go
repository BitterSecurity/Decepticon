package inventory

import (
	"context"
	"os"
	"testing"
)

func TestDSN(t *testing.T) {
	got := DSN("", 0, "decepticon", "pw", "litellm")
	want := "postgres://decepticon:pw@127.0.0.1:5432/litellm?sslmode=disable&connect_timeout=3"
	if got != want {
		t.Errorf("DSN = %q, want %q", got, want)
	}
	if h := DSN("db.example", 6543, "u", "p", "d"); h != "postgres://u:p@db.example:6543/d?sslmode=disable&connect_timeout=3" {
		t.Errorf("custom DSN wrong: %q", h)
	}
}

func TestNilStoreNoops(t *testing.T) {
	var s *Store // nil store = postgres unavailable (stack not up)
	s.Close()
	if err := s.Record(context.Background(), "z", "passive", []string{"a"}); err != nil {
		t.Errorf("nil Record should no-op, got %v", err)
	}
	got, err := s.Lookup(context.Background(), "z")
	if err != nil || got != nil {
		t.Errorf("nil Lookup should return (nil,nil), got (%v,%v)", got, err)
	}
}

// TestLiveInventory is a real-postgres integration check (guarded). Point it at
// a throwaway postgres:
//
//	INVENTORY_TEST_DSN='postgres://u:p@127.0.0.1:5432/db?sslmode=disable' \
//	  go test -run TestLiveInventory -v ./internal/inventory/
func TestLiveInventory(t *testing.T) {
	dsn := os.Getenv("INVENTORY_TEST_DSN")
	if dsn == "" {
		t.Skip("set INVENTORY_TEST_DSN to run the live inventory test")
	}
	ctx := context.Background()
	s, err := Open(ctx, dsn)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer s.Close()

	zone := "itest.example.com"
	if err := s.Record(ctx, zone, "passive", []string{"a." + zone, "b." + zone}); err != nil {
		t.Fatalf("Record: %v", err)
	}
	// Upsert one (refresh) and add a new one from a different source.
	if err := s.Record(ctx, zone, "passive-dns", []string{"a." + zone, "c." + zone}); err != nil {
		t.Fatalf("Record (2nd): %v", err)
	}
	got, err := s.Lookup(ctx, zone)
	if err != nil {
		t.Fatalf("Lookup: %v", err)
	}
	set := map[string]bool{}
	for _, h := range got {
		set[h] = true
	}
	for _, want := range []string{"a." + zone, "b." + zone, "c." + zone} {
		if !set[want] {
			t.Errorf("missing %q in inventory %v", want, got)
		}
	}
	// A different zone must not leak.
	other, err := s.Lookup(ctx, "other.example.com")
	if err != nil {
		t.Fatalf("Lookup other: %v", err)
	}
	if len(other) != 0 {
		t.Errorf("unexpected cross-zone leak: %v", other)
	}
}
