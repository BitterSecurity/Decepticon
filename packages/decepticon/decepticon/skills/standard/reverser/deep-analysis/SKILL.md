---
name: deep-analysis
description: "Depth-first RE investigation loop for a single binary or function cluster: decompile→rename→retype→comment→re-read, with context-rot guards and on-task checks. Use when triage has already identified the interesting area and the goal is full understanding: what does function X do, identify cryptographic primitives, locate C2 protocol, recover data structures. Triggers on: 'deep analysis', 'understand function', 'recover struct', 'crypto identification', 'C2 protocol', 'reverse this binary fully', 'what does this function do'."
allowed-tools: Bash Read Write
metadata:
  subdomain: reverse-engineering
  when_to_use: "deep analysis understand function recover struct crypto identification C2 protocol reverse binary fully decompile rename retype comment ghidra"
  tags: reverse-engineering, ghidra, decompilation, static-analysis, deep-re
  mitre_attack: T1027, T1055, T1129
---

# Deep Binary Analysis Loop

Depth-first RE for full understanding of a specific target area. Distinct from
breadth-first triage (`reverser/triage`) — here you don't stop at a signal,
you keep iterating until the function is fully understood.

## Core Principle: Narrow → Deep

Triage finds the surface; deep-analysis excavates one shaft. Commit to one
question at a time. Context-rot (losing track of what you've already renamed
or retyped) is the primary failure mode — guard against it explicitly.

## 1. Entry Point

Before decompiling anything, state the question in writing:

```
kg_add_node("hypothesis", "deep-analysis target",
  props={"question": "<exact question, e.g. 'what does sub_401A20 do'>",
         "binary": "/workspace/target",
         "entry_function": "<hex addr or name>",
         "key": "deep-analysis:entry"})
```

Commit to one question. Do not pivot mid-session unless the current
function is fully annotated and the answer is "it delegates to X".

## 2. Decompile→Rename→Retype→Comment Loop

For each function under analysis, follow this cycle exactly:

### 2a. Decompile
```
Use `ghidra_find_tools(query="decompile")` in hosted runs to discover the native decompiler tool and its argument schema; pass the imported program and target function.
```
Read the full output before touching anything.

### 2b. Rename all locals and parameters
As soon as you understand a variable's purpose, rename it immediately:
```bash
# Via Ghidra MCP batch rename (preferred — one round trip)
# collect all your renames first, then batch:
ghidra_batch_rename(binary="/workspace/target", renames={
  "sub_401A20": "decrypt_config_blob",
  "local_18": "key_len",
  "param_1": "encrypted_buf",
  "param_2": "buf_len"
})
```
Do not leave a decompiled function with placeholder names (`param_1`,
`local_8`, `uVar1`) if you know what they are.

### 2c. Retype parameters and locals
Correct types catch bugs in decompiler reasoning:
```bash
# If param_1 is char* not int
ghidra_retype(binary="/workspace/target", symbol="decrypt_config_blob::param_1", type="char *")
ghidra_retype(binary="/workspace/target", symbol="decrypt_config_blob::local_18", type="uint32_t")
```
Pay special attention to: size_t vs int (sign confusion), pointer-as-int
patterns, struct pointer vs void*, callback function pointers.

### 2d. Add inline comments
At every branch point whose purpose is now understood:
```bash
ghidra_set_comment(binary="/workspace/target", address="0x401A3C",
  comment="XOR-decrypts single byte: key[i % key_len] ^ buf[i]")
```

### 2e. Re-read the decompilation
After rename+retype, decompile again. The output will often look
dramatically cleaner and reveal previously hidden logic.

Repeat 2a–2e until the function's purpose is unambiguous.

## 3. On-Task Check (every 3–5 tool calls)

After each batch of tool calls, answer explicitly:

1. Is the current function still the right place to dig?
2. Have I renamed every local/param I now understand?
3. Have I documented what this function does (even partially) in the KG?
4. Am I still working toward the original question, or have I drifted?

If you've called into a callee 3+ levels deep without updating the KG,
that's context-rot — surface the findings upward before going deeper.

```
kg_add_node("note", "deep-analysis progress",
  props={"function": "decrypt_config_blob",
         "finding": "RC4 variant — XOR stream cipher, 16-byte key from .rdata:0x40D0C0",
         "status": "complete",
         "key": "deep-analysis:decrypt_config_blob"})
```

## 4. Per-Question Strategies

### "What does function X do?"

1. Decompile X.
2. List callees with the registered `ghidra_get_xrefs_from` tool for the imported program; use `ghidra_find_tools` when another reference view is needed.
3. For each callee, check if it's a library stub (look up import table) or
   custom code. Library stubs → infer semantics from function name. Custom
   code → recurse one level.
4. Draw the call path as a KG `calls` edge chain.
5. Answer: input/output contract, side effects, return value semantics.

### "Identify crypto primitive"

