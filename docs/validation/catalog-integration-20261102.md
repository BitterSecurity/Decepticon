# Current-main Terra catalog integration

Integrate #1139 on qualified main ce4a94368c17b0c20e34621f4c3a918bfbe660a2, preserving dependency-security #1141 and isolated-test #1142. Review all ten source paths: the shared public row, generated local/optional catalogs and CLI data, build-time Responses mapper compatibility, tests and instructions.

The new helper failed the full Ruff format gate because of its triple-quoted delimiter. Normalize that delimiter and prove the parsed AST and string constant are unchanged. All884 files now pass format and full Ruff passes. This makes the current source gate reviewable without a suppression. Correct the instructions to preserve the user's local-verification preference while checking actual hosted status; OSS CI/Security did run successfully for the prerequisite merges.

Frozen npm install succeeds. Streaming/CLI build and CLI typecheck succeed;41files/300CLI tests pass32.87s. The high-severity npm audit gate exits0; existing3low/19moderate dependency findings remain, with no lockfile change. Ten generator/tamper/propagation tests pass12.22s; current generator check reports205public models and four clean outputs.

Rebuild the actual gateway through the shared coordinator and test its LiteLLM1.88.1 mapper against the current generated OSS local YAML with dummy credentials and external networking disabled:10passed,8unrelated production-YAML variants deselected,20.80s,exit0. This covers configured route/effort translation and both summary-policy cases; the fixture verifies tool-call request/response conversion. The old previously documented image had been removed; that failed repeat started no tests. The fresh measured image and installed helper hash are in the receipt. No provider call, image publication, account availability or installed-session acceptance is inferred.

All Python-package/test/benchmark, lockfile, Web/shared and telemetry input objects are byte-identical to qualified ce4main. Reuse the actual #1142 full fast Python6133pass/3Windows-onlyskips/197.72s/errors-only typecheck0 and #1141 Web/telemetry verification for those unchanged inputs. The changed CLI, catalog and gateway gates were rerun above. The evidence explicitly separates reused checks from new runs. No package version, tag, public release or SaaS deployment.

[Receipt and logs](evidence/catalog-integration-20261102/receipt.json) · [CHAGELOG.md](../../CHAGELOG.md).
