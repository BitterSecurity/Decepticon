"""Agent graph introspection and lifecycle tools.

Provides tools for viewing the agent tree, messaging between agents,
structured completion reports, and graceful cascade stop.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


def _agents_path() -> Path:
    return _workspace() / "agents"


def _inbox_path(agent_id: str) -> Path:
    return _agents_path() / agent_id / "inbox.jsonl"


_lock = threading.Lock()


def _load_agents() -> dict:
    p = _agents_path() / "graph.json"
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            pass
    return {"agents": {}, "root": None}


def _save_agents(data: dict) -> None:
    p = _agents_path() / "graph.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), "utf-8")
    tmp.replace(p)


@tool
def register_agent(
    agent_id: str,
    name: str,
    task: str,
    parent_id: str = "",
    skills_json: str = "[]",
) -> str:
    """Register a new agent in the agent graph.

    WHEN TO USE: When spawning a new specialist sub-agent.

    Args:
        agent_id: Unique identifier for the agent
        name: Human-readable agent name
        task: Description of the agent's assigned task
        parent_id: ID of the parent agent (empty for root)
        skills_json: JSON array of skill names (max 5 kept)
    """
    try:
        skills = json.loads(skills_json) if skills_json.strip() else []
    except json.JSONDecodeError:
        skills = []

    with _lock:
        data = _load_agents()
        if agent_id in data["agents"]:
            return _json({"error": f"agent {agent_id} already registered"})
        data["agents"][agent_id] = {
            "id": agent_id,
            "name": name,
            "task": task,
            "parent_id": parent_id or None,
            "skills": skills[:5],
            "status": "running",
            "registered_at": time.time(),
            "children": [],
        }
        if parent_id and parent_id in data["agents"]:
            data["agents"][parent_id].setdefault("children", []).append(agent_id)
        if not data["root"]:
            data["root"] = agent_id
        _save_agents(data)
        # Ensure inbox dir exists
        (_agents_path() / agent_id).mkdir(parents=True, exist_ok=True)
    return _json({"registered": True, "agent_id": agent_id})


@tool
def view_agent_graph() -> str:
    """View the current agent tree with statuses.

    WHEN TO USE: To understand which agents are running, waiting,
    completed, or crashed. Shows the hierarchy and task assignments.
    """
    data = _load_agents()
    agents = data.get("agents", {})
    if not agents:
        return _json({"agents": [], "total": 0})

    def _tree(aid: str, depth: int = 0) -> list[dict]:
        a = agents.get(aid, {})
        indent = "  " * depth
        entry = {
            "indent": indent,
            "id": aid,
            "name": a.get("name", ""),
            "status": a.get("status", "unknown"),
            "task_preview": a.get("task", "")[:100],
            "skills": a.get("skills", []),
        }
        result = [entry]
        for cid in a.get("children", []):
            result.extend(_tree(cid, depth + 1))
        return result

    root = data.get("root")
    tree = _tree(root) if root else []

    status_counts: dict[str, int] = {}
    for a in agents.values():
        s = a.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    return _json({"total": len(agents), "status_counts": status_counts, "tree": tree})


@tool
def send_message_to_agent(
    target_agent_id: str,
    message: str,
    message_type: str = "information",
    priority: str = "normal",
    sender_id: str = "",
) -> str:
    """Send a message to another agent's inbox.

    WHEN TO USE: To coordinate with peer or child agents — share findings,
    give instructions, or ask questions.

    Args:
        target_agent_id: The recipient agent ID
        message: The message content
        message_type: query, instruction, or information
        priority: low, normal, high
        sender_id: Your agent ID
    """
    data = _load_agents()
    if target_agent_id not in data.get("agents", {}):
        return _json({"error": f"agent {target_agent_id} not found"})

    target = data["agents"][target_agent_id]
    if target.get("status") in ("completed", "crashed", "stopped"):
        return _json(
            {"error": (f"agent {target_agent_id} is {target['status']} — cannot receive messages")}
        )

    inbox = _inbox_path(target_agent_id)
    inbox.parent.mkdir(parents=True, exist_ok=True)
    msg = {
        "id": str(uuid.uuid4())[:8],
        "from": sender_id,
        "type": message_type,
        "priority": priority,
        "message": message,
        "sent_at": time.time(),
    }
    with open(inbox, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, default=str) + "\n")
    return _json({"sent": True, "to": target_agent_id, "message_id": msg["id"]})


@tool
def agent_finish(
    agent_id: str,
    result_summary: str,
    findings_json: str = "[]",
    open_items_json: str = "[]",
    success: bool = True,
    recommendations: str = "",
) -> str:
    """Complete an agent's task with a structured completion report.

    WHEN TO USE: When a specialist agent finishes its task. Files a
    structured report to the parent agent.

    Args:
        agent_id: The agent completing its task
        result_summary: Summary of what was accomplished
        findings_json: JSON array of key findings
        open_items_json: JSON array of items still unresolved
        success: Whether the task succeeded
        recommendations: Follow-up recommendations
    """
    with _lock:
        data = _load_agents()
        if agent_id not in data["agents"]:
            return _json({"error": f"agent {agent_id} not found"})
        agent = data["agents"][agent_id]
        agent["status"] = "completed"
        agent["completed_at"] = time.time()

        try:
            findings = json.loads(findings_json) if findings_json.strip() else []
        except json.JSONDecodeError:
            findings = []
        try:
            open_items = json.loads(open_items_json) if open_items_json.strip() else []
        except json.JSONDecodeError:
            open_items = []

        completion_report = {
            "agent_id": agent_id,
            "agent_name": agent.get("name", ""),
            "success": success,
            "result_summary": result_summary,
            "findings_filed": findings,
            "open_items": open_items,
            "recommendations": recommendations,
            "completed_at": time.time(),
        }
        agent["completion_report"] = completion_report

        # Post to parent inbox
        parent_id = agent.get("parent_id")
        if parent_id and parent_id in data["agents"]:
            inbox = _inbox_path(parent_id)
            inbox.parent.mkdir(parents=True, exist_ok=True)
            msg = {
                "id": str(uuid.uuid4())[:8],
                "from": agent_id,
                "type": "completion_report",
                "priority": "high",
                "message": json.dumps(completion_report, default=str),
                "sent_at": time.time(),
            }
            with open(inbox, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, default=str) + "\n")

        _save_agents(data)
    return _json({"finished": True, "agent_id": agent_id, "report": completion_report})


@tool
def stop_agent(
    agent_id: str,
    cascade: bool = True,
    reason: str = "",
) -> str:
    """Gracefully stop an agent (and optionally its descendants).

    WHEN TO USE: When an agent's task is no longer needed or the
    operation should be aborted.

    Args:
        agent_id: The agent to stop
        cascade: If True, stop all descendant agents first (leaves-first)
        reason: Why the agent is being stopped
    """
    with _lock:
        data = _load_agents()
        if agent_id not in data["agents"]:
            return _json({"error": f"agent {agent_id} not found"})

        stopped: list[str] = []

        def _stop(aid: str) -> None:
            a = data["agents"].get(aid)
            if not a or a.get("status") in ("completed", "stopped"):
                return
            if cascade:
                for cid in a.get("children", []):
                    _stop(cid)
            a["status"] = "stopped"
            a["stopped_at"] = time.time()
            a["stop_reason"] = reason
            stopped.append(aid)

        _stop(agent_id)
        _save_agents(data)
    return _json({"stopped": stopped, "count": len(stopped)})


@tool
def read_agent_inbox(agent_id: str) -> str:
    """Read pending messages in an agent's inbox.

    WHEN TO USE: To check for coordination messages, completion reports,
    or instructions from other agents.

    Args:
        agent_id: The agent whose inbox to read
    """
    inbox = _inbox_path(agent_id)
    if not inbox.exists():
        return _json({"messages": [], "count": 0})
    messages = []
    for line in inbox.read_text("utf-8").strip().splitlines():
        if line.strip():
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return _json({"messages": messages, "count": len(messages)})


AGENT_GRAPH_TOOLS = [
    register_agent,
    view_agent_graph,
    send_message_to_agent,
    agent_finish,
    stop_agent,
    read_agent_inbox,
]
