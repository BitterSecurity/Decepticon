# Independent host integration consumer review — 2026-11-11

For [OSS #1170](https://github.com/BitterSecurity/Decepticon/pull/1170), tested
head820858495f9705cd278d35bde496ba34357fd192 against freshly fetched main
d26ddaa1e3b0ccaffd50b982fcc2debeb650c840. The complete60-path status/hash
manifest and process/artifact hashes are in [the receipt](host-provider-consumer-review-20261111.json).
Subsequent changes in this review only correct compatibility/version-tracking
documentation; they do not change the tested runtime source.

## Review and actual verification

- Reviewed the public protocol, strict optional/required discovery and all seven
  consumer sites: agent model/tools, middleware composition, model dispatch,
  fallback error propagation, MCP methods, settings router and LLM factory.
  Optional absence alone selects null; installed/broken, ambiguous, disabled or
  absent required authority refuses. Public core retains no LangChain import.
- Existing forced-model aliases preserve selection precedence and no-fallback
  authority. Dispatch and result revocation use the provider's opaque handle;
  a failed required provider cannot reach ordinary model/tool composition.
- All four removed vendor modules and the policy/real-HTTP regressions are
  preserved in the private adapter merged by [SaaS #1709](https://github.com/BitterSecurity/decepticon-saas/pull/1709).
  Its59connected cases include four real compiled-agent/provider/tool HTTP
  profiles; five actual built-wheel entry-point profiles separately qualify
  discovery, settings and missing-provider/policy refusals without SaaS bootstrap.
- Independently ran all core tests and all engine middleware, agents, LLM and
  tools unit suites against the exact candidate source roots: **2844 passed,
  one skipped, zero failures/errors; process0**. The skipped optional ML detector
  test requires `transformers`, absent from the reused dependency environment.
  It is not counted as a passing case. Dependencies are reused read-only; the
  candidate's three package source paths override stale editable roots.

The60-path review accounts for removed vendor design/verification artifacts as
intentional public-core cleanup (their Git history remains), removed vendor
implementations/tests as relocation, and protocol/consumer/test/design/changelog
changes as the new integration contract. No deleted artifact is silently treated
as a passing qualification of the new runtime.

## Compatibility and remaining release gates

Pure OSS model/tool behavior retains its null path. The Desktop settings endpoint
is intentionally moved: a bare engine no longer serves that vendor API; the
private provider restores its existing route and response. The changelog now
states this explicitly and links the existing design instead of a missing file.

No package publication/version bump or production activation is performed by
this source review. The separately pinned Desktop Local runtime remains on its
existing source until a coherent vendor/OSS image/context is actually assembled
and qualified. Do not upgrade a Desktop runtime to seam-bearing OSS without its
vendor provider. Full selected-image/old-checkpoint/SDK/media and native E2B
lifecycle acceptance remains under the SaaS release PRs. The known Agent Server
security deferral is preserved, not a clean security scan. No new Secret access,
customer inference, provider sandbox or prod mutation was used.
