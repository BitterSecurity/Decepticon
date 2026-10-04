# Skillogy retrieval evaluation

`cases-v1.json` is a versioned, 18-case search set adapted from prod's
`apps/decepticon/benchmark/skillogy/cases-v1.json` at `aae2ef8e`.
Two prod cases that require prerequisite/validator bundles are omitted because
the OSS `find_skill` response does not yet expose selection roles or bundles.
Two OSS-specific cases cover SQL injection and AWS IAM enumeration. Every
expected and forbidden path is checked against the OSS skill tree.

With the Skillogy service running and its catalog loaded, run from the repo root:

```bash
DECEPTICON_SKIP_BOOT=1 uv run python -m decepticon.skillogy.evaluation \
  benchmark/skillogy/cases-v1.json \
  --skills-root packages/decepticon/decepticon/skills \
  --source-revision "$(git rev-parse HEAD)" \
  --url http://localhost:9100 > skillogy-evaluation.json
```

If `SKILLOGY_API_KEY` is set, the runner forwards it to the service. To check
only the dataset without a running service, add `--check-dataset`.

The report contains Recall@1/3/10 over positive cases, abstention precision
and recall over negative cases, forbidden-skill exposure at rank 3, p95 search
latency, and bytes of the first returned skill body loaded for each case.
Estimated loaded tokens use the same bytes/4 proxy as prod; they are not model
tokenizer counts. A zero abstention precision when the service never abstains
means the metric has no positive predictions; inspect abstention recall too.
The runner does not set pass/fail thresholds: compare reports from identical
datasets, skill catalogs, service settings, and embedding availability.
