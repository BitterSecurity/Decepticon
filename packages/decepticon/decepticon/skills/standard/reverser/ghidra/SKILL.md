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

The isolated E2B Ghidra companion is the analysis runtime and the Run tab mirrors its actual CodeBrowser. Import a binary already under `/workspace` with `ghidra_import_file`, then select the program with `ghidra_open_program`. Check `ghidra_analysis_status` before relying on incomplete analysis. Opening the viewer alone creates or displays the project; it does not imply that a binary was imported.

Use the available native `ghidra_` MCP tools. The initial catalog includes `ghidra_get_functions`, `ghidra_find_functions`, `ghidra_get_xrefs_to`, `ghidra_get_xrefs_from`, `ghidra_list_strings`, `ghidra_tool_goto_address`, and `ghidra_get_ui_cursor`. For decompilation, data types, scripts, or other tools not initially listed, call `ghidra_find_tools` with a task-specific query, inspect the returned native name and schema, and call it on the next step. Pass `program` to program-scoped calls. Do not guess tool names or invoke the MCP endpoint with shell or curl.

Address-specific calls should navigate the real Listing and Decompiler through the GUI follow hook. Inspect the returned GUI follow result before saying the operator saw the location. If following failed, report that analysis may have succeeded but visual confirmation did not. Record the binary hash and address with every finding.

## Standalone OSS runtime

A standalone run may expose different local reversing tools, including `ghidra_status` and `ghidra_analyze`. Use only tools actually registered in that runtime. These local tools do not imply that a hosted GUI was updated. Keep standalone output and hosted GUI claims distinct.
