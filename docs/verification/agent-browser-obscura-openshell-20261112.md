# Agent browser (Obscura) under a native OpenShell RoE policy — 2026-11-12

Scope: the `browser_*` runner command, the Obscura provider and the sandbox
image on a real OpenShell gateway. No model calls, no SaaS, no release.

## Environment

* Ubuntu 24.04 VM (8 vCPU), Docker 29.1.3, OpenShell gateway/CLI **0.1.6**
  (Debian package, checksum-verified installer), docker compute driver,
  gateway bound to `127.0.0.1:17670` with mTLS.
* Sandbox image built from this branch: `containers/sandbox.Dockerfile`
  (default build args), Obscura `0.1.0-dev+3bae609` built in the
  `obscura-build` stage (`--locked --features render`, ~4 min cold).
* Policy: produced by `compile_openshell_policy` for
  `machine_enforcement.in_scope = ["host.docker.internal"]` with
  `DECEPTICON_OPENSHELL_ROE_PORTS=<allowed port>`; default binaries `**`.
* Fixtures: two host HTTP listeners, allowed port 38123 (in policy) and
  denied port 38124 (not in policy). The allowed page runs
  `fetch("http://host.docker.internal:38124/...")` and records the outcome.

The runner was invoked through `openshell sandbox exec` with the exact
command the agent tools send (`python3 -I
/usr/local/lib/decepticon-sandbox/decepticon/sandbox_browser/__main__.py call
--workspace /workspace --provider obscura --request-b64 ...`), as UID 1000.

## Results

| Check | Result | Supervisor (OCSF) evidence |
|---|---|---|
| navigate in scope | OK, title read, allowed listener hit 1 | `NET:OPEN ALLOWED /usr/local/bin/obscura(0) -> host.docker.internal:38123 [policy:roe_in_scope engine:opa]` |
| page script fetch to out-of-scope port | page observed `denied`; denied listener hits 0 | `NET:OPEN DENIED /usr/local/bin/obscura(0) -> 198.18.0.2:38124 [reason:transparent_tcp_mapping_denied]` |
| direct navigate out of scope (port) | `action_failed` (connect EACCES) | same DENIED record |
| direct navigate out of scope (public host `example.org`) | `action_failed` | `NET:REFUSE DENIED example.org [reason:policy_dns_ineligible]`, `NET:OPEN DENIED ... example.org:443 [reason:transparent_tcp_policy_denied]` |
| wait / snapshot / screenshot | OK; PNG at `/workspace/evidence/browser/…png`, owned by UID 1000 | — |
| shutdown | browser server stopped | — |

No proxy variables were present in the sandbox; OpenShell enforced the
browser through policy DNS and transparent TCP.

## Failures observed and fixed during this measurement

1. `python3 -m decepticon.sandbox_browser` failed with `No module named
   'decepticon'`: managed exec does not inherit the image `PYTHONPATH`.
2. Running the bundle script then failed in `decepticon/__init__.py`
   (`cannot import name '_boot'`): `DECEPTICON_SKIP_BOOT` is also not
   inherited. The runner now runs as an isolated script from the immutable
   bundle and sets the flag itself.
3. The runner first rejected `--workspace /workspace`; managed sessions bind
   the engagement at `/workspace` itself.

## Limits

* `tests/validation/managed_native.py --agent-browser` was added but its
  baseline (`curl` to the allowed listener) cannot pass on native Linux
  Docker: the OpenShell docker driver runs the supervisor in the host
  network and maps `host.docker.internal` to `127.0.0.1`, while the validator
  requires a non-loopback bridge fixture address. The measurement above uses
  the same gateway, policy compiler and image through the CLI instead.
* The `web_fetch` engine is started the same way
  (`python3 -m decepticon.sandbox_web`) and is therefore expected to hit
  failures 1–2 in managed sessions. Not measured or changed here.
* Local component observation only; no E2B, SaaS or production claim.