High-entropy constants are the fastest path:
```bash
# Search for known crypto constants (AES S-box, RC4 init, ChaCha sigma, etc.)
grep -r "0x63636363\|0x6B617479\|0xE2280613\|0xDB3D18\|0x61707865\|0x3320646E" \
  /workspace/target.bin 2>/dev/null || true

# Capa identifies crypto more reliably
capa /workspace/target --json > /workspace/capa_crypto.json
cat /workspace/capa_crypto.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
for r in d.get('rules', {}).values():
    if 'crypto' in r.get('rule',{}).get('namespace','').lower():
        print(r['rule']['name'])
"
```

Constants to recognize:
| Constant | Algorithm |
|----------|-----------|
| `0x61707865` / `expand 32-byte k` | ChaCha20 / Salsa20 |
| AES S-box start `0x63,0x7c,0x77,0x7b` | AES |
| `0x67452301,0xEFCDAB89` | MD5 init |
| `0x6A09E667,0xBB67AE85` | SHA-256 init |
| `0x9E3779B9` | TEA / XTEA Δ |
| `0xB7E15163,0x9E3779B9` | RC5/RC6 magic |
| Byte-by-byte XOR loop with index-mod key | RC4 or custom XOR |

Once identified, rename the function accordingly and document the key
schedule location if observable.

### "Find C2 protocol / config decryption"

1. Start from network sinks: search imports for `connect`, `WSAConnect`,
   `send`, `write`, `curl_easy_perform`.
2. Follow xrefs upward from the sink to find the function that builds the
   buffer — that's likely the protocol serializer.
3. Look for the decryption stub called at startup or on first network
   connect — it usually reads from a hardcoded blob in `.data` or `.rdata`.
4. Identify config blob location:
   ```bash
   # Find all large-ish .rdata blobs (>16 bytes, high entropy)
   python3 -c "
   import pefile, math
   pe = pefile.PE('/workspace/target.exe')
   for s in pe.sections:
       if b'.rdata' in s.Name:
           data = s.get_data()
           # scan in 16-byte windows
           for off in range(0, len(data)-16, 4):
               chunk = data[off:off+16]
               counts = [chunk.count(bytes([b])) for b in range(256)]
               ent = -sum((c/16)*math.log2(c/16) for c in counts if c)
               if ent > 7.0:
                   print(hex(s.VirtualAddress + off), 'entropy', round(ent,2))
   " 2>/dev/null | head -20
   ```
5. Decompile the decrypt stub. Rename it `decrypt_c2_config`. Document
   algorithm + key in KG.

## 5. Struct Recovery

When you see pointer arithmetic into a struct you don't have a definition for:

1. Note the maximum offset accessed (e.g. `[param_1 + 0x28]`).
2. Create a Ghidra struct: `ghidra_create_struct(name="Config", size=0x30)`.
3. Add fields as you discover their types:
   ```
   ghidra_add_field(struct="Config", offset=0x00, type="char[16]", name="key")
   ghidra_add_field(struct="Config", offset=0x10, type="char[64]", name="c2_host")
   ghidra_add_field(struct="Config", offset=0x50, type="uint16_t", name="c2_port")
   ```
4. Apply the struct type to all uses of param_1.
5. Re-decompile — member names will appear in the pseudocode.

## 6. Context-Rot Prevention Rules

- Never go more than 3 callees deep without writing a KG note.
- If you have renamed 5+ functions, update the entry hypothesis with a
  "state_so_far" field.
- If a function's purpose is "unclear after full loop", mark it `status:blocked`
  and surface to orchestrator rather than spiraling.
- Keep a breadcrumb: before entering a callee, record the parent and
  what you expect to learn from the callee.

## 7. Completion Gate

The deep-analysis session is done when:
- The original question is answered and documented in KG
- All functions touched have at least a rename + one-line comment
- Crypto primitives identified (or confirmed absent)
- C2 config location/format documented (if applicable)
- Struct definitions created for all major data types

```
kg_add_node("finding", "deep-analysis complete",
  props={"question": "<original question>",
         "answer": "<concise answer>",
         "key_functions": ["decrypt_config_blob", "build_beacon_packet"],
         "crypto": "ChaCha20 — key at .data:0x40E0A0",
         "c2_format": "TLV over TCP port 4444",
         "key": "deep-analysis:complete"})
```

## Tools

| Tool | Purpose |
|------|---------|
| Native Ghidra decompiler tool, discovered with `ghidra_find_tools` | C pseudocode for a function |
| `ghidra_batch_rename` | Rename multiple symbols in one call |
| `ghidra_retype` | Fix type annotations |
| `ghidra_get_xrefs_to` / `ghidra_get_xrefs_from` | Callers / callees of an address in hosted runs |
| `ghidra_create_struct` | Define a data structure |
| `capa` | High-level capability + crypto detection |
| `pefile` / `lief` | PE/ELF structure access from Python |
