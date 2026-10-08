---
name: reverser-overview
description: Root pointer for the binary reversing lane. Covers triage, Radare2 fallback, string extraction, packer unpacking, virtualized protectors, symbol risk, ROP, Ghidra deep analysis, and firmware extraction.
metadata:
  subdomain: reverse-engineering
  when_to_use: "reverser binary reversing triage strings packer unpack rop ghidra firmware VMProtect VMP2 Themida virtualized protectors overview routing"
  upstream_ref: "Decepticon reverser lane catalog — Ghidra, Radare2, Back Engineering VMProtect/Themida research, AFL++, libFuzzer, binwalk, and binary triage tooling"
  capability_contract:
    lane: reverse-engineering
    scope: isolated-lab
    environment: [analysis-container, disposable-vm]
    required_tools: [ghidra, radare2, sanitizer]
    evidence: [binary-sha256, function-map, dynamic-trace, minimized-input]
    verification: "reproduce the claimed behavior against the pinned binary hash"
    negative_control: "run a benign corpus input through the same harness"
    scorecard: [reproducer-rate, root-cause-rate, artifact-completeness]
    benchmark: held-out-reversing
---

# Reverser Skill Catalog

## Playbooks
| Skill | Use for |
|---|---|
| `/skills/standard/reverser/triage/SKILL.md`            | First-pass ELF/PE/Mach-O triage |
| `/skills/standard/reverser/firmware/SKILL.md`          | Router / IoT firmware extraction |
| `/skills/standard/reverser/packer-unpacking/SKILL.md`  | UPX / ASPack / Themida / VMProtect |
| `/skills/standard/reverser/virtualized-protectors/SKILL.md` | VMProtect / VMP2 / Themida workflow |
| `/skills/standard/reverser/rop-chain/SKILL.md`         | Gadget hunting for exploit dev |
| `/skills/standard/reverser/anti-debug-bypass/SKILL.md` | IsDebuggerPresent, ptrace, NtGlobalFlag |
| `/skills/standard/reverser/ghidra/SKILL.md`            | Deep Ghidra analysis — decompile, xrefs, imports, P-code |
| `/skills/standard/reverser/windows-internals/SKILL.md` | Defensive driver exposure assessment in disposable Windows VMs |
| `/skills/standard/reverser/game-security/SKILL.md`     | Self-hosted game client, protocol, replay, and server-authority research |

## Workflow
1. Check which reversing tools the active runtime exposes. Hosted runs use an isolated Ghidra MCP companion and mirrored GUI; standalone OSS may expose local analysis tools.
2. `bin_identify` — format, arch, NX/PIE
3. `bin_packer` — entropy + signature
4. If packed → follow the packer-unpacking skill, re-identify after unpack
5. `bin_strings` — category=url/ip/crypto/secret/version to seed the graph
6. `bin_symbols_report` — risk bucket classification
7. Version strings → `cve_lookup` + `cve_by_package`
8. In hosted runs, import the `/workspace` binary with `ghidra_import_file`, open the program, and inspect `ghidra_analysis_status`; discover additional native tools with `ghidra_find_tools` when needed.
9. Inspect functions and cross-references with the registered `ghidra_` MCP tools. Check GUI follow status for address-specific results. In standalone OSS, use only local reversing tools actually registered, including `bin_r2_script` as a Radare2 fallback when available.
10. Record every observation in the knowledge graph
