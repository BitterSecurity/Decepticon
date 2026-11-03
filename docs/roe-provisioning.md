# Provision an RoE for an existing engagement

An authorized operator provisions `plan/roe.json`. Soundwave can draft a
candidate such as `plan/roe-authorized.json`, but its filesystem tools cannot
create or modify the operator-confirmed `plan/roe.json`. New CLI engagements
generate their machine RoE from reviewed targets/exclusions
and open Red immediately. This import procedure is for legacy or externally
created workspaces with missing scope, not a prerequisite for normal creation.
The web creation form provisions a direct
RoE only for new engagements. Neither recreating the engagement nor restarting
its services is required to import a missing RoE.

## Portable terminal procedure

Pause active work, review the candidate against the signed authorization, then
use the operator command inside the existing engagement:

```text
/scope import plan/roe-authorized.json --confirm
/mode red
```

The source can be relative to the workspace or an absolute path readable by the
CLI. Import uses the canonical operator validator and creates only missing
`plan/roe.json`, preserving the reviewed bytes and refusing an existing file or
symlinked destination. The `plan` directory must be real. The terminal importer
can create that directory; the Python command requires it already to exist.

Red enforces the installed machine block. `/mode red approve` and the Python
`--approve-red` flag are retired; neither is supported by current main. No extra
approval marker or optional Plan bundle is required after scope creation.

`POST /_decepticon/roe/validate` validates candidate bytes without writing files.
The CLI refuses redirects; use the final server URL. Operator import is outside
the agent tool surface and is not an authorization bypass.

## Operator procedure

1. Pause active work. Review the candidate against the signed
   authorization: targets, exclusions, dates, permitted actions, contacts, and
   machine enforcement. The command's confirmation is your attestation; it
   cannot establish legal authorization from an arbitrary reference string.
2. On the host, place the reviewed JSON at
   `${DECEPTICON_HOME:-$HOME/.decepticon}/workspace/itps/plan/roe-authorized.json`.
   Use the actual engagement directory instead of `itps`.
3. In a second host terminal, run the command in the **running LangGraph
   container**. For an installed launcher stack:

   ```bash
   cd "${DECEPTICON_HOME:-$HOME/.decepticon}"
   docker compose exec -T -e DECEPTICON_SKIP_BOOT=1 langgraph \
     python -m decepticon.provision_roe \
     --workspace /workspace \
     --file /workspace/plan/roe-authorized.json --confirm
   ```

   The launcher mounts the selected engagement at `/workspace`. With a raw
   development Compose stack that mounts the entire workspace tree, use
   `--workspace /workspace/itps --file /workspace/itps/plan/roe-authorized.json`.
   Check the running container's mount before choosing the path; do not create a
   second engagement to work around a path error. Use the same Compose project
   options as your installation if you override its project name or files.

   From a source checkout with dependencies installed, the equivalent is:

   ```bash
   DECEPTICON_SKIP_BOOT=1 uv run python -m decepticon.provision_roe \
     --workspace /absolute/path/to/existing/itps \
     --file /absolute/path/to/reviewed-roe.json --confirm
   ```

4. Select `/mode red` to execute under the installed scope, or `/mode plan`
   for optional discussion/documents. Soundwave cannot edit protected scope or
   own execution OPPLAN. A missing machine block still causes gated execution
   to fail, even if a human planning RoE passed the importer’s schema validator.

The Python importer validates reviewed bytes before create-only publication;
existing `plan/roe.json` is refused. It neither overwrites scope nor adds an
approval marker. Invalid input exits with status 2. Successful creation exits
with status 0. There is no HTTP mutation endpoint for this operation.

## Required JSON and validation boundary

The importer uses the same canonical `RoE` model as planning completion.
Required fields are `engagement_name`, `client`, `start_date`, `end_date`,
`engagement_type`, and `testing_window`. Provisioning additionally requires a
nonblank `authorization_reference`, a nonblank `engagement_name`, and at least
one `in_scope` entry with a nonblank `target`. Each scope entry requires a
`target` and a `type`; `notes` is optional. `engagement_type` is `external`,
`internal`, `hybrid`, `assumed-breach`, or `physical`.

For example, adapt this local-only scope to your actual signed authorization:

```json
{
  "engagement_name": "itps",
  "client": "Local test operator",
  "start_date": "2026-10-17",
  "end_date": "2026-10-17",
  "engagement_type": "external",
  "testing_window": "09:00-17:00 UTC",
  "in_scope": [{"target": "127.0.0.1", "type": "ip-range"}],
  "out_of_scope": [],
  "authorization_reference": "Signed authorization reference",
  "machine_enforcement": {"in_scope": ["127.0.0.1"]}
}
```

Export all canonical fields, defaults, and nested schemas with:

```bash
docker compose exec -T -e DECEPTICON_SKIP_BOOT=1 langgraph \
  python -m decepticon.provision_roe --schema > roe.schema.json
```

The canonical model treats dates and scope kinds as strings and ignores
extensions during validation. Import preserves the **original bytes**, including
`machine_enforcement`; it does not normalize or silently discard extensions.
The command validates the planning schema, not the semantics of signed
contracts, dates, scope patterns, or machine enforcement. Review the
[machine enforcement schema and behavior](security/roe-machine-enforcement.md)
separately: missing or non-object machine configuration causes gated calls to
be refused with `ROE_CONFIG_MISSING`. Legacy `mode` keys are ignored; they no
longer select audit-only or warning-only execution. A present empty block still
has an empty allowlist, so review actual scope rules. Provisioning does not
replace the runtime guardrail; on OpenShell the admitted policy hash is verified against the compiled RoE every turn (`egress_provisioned` audit event).
