---
name: reverser-ghidra
description: Analyze an authorized binary through the active Ghidra runtime and verify navigation in the mirrored GUI.
metadata:
  subdomain: reverse-engineering
  when_to_use: "ghidra binary import functions decompile cross references codebrowser GUI"
  upstream_ref: "NSA Ghidra and the active Decepticon Ghidra tool catalog"
---

# Ghidra analysis

## Hosted engagement

The isolated E2B REA/Ghidra companion is the analysis runtime and the Run tab can mirror its CodeBrowser. The engagement agent discovers a binary under `/workspace` and opens it through `rea_open_binary`; the product pins its source digest and analysis run. Opening the viewer alone does not imply that a binary was analyzed.

Use `rea_find_tools` with a task-specific query to inspect the available native name and schema before calling it. REA provides a source-bound Evidence result and marks unresolved or derived observations. Do not infer tool availability from the full package catalog.

Address-specific calls can navigate the real Listing and Decompiler through the GUI mirror. Inspect the GUI result before saying the operator saw the location. If following failed, keep the REA analysis result and report the lack of visual confirmation separately. Record the binary hash, Evidence ID, and address with every finding.

## Standalone OSS runtime

A standalone run starts the optional `reversing` workload and uses `rea_status`, `rea_find_tools(binary_path, query)`, and `rea_call_tool(binary_path, name, arguments_json)`. The binary must be under the engagement's `/workspace`; no separate upload is needed. The sidecar pins the bytes and persists the original Evidence bundle under `/workspace/.analysis/rea/<runId>/` before delivering a successful result. The complete available Ghidra-session tool set is pageable through `rea_find_tools`. Legacy headless tools remain a fallback when REA is unavailable; standalone output does not imply a hosted GUI update.
