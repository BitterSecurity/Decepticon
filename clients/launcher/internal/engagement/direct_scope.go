package engagement

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type directScopeWindow struct {
	Start string `json:"start"`
	End   string `json:"end"`
}

// machineScope is the generated RoE: one machine-enforceable scope compiled
// from the creation inputs. There is no separate human scope document and no
// approval marker — creating the engagement with a scope IS the
// authorization, and the middleware enforces this block unconditionally
// (post-unification there is no mode field: presence is enforcement).
type machineScope struct {
	InScope           []string            `json:"in_scope"`
	OutOfScope        []string            `json:"out_of_scope"`
	AuthorizedWindows []directScopeWindow `json:"authorized_windows"`
}

type machineRoeDocument struct {
	EngagementName     string       `json:"engagement_name"`
	MachineEnforcement machineScope `json:"machine_enforcement"`
}

// buildRoeDocument encodes the machine-enforced RoE JSON from the normalized
// scope lists, stamping a now→now+7d authorized window.
func buildRoeDocument(slug string, lists scopeLists, now time.Time) ([]byte, error) {
	now = now.UTC()
	end := now.AddDate(0, 0, 7)
	document := machineRoeDocument{
		EngagementName: slug,
		MachineEnforcement: machineScope{
			InScope:           lists.InScope,
			OutOfScope:        lists.OutOfScope,
			AuthorizedWindows: []directScopeWindow{{Start: now.Format(time.RFC3339), End: end.Format(time.RFC3339)}},
		},
	}
	raw, err := json.MarshalIndent(document, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("encode direct scope: %w", err)
	}
	return append(raw, '\n'), nil
}

// createDirectEngagement only owns a newly created workspace. Existing scopes
// are never migrated or overwritten during creation. The caller compiles the
// scope through the shared normalization contract (see scope_targets.go and
// the golden vectors) and shows it for review; dropped entries are surfaced
// in that preview, not re-reported here.
func createDirectEngagement(home, slug string, lists scopeLists, now time.Time) (Choice, error) {
	if err := validateSlug(home, slug); err != nil {
		return Choice{}, err
	}
	if len(lists.InScope) == 0 {
		return Choice{}, fmt.Errorf("engagement scope requires at least one in-scope target")
	}
	raw, err := buildRoeDocument(slug, lists, now)
	if err != nil {
		return Choice{}, err
	}
	workspace := filepath.Join(home, "workspace")
	if err := os.MkdirAll(workspace, 0o700); err != nil {
		return Choice{}, fmt.Errorf("create workspace root: %w", err)
	}
	root := filepath.Join(workspace, slug)
	if err := os.Mkdir(root, 0o700); err != nil {
		return Choice{}, fmt.Errorf("create engagement: %w", err)
	}
	if err := os.Mkdir(filepath.Join(root, "plan"), 0o700); err != nil {
		return Choice{}, fmt.Errorf("create scope directory: %w", err)
	}
	if err := os.WriteFile(filepath.Join(root, "plan", "roe.json"), raw, 0o600); err != nil {
		return Choice{}, fmt.Errorf("write direct scope: %w", err)
	}
	return choiceForEngagement(home, slug), nil
}

// writeDraftScope rewrites an existing engagement's scope. It is allowed ONLY
// while the engagement is a draft (not yet started): once started the scope is
// locked and a different scope requires a new engagement. This is the only
// overwrite path for roe.json — creation never overwrites.
func writeDraftScope(home, slug string, lists scopeLists, now time.Time) error {
	if !slugRe.MatchString(slug) {
		return fmt.Errorf("invalid engagement name %q", slug)
	}
	if len(lists.InScope) == 0 {
		return fmt.Errorf("engagement scope requires at least one in-scope target")
	}
	root := filepath.Join(home, "workspace", slug)
	if IsStarted(root) {
		return fmt.Errorf("engagement %q has started; its scope is locked — create a new engagement for a different scope", slug)
	}
	raw, err := buildRoeDocument(slug, lists, now)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Join(root, "plan"), 0o700); err != nil {
		return fmt.Errorf("ensure scope directory: %w", err)
	}
	if err := os.WriteFile(filepath.Join(root, "plan", "roe.json"), raw, 0o600); err != nil {
		return fmt.Errorf("write draft scope: %w", err)
	}
	return nil
}
