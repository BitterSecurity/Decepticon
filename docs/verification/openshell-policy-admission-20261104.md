# OpenShell admission integration review

Base main `79978a642b7a9c9cbd351150774b869fbb724416`; original #1144 `0744bf78bc28981f10d47d568036dbbca57447dd`. Conflict resolution retains the contributor vouch-gate history and the complete OpenShell-only proposal. Parent and this correction remain unreleased. Qualification is in progress; actual results will replace this note before readiness.

Changes: read effective policy and Loaded ACK through the real SDK authenticated transport, compare full protobufs and runtime identity, reject policy/RoE changes, stop failed admission before the model/tools, pass explicit run config, audit only after successful verification and serialize concurrent admissions. SDK minimum0.1.6 matches the qualified protocol; package inventory otherwise remains unchanged. #1140's old nftables ACK cannot be merged after removing that authority; its failure/retry/concurrency requirements are carried here.

Initial offline regression reproduced a mutable-reference runtime race (151 passed,1failed); snapshot runtime identity before RPC fixes it. First isolated lint attempt also failed formatting. Offline lock refresh lacked cached0.1.6 index metadata; network refresh succeeds without package-version drift. Preserve these failures rather than calling them passes.

Limits: no external model/target requests, release/tag/PyPI publication, QA serving changes or production deployment. Author-reported clean-Linux kernel probes on patched gateway/supervisor are separate from this review's owned loopback protocol/admission tests. Stock mixed-class gateway rejection remains fail-closed. This WSL host's existing mirrored-network seccomp limitation is not bypassed or changed.
