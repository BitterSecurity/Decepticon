package engagement

// Target normalization shared with decepticon_core.types.roe: the golden
// vectors in packages/decepticon-core/tests/testdata/roe-target-vectors.json
// are consumed by both the Go and Python test suites, so the launcher and the
// MCP/CLI builder cannot drift apart. IDNA diverges on purpose: Python
// IDNA-encodes non-ASCII hosts, Go rejects them with an actionable message
// (operators pass the punycode ``xn--`` form).

import (
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"regexp"
	"strings"
)

var embeddedListSplit = regexp.MustCompile(`[,\s]+`)
var scopeChars = regexp.MustCompile(`^[a-z0-9.\-*?:]+$`)

func splitScopeTargets(targets []string) []string {
	var fragments []string
	for _, raw := range targets {
		for _, part := range embeddedListSplit.Split(raw, -1) {
			if part != "" {
				fragments = append(fragments, part)
			}
		}
	}
	return fragments
}

// normalizeHostname lowercases, strips DNS trailing dots, and rejects
// non-ASCII (the Go counterpart of the Python IDNA encoding).
func normalizeHostname(host string) (string, error) {
	value := strings.ToLower(strings.TrimRight(strings.TrimSpace(host), "."))
	if value == "" {
		return "", nil
	}
	for _, r := range value {
		if r > 0x7f {
			return "", fmt.Errorf("non-ASCII hostname %q: pass the punycode (xn--) form instead", host)
		}
	}
	return value, nil
}

// normalizeScopeTarget reduces a fragment to a scope pattern. URL parse
// failures and non-ASCII hostnames are errors (loud, like the Python builder).
func normalizeScopeTarget(raw string) (string, error) {
	value := strings.TrimSpace(raw)
	if value == "" {
		return "", nil
	}
	if strings.Contains(value, "://") || strings.HasPrefix(value, "//") {
		parsed, err := url.Parse(value)
		if err != nil {
			return "", fmt.Errorf("parse target %q: %w", raw, err)
		}
		return normalizeHostname(parsed.Hostname())
	}
	if strings.Contains(value, "/") {
		if prefix, prefixErr := netip.ParsePrefix(value); prefixErr == nil {
			return prefix.Masked().String(), nil
		}
	}
	// A bare ``host:port`` or ``host/path`` — keep only the host.
	value = strings.SplitN(value, "/", 2)[0]
	if at := strings.LastIndex(value, "@"); at >= 0 {
		// scp-style / userinfo git remotes: the network enforcement cares
		// about the host, not the credential.
		value = value[at+1:]
	}
	if strings.HasPrefix(value, "[") {
		// Bracketed bare IPv6, optionally with a port: [2001:db8::1]:8443.
		rest := value[1:]
		if end := strings.Index(rest, "]"); end >= 0 {
			value = rest[:end]
		} else {
			value = rest
		}
	} else if !looksLikeIPv6(value) {
		if colon := strings.Index(value, ":"); colon >= 0 {
			value = value[:colon]
		}
	}
	if strings.Trim(value, "*.") == "" {
		// Pathological wildcard/dot forms ("*", "*.", "*.."): leave them for
		// the dead-pattern check — trailing-dot stripping would collapse the
		// dead "*." into the dangerously broad "*".
		return value, nil
	}
	return normalizeHostname(value)
}

func looksLikeIPv6(value string) bool {
	if ip := net.ParseIP(value); ip != nil {
		return strings.Contains(value, ":")
	}
	return strings.Count(value, ":") > 1
}

func isDeadPattern(pattern string) bool {
	if net.ParseIP(pattern) != nil {
		return false
	}
	if _, err := netip.ParsePrefix(pattern); err == nil {
		return false
	}
	if pattern == "" || !scopeChars.MatchString(pattern) {
		return true
	}
	if strings.HasPrefix(pattern, "*.") {
		body := pattern[2:]
		if body == "" {
			return true
		}
		for _, part := range strings.Split(body, ".") {
			if part == "" {
				return true
			}
		}
		return false
	}
	if strings.ContainsAny(pattern, "*?") {
		return false
	}
	for _, part := range strings.Split(pattern, ".") {
		if part == "" {
			return true
		}
	}
	return false
}

// dedupeScopeTargets normalizes fragments, preserves first-seen order, and
// collects the patterns no DNS/IP target can match.
func dedupeScopeTargets(fragments []string) (in, dropped []string, err error) {
	seen := map[string]bool{}
	for _, raw := range fragments {
		norm, normErr := normalizeScopeTarget(raw)
		if normErr != nil {
			return nil, nil, normErr
		}
		if norm == "" {
			continue
		}
		if isDeadPattern(norm) {
			dropped = append(dropped, norm)
			continue
		}
		if !seen[norm] {
			seen[norm] = true
			in = append(in, norm)
		}
	}
	return in, dropped, nil
}

type scopeLists struct {
	InScope    []string
	OutOfScope []string
	Dropped    []string
}

// buildScopeLists compiles creation inputs into the machine scope: operator
// targets first (order preserved), without implicitly adding wildcard apexes.
// The denylist is passed through. An engagement with no surviving in-scope target is an error
// — the same fail-closed contract as the Python builder.
func buildScopeLists(targets, outOfScope []string) (scopeLists, error) {
	inScope, dropped, err := dedupeScopeTargets(splitScopeTargets(targets))
	if err != nil {
		return scopeLists{}, err
	}
	outScope, droppedOut, err := dedupeScopeTargets(splitScopeTargets(outOfScope))
	if err != nil {
		return scopeLists{}, err
	}
	if outScope == nil {
		outScope = []string{}
	}
	lists := scopeLists{InScope: inScope, OutOfScope: outScope, Dropped: append(dropped, droppedOut...)}
	if len(lists.InScope) == 0 {
		return lists, fmt.Errorf("engagement scope requires at least one in-scope target")
	}
	return lists, nil
}

func containsString(list []string, value string) bool {
	for _, item := range list {
		if item == value {
			return true
		}
	}
	return false
}
