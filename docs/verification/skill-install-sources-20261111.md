# Standard skill installation sources — 2026-11-11

Scope: five standard skills (NetExec, OPC-UA, PROFINET, LoRaWAN OTAA and
Flutter). Source baseline: `0632492d193cbebb345135f5f4898f016e91daf1`.
No package publication, serving catalog migration or production deployment.

## Source decisions

- NetExec follows the [publisher installation guide](https://www.netexec.wiki/getting-started/installation/installation-on-unix), using its Git repository rather than an index name. The [v1.5.1 release](https://github.com/Pennyw0rth/NetExec/releases/tag/v1.5.1) tag resolved through `git ls-remote` to `c7dc286ba65daf10402cdc470e531b84e6d3d911`; installation pins that commit. This establishes source/version selection, not an audit of every transitive dependency.
- OPC-UA retains the publisher's existing [asyncua package](https://pypi.org/project/asyncua/). Discovery uses its documented [connect_and_get_server_endpoints](https://opcua-asyncio.readthedocs.io/en/latest/api/asyncua.client.html#asyncua.client.client.Client.connect_and_get_server_endpoints) API instead of an unsupported module/NSE recipe. The previous context manager activated a session despite the example's claim; the replacement temporary-channel helper does not.
- PROFINET retains the existing [Scapy](https://pypi.org/project/scapy/) path; the unused alternative installation and reference are removed.
- LoRaWAN's offline JoinRequest example needs no dependency. It checks the complete 23-byte PHY payload/MHDR, decodes EUI wire order and little-endian DevNonce, and explicitly leaves MIC authentication unverified. Field layout cross-checked against [Scapy's decoder](https://github.com/secdev/scapy/blob/master/scapy/contrib/loraphy2wan.py). The fixture is synthetic, not a claimed capture.
- Flutter removes unsupported parser installation and suppressed failure, retaining version-specific analysis guidance. No replacement package name is guessed.

## Local verification

Use current source roots in `PYTHONPATH` and the existing read-only Python3.13
test environment; do not synchronize or mutate another task's environment.

- `python -m decepticon.skill_audit --mode strict`: 329 files, zero violations.
- `pytest -q packages/decepticon/tests/unit/skill_audit packages/decepticon/tests/unit/capabilities packages/decepticon/tests/unit/tools/test_skills_loader.py`: 133 passed; three existing deprecation warnings.
- Rebuilt the catalog with `python -m decepticon.skillogy.builder --frozen-built-at`, then `--check`: 2,140 nodes / 5,763 edges, exact match. Exactly the five skill node property lines change; identities/relationships stay intact.
- Eight changed fenced examples pass Bash/Python syntax checks without executing installation, target scans or device commands. A broader syntax attempt also encountered an existing NetExec interactive-console transcript; this is not presented as executable Bash or a full corpus syntax pass.
- Executed only the dependency-free offline parser: independently expected EUI/nonce/MIC values, four nonce boundaries (0,255,256,65535), seven invalid hex/length/MHDR cases pass.
- Built an actual framework wheel with `uv build --package decepticon --wheel` (build-only Hatchling1.33.0 constraint). ZIP readback matches all five source skills and the rebuilt graph byte-for-byte. No reported unsafe install command remains in packaged Markdown/catalog text. The local sentinel0.0.0 wheel is a packaging check, not a published release or selected SaaS runtime.
- `git diff --check` passes. No assessment command, package installation from these instructions, credential read or external report was performed.

Existing installed packages, images and loaded graph catalogs retain their old
contents until upgraded through their normal publication/synchronization path.
This source correction does not establish compromise, registration availability
of any missing index name, or safety of unrelated skill dependencies. Final SaaS
source cohorts must be rebuilt from the merged correction before release.
