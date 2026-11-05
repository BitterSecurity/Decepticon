"""Patch generation + verification — Stage 4 of the vulnresearch pipeline.

The patcher agent uses the two tools exposed here to record a proposed
fix and to prove (or disprove) that the fix actually closes the
vulnerability. Both tools persist state through the existing
:class:`KnowledgeGraph` so the orchestrator can query "which findings
are still unpatched?" with a plain ``kg_query(kind='patch')``.

Data model
----------
- ``NodeKind.PATCH`` stores ``{vuln_id, finding_id, diff, commit_message,
  status, tests_passed, poc_still_fires, created_at, verified_at}``.
- A ``PATCHES`` edge links ``patch → vulnerability`` (and optionally
  ``patch → finding`` when a specific validated finding is being fixed).

``status`` lifecycle:
    proposed  → patch_propose just recorded the diff
    verified  → patch_verify re-ran the PoC and it no longer fires
    regressed → patch_verify found the PoC still fires OR tests failed

Verification
------------
``patch_verify`` re-runs the *original* PoC command (the one that
stage-3 :func:`~decepticon.research.tools.validate_finding` used) inside
the same sandbox via :func:`decepticon.research.poc.sandbox_runner`. If
none of the original success signals fire, the patch is accepted and the
linked vulnerability severity is flipped to ``info`` with a ``patched``
prop. If the signals still fire, the patch is rejected and the
vulnerability is left untouched so the patcher can iterate.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from langchain_core.tools import tool

from decepticon.tools.research._state import _json, _load, _save
from decepticon.tools.research.poc import sandbox_runner
from decepticon_core.types.kg import Edge, EdgeKind, Node, NodeKind, Severity
from decepticon_core.utils.logging import get_logger

log = get_logger("research.patch")


def _hash_diff(diff: str) -> str:
    return hashlib.sha1(diff.encode("utf-8", errors="replace"), usedforsecurity=False).hexdigest()[
        :16
    ]


@tool
def patch_propose(
    vuln_id: str,
    diff: str,
    commit_message: str,
    finding_id: str = "",
    applied: bool = False,
) -> str:
    """Record a proposed fix diff for a verified vulnerability.

    WHEN TO USE: Stage 4. After reading the source around a
    ``VULNERABILITY`` node that has at least one ``VALIDATES`` finding,
    craft a minimal diff, and call this tool BEFORE applying it to disk
    so the proposal is captured in the graph even if apply fails.

    The recorded ``PATCH`` node carries a deterministic dedup key of
    ``"patch::{vuln_id}::{sha1(diff)}"``, so re-proposing the same diff
    is idempotent. Set ``applied=True`` only after you've actually
    written the diff to the target files via Edit/bash — the orchestrator
    uses this flag to decide whether :func:`patch_verify` needs to run
    the PoC at all.

    Args:
        vuln_id: Graph id of the vulnerability being fixed. MUST already
            have ``validated=True`` set by stage-3 validate_finding.
        diff: Unified-diff text of the proposed fix. Keep it minimal —
            no unrelated formatting, no doc changes, no new abstractions.
            Use ``git diff --no-color`` output, or a hand-written hunk.
        commit_message: Conventional-commit style summary, e.g.
            ``"fix(auth): use constant-time comparison in verify_hmac"``.
        finding_id: Optional id of a specific ``FINDING`` node this patch
            targets (when the vuln has multiple validated findings).
        applied: True if the diff has already been written to disk.

    Returns:
        JSON with the patch node id, the linked vuln id, and graph stats.
    """
    graph, path = _load()
    vuln = graph.nodes.get(vuln_id)
    if vuln is None:
        return _json({"error": f"vuln node not found: {vuln_id}"})
    if vuln.kind != NodeKind.VULNERABILITY:
        return _json({"error": f"node {vuln_id} is kind={vuln.kind.value}, expected vulnerability"})
    if not vuln.props.get("validated"):
        # Not fatal — we still record, but flag it so the orchestrator can triage.
        log.warning("patch_propose: vuln %s has no validated=True flag", vuln_id)

    diff_hash = _hash_diff(diff)
    label = f"patch: {commit_message[:80]}"
    node = Node.make(
        NodeKind.PATCH,
        label,
        key=f"patch::{vuln_id}::{diff_hash}",
        vuln_id=vuln_id,
        finding_id=finding_id or None,
        diff=diff[:16384],  # cap so the KG file stays small
        diff_hash=diff_hash,
        diff_bytes=len(diff.encode("utf-8", errors="replace")),
        commit_message=commit_message,
        applied=bool(applied),
        status="proposed",
        created_at=time.time(),
    )
    graph.upsert_node(node)
    graph.upsert_edge(Edge.make(node.id, vuln_id, EdgeKind.PATCHES, weight=0.5))
    if finding_id and finding_id in graph.nodes:
        graph.upsert_edge(
            Edge.make(node.id, finding_id, EdgeKind.PATCHES, weight=0.5, key="finding")
        )
    _save(graph, path)
    return _json(
        {
            "id": node.id,
            "vuln_id": vuln_id,
            "diff_hash": diff_hash,
            "status": node.props["status"],
            "stats": graph.stats(),
        }
    )


@tool
async def patch_verify(
    patch_id: str,
    poc_command: str,
    success_patterns: str,
    test_cmd: str = "",
) -> str:
    """Re-run the PoC against the patched target and verify the fix holds.

    WHEN TO USE: Stage 4, immediately after the patch has been written to
    disk. This tool is the single source of truth for "is the vuln
    actually fixed?" — the patcher agent MUST NOT claim completion
    without a green ``patch_verify``.

    Verification steps:
      1. Run ``test_cmd`` inside the sandbox (if provided). Any non-zero
         exit code marks the patch as ``regressed`` and the tool returns
         early without touching the vulnerability node.
      2. Run ``poc_command`` (usually the same string stage-3 verifier
         used) inside the sandbox and check whether any of the
         ``success_patterns`` still match. If none match, the patch is
         ``verified`` and the underlying vulnerability severity is flipped
         to ``info`` with a ``patched=True`` marker.

    The original vulnerability node and its finding history are
    preserved (audit trail); only ``severity`` and two bookkeeping props
    change on success.

    Args:
        patch_id: Graph id of the ``PATCH`` node from :func:`patch_propose`.
        poc_command: The bash PoC that originally demonstrated the bug.
            Usually the same string used by ``validate_finding`` at
            stage 3 — copy it from the finding's stdout log or the
            verifier's agent transcript.
        success_patterns: Comma-separated regexes that indicate the
            exploit still works. Identical format to ``validate_finding``.
        test_cmd: Optional regression/test command to run first (e.g.
            ``"cd /workspace/target && pytest -q tests/security/"``).
            A non-zero exit aborts verification.

    Returns:
        JSON with fields ``status`` (verified/regressed/tests_failed),
        ``poc_still_fires`` (bool), ``tests_passed`` (bool or null),
        ``signals`` (list of matched patterns), and ``stdout_excerpt``.
    """
    import re as _re

    from decepticon.tools.bash.bash import get_sandbox

    sandbox = get_sandbox()
    if sandbox is None:
        return _json({"error": "HTTPTransportSandbox not initialized"})

    graph, db_path = _load()
    patch = graph.nodes.get(patch_id)
    if patch is None or patch.kind != NodeKind.PATCH:
        return _json({"error": f"patch node not found: {patch_id}"})
    vuln_id = patch.props.get("vuln_id")
    vuln = graph.nodes.get(vuln_id) if vuln_id else None
    if vuln is None:
        return _json({"error": f"linked vuln not found: {vuln_id}"})

    runner = sandbox_runner(sandbox)

    patterns = [p.strip() for p in success_patterns.split(",") if p.strip()]
    result: dict[str, Any] = {
        "patch_id": patch_id,
        "vuln_id": vuln_id,
        "tests_passed": None,
        "poc_still_fires": None,
        "signals": [],
        "stdout_excerpt": "",
        "status": "regressed",
    }

    # Step 1: optional test suite.
    if test_cmd:
        t_out, t_err, t_code = await runner(test_cmd)
        tests_passed = t_code == 0
        result["tests_passed"] = tests_passed
        if not tests_passed:
            result["status"] = "tests_failed"
            result["stdout_excerpt"] = (t_out + t_err)[:800]
            patch.props.update(
                {
                    "status": "regressed",
                    "tests_passed": False,
                    "verified_at": time.time(),
                }
            )
            patch.updated_at = time.time()
            _save(graph, db_path)
            return _json(result)

    # Step 2: re-run the PoC.
    p_out, p_err, p_code = await runner(poc_command)
    combined = f"{p_out}\n{p_err}"
    matches: list[str] = []
    for pat in patterns:
        try:
            if _re.search(pat, combined, _re.DOTALL | _re.IGNORECASE):
                matches.append(pat)
        except _re.error:
            if pat.lower() in combined.lower():
                matches.append(pat)

    # Step 3: crash-vs-fix detection.
    # If success patterns didn't match, check whether the target crashed
    # rather than genuinely being fixed — a 5xx / traceback / non-zero
    # exit is not proof of remediation.
    _ERROR_INDICATORS = ("500", "Internal Server Error", "Traceback", "FATAL", "panic:")
    if not matches:
        looks_like_crash = p_code != 0 or any(ind in combined for ind in _ERROR_INDICATORS)
        if looks_like_crash:
            log.warning(
                "patch_verify: vuln %s — PoC patterns absent but response "
                "looks like a crash (exit=%d). Verify the fix is genuine, "
                "not a masked failure.",
                vuln_id,
                p_code,
            )
            result["crash_warning"] = True
    still_fires = bool(matches)
    result["poc_still_fires"] = still_fires
    result["signals"] = matches
    result["stdout_excerpt"] = combined[:800]
    result["poc_exit_code"] = p_code

    now = time.time()
    if still_fires:
        result["status"] = "regressed"
        patch.props.update(
            {
                "status": "regressed",
                "poc_still_fires": True,
                "verified_at": now,
            }
        )
    else:
        result["status"] = "verified"
        patch.props.update(
            {
                "status": "verified",
                "poc_still_fires": False,
                "verified_at": now,
            }
        )
        # Flip the vuln to patched/info while preserving the original
        # severity so reporting can still show the "at worst it was" grade.
        original_sev = vuln.props.get("severity")
        if original_sev and original_sev != Severity.INFO.value:
            vuln.props["severity_before_patch"] = original_sev
        vuln.props["severity"] = Severity.INFO.value
        vuln.props["patched"] = True
        vuln.props["patched_by"] = patch_id
        vuln.props["patched_at"] = now
        vuln.updated_at = now

    patch.updated_at = now
    _save(graph, db_path)
    return _json(result)


PATCH_TOOLS = [patch_propose, patch_verify]
