# Local finding evidence contract

`decepticon.tools.reporting.evidence_contract` validates references in local
finding/report documents. It accepts the existing `evidence_pointer` format and
optional structured metadata adapted from the SaaS finding document model.

Run against a local engagement workspace on Linux/WSL:

```sh
DECEPTICON_SKIP_BOOT=1 python -m decepticon.tools.reporting.evidence_contract \
  --root ~/.decepticon/workspace/example findings/FIND-001.md
```

The command prints JSON containing `valid`, `references`, and `errors`. Exit 0
means the contract passes; exit 1 means it fails. Library callers can use
`validate_finding_document(root: Path, document: str)` or
`validate_evidence_metadata(root: Path, metadata: object)`.

## Authored metadata

```yaml
---
id: FIND-001
evidence_pointer: findings/evidence/FIND-001-response.txt
evidence_manifest:
  - path: findings/evidence/FIND-001-response.txt
    label: Response log
    role: observation
    supports: The service returned HTTP 403
attack_steps:
  - label: Observe response
    status: observed
    evidence_refs:
      - findings/evidence/FIND-001-response.txt
---
```

The manifest is optional. Each reference supplies `path`, `label`, `role`, and
nullable `supports`; missing label/role default to the filename/`artifact`.
Legacy pointers and step references are deduplicated by path, preserving manifest
metadata. Duplicate manifest paths are errors. Legacy text steps remain accepted.
Structured steps accept `observed` or `untested` status. These are caller-authored
descriptions, never independent validation of an observation.

## Filesystem contract

Documents must be Markdown under `findings/` or `report/`. References must be
inside `findings/evidence/`, `recon/evidence/`, `exploit/evidence/`, or the existing
OSS `evidence/` tree (including terminal recordings). No extension allowlist is
imposed on evidence; the validator does not execute, render, or read its content.

Paths are canonical relative POSIX paths. Absolute paths, empty/`.`/`..`
segments, backslashes, whitespace, URL encoding, fragments, query strings,
control characters and colon are rejected. Paths are never decoded or normalized
into acceptable input. The caller-selected root is resolved once per open;
symlinks in every component beneath that root, including links within the
workspace, are rejected using directory-relative `O_NOFOLLOW` opens. Evidence
must be an accessible regular file. Missing files, directories, sockets and
FIFOs fail validation. Descriptor traversal protects against symlink replacement
during open. The implementation targets Linux/WSL, where these OS flags exist.

A finding document is limited to 1 MiB; manifests to 100 entries; structured
steps to 30; references per step to 100; metadata text to 2000 characters; paths
to 1024 characters. Oversized or malformed values produce errors. YAML uses a
safe loader and rejects duplicate/non-string mapping keys. Markdown without
frontmatter remains readable and returns no declared references.

## Interpretation and limits

`valid` means declared references were well formed and accessible when checked.
It does not prove the finding, verify a claim in `supports`, or establish artifact
integrity. Callers must inspect `errors`; returned references can include
unavailable files when `valid` is false. Files may change after validation;
consumers must perform their own safe open when later reading evidence. The
validator does not scan Markdown links, infer ownership from filenames, inspect
SARIF/KG documents, discover an entire directory tree, or promote findings.
This is an opt-in public reporting contract; agent execution and existing report
exporters are not wired to it. Future Blue Cell/report consumers can reuse the
structured references without treating authored metadata as trusted telemetry.
