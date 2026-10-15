"""The report checker rejects unsupported status and score claims."""

from __future__ import annotations

import json
from inspect import signature
from types import SimpleNamespace
from typing import Annotated, TypedDict

from deepagents.backends.filesystem import FilesystemBackend
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from decepticon.tools.reporting.finding_quality import (
    build_finding_quality_tool,
    check_finding_content,
)


def finding(**overrides):
    meta = {
        "id": "FIND-001",
        "result_kind": "vulnerability",
        "severity": "high",
        "verification_status": "verified",
        "verification_rationale": "Positive differs from baseline",
        "report_status": "draft",
        "evidence_pointer": "findings/evidence/proof.txt",
        "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "cvss_score": 9.8,
        "cvss_version": "3.1",
    }
    meta.update(overrides)
    front = "\n".join(f"{key}: {value}" for key, value in meta.items() if value is not None)
    return f"---\n{front}\n---\n## Summary\nObserved issue\n## Verification\nPositive and negative\n## Impact\nObserved impact\n## Remediation\nFix\n"


def check(text, exists=lambda _path: True):
    return check_finding_content("findings/FIND-001.md", text, evidence_exists=exists)


def test_valid_saved_finding():
    assert check(finding()) == []


def test_rejects_missing_evidence_and_score_mismatch():
    issues = check(finding(cvss_score=8.8), exists=lambda _path: False)
    assert any("CVSS score" in issue for issue in issues)
    assert any("missing evidence" in issue for issue in issues)


def test_rejects_false_positive_scored_as_vulnerability():
    issues = check(finding(verification_status="false_positive"))
    assert any("false-positive" in issue for issue in issues)


def test_verified_negative_result_is_coverage_without_cvss():
    text = finding(result_kind="negative_result", cvss_vector=None, cvss_score=None)
    assert check(text) == []


def test_rejects_duplicate_frontmatter_key():
    text = finding().replace("id: FIND-001", "id: FIND-001\nid: FIND-002", 1)
    assert any("frontmatter" in issue for issue in check(text))


def test_agent_tool_reads_only_active_workspace(tmp_path):
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    backend.write("/workspace/findings/FIND-001.md", finding())
    backend.write("/workspace/findings/evidence/proof.txt", "positive and baseline")
    tool = build_finding_quality_tool(backend)
    runtime = SimpleNamespace(state={"workspace_path": "/workspace"})
    result = json.loads(tool.func("findings/FIND-001.md", runtime))
    assert result == {"valid": True, "errors": []}
    blocked = json.loads(tool.func("../FIND-001.md", runtime))
    assert blocked["valid"] is False


def _tool_runtime():
    return ToolRuntime(
        state={"workspace_path": "/workspace"},
        context={},
        config={},
        stream_writer=lambda *args, **kwargs: None,
        store=None,
        tool_call_id="call-1",
    )


def test_runtime_annotation_resolves_to_toolruntime():
    """The raw `runtime` annotation must be the class langchain inspects.

    Regression: `from __future__ import annotations` stringifies annotations.
    LangChain's injected-argument detection reads the raw signature rather than
    resolving type hints, so a string annotation hides `runtime` from its
    injected-argument tracking even though other resolvers compensate. The
    agent-facing schema must also keep `runtime` out of model-callable args.
    """
    quality_tool = build_finding_quality_tool(None)
    assert isinstance(quality_tool, StructuredTool) and quality_tool.func is not None
    assert signature(quality_tool.func).parameters["runtime"].annotation is ToolRuntime
    assert "runtime" not in quality_tool.args


def test_runtime_injected_through_public_invoke_path(tmp_path):
    """`runtime` must survive the public `invoke` path used by `ToolNode`.

    ToolNode places injected values in the tool call's `args` before invoking;
    exercising `invoke` here pins the injection contract rather than bypassing
    it through `tool.func`.
    """
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    backend.write("/workspace/findings/FIND-001.md", finding())
    backend.write("/workspace/findings/evidence/proof.txt", "positive and baseline")
    quality_tool = build_finding_quality_tool(backend)
    result = quality_tool.invoke(
        {
            "name": "check_finding_report",
            "args": {"path": "findings/FIND-001.md", "runtime": _tool_runtime()},
            "id": "call-1",
            "type": "tool_call",
        }
    )
    assert json.loads(result.content) == {"valid": True, "errors": []}


def test_runtime_injected_through_toolnode_graph(tmp_path):
    """A compiled graph must inject ToolRuntime into the tool end to end.

    This is the production shape: `ToolNode` constructs `ToolRuntime` from the
    live graph state and hands it to the tool inside `invoke`.
    """
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    backend.write("/workspace/findings/FIND-001.md", finding())
    backend.write("/workspace/findings/evidence/proof.txt", "positive and baseline")
    quality_tool = build_finding_quality_tool(backend)
    QualityState = TypedDict(
        "QualityState",
        {"messages": Annotated[list, add_messages], "workspace_path": str},
    )
    graph_builder = StateGraph(QualityState)
    graph_builder.add_node("tools", ToolNode([quality_tool]))
    graph_builder.add_edge(START, "tools")
    graph = graph_builder.compile()
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "check_finding_report",
                "args": {"path": "findings/FIND-001.md"},
                "id": "call-1",
            }
        ],
    )
    result = graph.invoke({"messages": [call], "workspace_path": "/workspace"})
    assert json.loads(result["messages"][-1].content) == {"valid": True, "errors": []}


def test_model_visible_description_documents_the_relative_path_contract():
    """The exposed description must say the path is engagement-relative.

    Regression (#957): the description previously only said "Check a
    canonical finding's verdict, CVSS math, sections and evidence paths" —
    nothing distinguished the required `findings/FIND-NNN.md` form from the
    `/workspace/...` virtual paths other filesystem tools accept, so a
    caller could plausibly pass the wrong form and only discover the
    mismatch from an invalid-call error.
    """
    quality_tool = build_finding_quality_tool(None)
    description = quality_tool.description
    assert "findings/FIND-" in description
    assert "/workspace/" in description and "not" in description.lower()
