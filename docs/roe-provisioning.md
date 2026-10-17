# Provision an RoE for an existing engagement

An authorized operator provisions `plan/roe.json`. Soundwave can draft a
candidate such as `plan/roe-authorized.json`, but its filesystem tools cannot
create or modify the operator-confirmed `plan/roe.json`. The CLI launcher
creates an empty planning workspace; the web creation form provisions a direct
RoE only for new engagements. Neither recreating the engagement nor restarting
its services is required to import a missing RoE.

## Operator procedure

1. Pause active work and select Plan. Review the candidate against the signed
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

4. Continue the same Plan conversation (or `/resume` if paused). Ask Soundwave
   to read the newly provisioned RoE and validate the existing planning bundle.
   The other seven documents and conversation are preserved. Their
   `engagement_name` must agree with the RoE. Review the validated bundle and
   use `/mode red approve` separately when ready for execution.

The importer validates before writing, installs the reviewed bytes atomically
with mode `0600`, and refuses an existing RoE or a symlinked `plan` directory.
It clears stale `.planning-draft-ready` and `.red-approved` markers before
publishing a restored document, so old approval cannot silently become active.
Invalid input exits with status 2. Successful provisioning exits with status 0.
It does not expose an agent tool or an HTTP endpoint. Existing RoE revisions
remain an operator task outside Plan; this command is deliberately create-only.

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
  "machine_enforcement": {"mode": "enforce", "in_scope": ["127.0.0.1"]}
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
separately: without that block the runtime defaults to audit-only, and an invalid
mode also falls back to audit. Provisioning does not change those runtime rules.
