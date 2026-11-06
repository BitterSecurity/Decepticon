# Open OSS PR integration review

Begin at freshly fetched main `44eb9032651c18df3f40e9f19a09e860d74ab43e` and preserve the complete #1160→#1165 ancestry. Review all39changed paths and connected policy/SDK, approved workspace binding, immutable managed startup, execution/Deep Agents compatibility and desktop Chromium consumers. Source review and combined artifact qualification remain in progress; this document makes no merge/release claim.

The original native validator sets and reads tmux state in one command. A separate later `execute_tmux` call now requires the same environment/cwd before checking a different session, addressing #1163’s unresolved review finding. This strengthens evidence without changing kernel policy, readiness deadlines or product execution code. The existing combined affected set passes327cases with process0; current-image/native remeasurement is still pending.

The earlier canonical image is absent from the local cache. Build the unchanged canonical Dockerfile with exact tracked COPY inputs in an isolated snapshot and the shared build slot, preserving source identity and previous historical receipts. Installed-wheel/image source verification and real dedicated mTLS OpenShell admission remain required. Do not reuse an old passing receipt as proof of the new separate-call check.

Known historical limitations remain: the selected0.1.6 gateway refuses a CIDR scope combined with default OSINT as ambiguous; supported hostname tests do not fix that case. Existing E2B browser latency/import samples are not aggregate production performance. Source integration does not publish an OSS version, change SaaS1.2.5, qualify installed Windows distribution, switch production or enable the held consolidated VM conversion.
