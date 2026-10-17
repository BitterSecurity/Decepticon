---
name: reverser-specialist-methods
description: Evidence-led reverser technical methods and evidence interpretation for the relevant environment.
metadata:
  skillogy:
    version: 1
  subdomain: reverse-engineering
  upstream_ref: "Decepticon standard reverser core prompt and skills/standard/reverser technique references"
  when_to_use: "reverser investigation methodology technical evidence handoff"
---

# Technical methods and evidence

Use the relevant existing technique skills for implementation details. These notes
explain technical prerequisites and evidentiary limits; they do not define an
engagement stage order, dispatch policy, or report lifecycle. Tool availability and
argument names come from the live schema. Authorization comes from the RoE.

## Binary provenance and triage

Record hash, format, architecture, ABI, image base and mitigation metadata before
interpreting disassembly. `bin_identify`, `bin_packer`, `bin_strings` and
`bin_symbols_report` expose complementary facts. High entropy is a packing/encryption
signal, not proof of a particular protector. Packed imports may conceal real code;
unpacking must preserve the original sample and document transformations. Firmware
extraction needs partition offsets and filesystem provenance, not a claim about
every extracted executable.

## Deep analysis and hypotheses

Use REA discovery/call tools and cite their evidence manifests when available;
Radare2 and the existing legacy Ghidra path provide alternatives according to the
actual installation. Focus decompilation on the question's entrypoints and data
flow. Decompiled types and function names may be inferred. ROP gadget availability
does not prove exploitation under PIE/ASLR, NX, CFI or runtime constraints. Existing
virtualized-protectors skills cover VMEnter/VMEXIT/VIP and incremental lifting;
Windows-internals and game-security skills define their specialized research
contexts. Malware network strings are IOCs to investigate, not proven live C2.
