# REA reverse engineering in OSS

The Reverser agent uses REA 6.3.0 with the Ghidra 12.1.4 provider when the optional reversing workload is configured. REA is the analysis and Evidence layer around Ghidra; it does not replace Ghidra's decompiler. The workload starts on demand when the agent decides a binary found in its engagement workspace needs deeper static analysis.

The binary must already be available under `/workspace`. The agent can locate it during its normal work; a separate user upload is not required. When REA is available, the Reverser checks `rea_status`, calls `rea_find_tools` for that binary to discover the Ghidra-session tools and their full schemas, then calls a selected tool through `rea_call_tool`. These three agent-facing tools let the agent use the session-available REA catalog without exposing every provider's tool as a separate top-level agent tool.

REA pins the binary bytes for its session. The OSS gateway saves the original Evidence bundle and an integrity manifest under `/workspace/.analysis/rea` before it delivers a successful result to the agent. The agent can cite the Evidence ID and manifest path in its findings. Evidence records identify the analyzed source and provider and distinguish observations from derived results and limitations; they are analysis records, not automatic vulnerability findings.

The shipped workload provides the Ghidra provider. Other REA providers require their own installed engines and are not implied by this OSS setup. REA performs static analysis; its result does not prove a runtime path executed. The OSS path does not mirror an agent-driven Ghidra GUI or preserve a user-edited Ghidra project. If the REA workload is unavailable, the Reverser can continue with its local radare2 and binary triage tools.

See the [agent roster](../agents.md), [architecture](../architecture.md), and [changelog](../../CHAGELOG.md) for the surrounding OSS workflow.
