# OpenShell refusal fixture type qualification

Fresh main `79978a642b7a9c9cbd351150774b869fbb724416`, integrated #1144 head `4a404063f228633086b2ab93e0a8d4c78559aa27`. Actual [Python job](https://github.com/BitterSecurity/Decepticon/actions/runs/41671886792/job/126042279084) executed and failed the unchanged `make ci-lint` errors-only gate: two `reportOptionalMemberAccess` diagnostics in the new fake-client policy-mutation tests. Ruff check/format passed; tests were not reached by that job. This was not a billing failure.

Add explicit non-None assertions for the captured creation spec before policy mutation. These are real fixture preconditions and preserve every denial assertion. No diagnostic ignore, baseline change, runtime/library edit, dependency/version bump, release or deployment. Existing c61e1321installed wheel/source qualification remains separately attributed; all runtime package trees must match it before parent merge.

Qualified locally: unchanged `make ci-lint` exits0 (all885Python files Ruff-formatted; basedpyright851files,0errors,985existing non-blocking warnings). The two affected test modules run in the existing separate wheel-only environment:62passed,0failures/errors/skips, process exit0 in7.156s (pytest4.69s). Runtime module origin remains the actual c61e1321candidate wheel, with no source overlay. The three application package trees exactly match that qualified runtime source; no full image rebuild or repeated full suite is attributed to this test-only correction.


The actual CodeQL check also flags the old exact set-membership assertion as incomplete substring URL sanitization. There is no URL/user-input sanitizer on this test path. Assert the entire default endpoint set instead, explicitly removing the optional override in that fixture. This strengthens the tested contract and removes the ambiguous membership shape without suppressing or dismissing the security rule.

The parent sandbox Docker build separately fails while pip tries uninstalling Debian-owned certifi without a RECORD file. That build correction is a separate task; it is not repaired or passed by this test-only PR.
