# Planning adapter fragment

Only soundwave.md remains here. PromptBuilder loads it explicitly for Soundwave's
selected planning mode. Operational business workflows live in
`decepticon/workflows/catalog/<id>/WORKFLOW.md` and are selected at runtime.
Domain methods live in the existing Skillogy catalogs under skills/standard/.
Do not add operational role fragments here; they are not auto-loaded.
