"""Per-agent TODO list — lightweight task tracking per agent.

Each agent maintains its own TODO list persisted at
workspace/<agent_name>/todos.json. Supports create, list, complete, delete.
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


_lock = threading.Lock()


def _todos_path(agent_name: str) -> Path:
    safe = agent_name.strip() or "default"
    return _workspace() / safe / "todos.json"


def _load(agent_name: str) -> list:
    p = _todos_path(agent_name)
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return []


def _save(agent_name: str, todos: list) -> None:
    p = _todos_path(agent_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(todos, indent=2, default=str), "utf-8")
    tmp.replace(p)


@tool
def create_todo(description: str, agent_name: str = "default", priority: str = "medium") -> str:
    """Create a TODO item for the calling agent.

    WHEN TO USE: When you identify a task to track — a test to run,
    an endpoint to revisit, a finding to verify later.

    Args:
        description: What needs to be done
        agent_name: The agent creating this TODO (defaults to 'default')
        priority: One of: low, medium, high, critical
    """
    valid_priorities = {"low", "medium", "high", "critical"}
    pri = priority.lower()
    if pri not in valid_priorities:
        return _json({"error": f"invalid priority: {pri}", "valid": sorted(valid_priorities)})

    todo = {
        "id": str(uuid.uuid4())[:8],
        "description": description,
        "priority": pri,
        "done": False,
        "created_at": time.time(),
    }
    with _lock:
        todos = _load(agent_name)
        todos.append(todo)
        _save(agent_name, todos)
    return _json(
        {"created": True, "todo": {"id": todo["id"], "description": description, "priority": pri}}
    )


@tool
def list_todos(agent_name: str = "default", show_done: bool = False) -> str:
    """List TODO items for an agent.

    Args:
        agent_name: The agent whose TODOs to list
        show_done: Include completed TODOs (default False)
    """
    todos = _load(agent_name)
    if not show_done:
        todos = [t for t in todos if not t.get("done")]

    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    todos.sort(key=lambda t: priority_order.get(t.get("priority", "medium"), 2))

    return _json(
        {
            "agent": agent_name,
            "total": len(todos),
            "todos": [
                {
                    "id": t["id"],
                    "description": t["description"],
                    "priority": t.get("priority", "medium"),
                    "done": t.get("done", False),
                    "created_at": t.get("created_at"),
                }
                for t in todos
            ],
        }
    )


@tool
def mark_todo_done(todo_id: str, agent_name: str = "default") -> str:
    """Mark a TODO item as completed.

    Args:
        todo_id: The ID of the TODO to complete
        agent_name: The agent that owns the TODO
    """
    with _lock:
        todos = _load(agent_name)
        for t in todos:
            if t["id"] == todo_id:
                t["done"] = True
                t["completed_at"] = time.time()
                _save(agent_name, todos)
                return _json({"done": True, "id": todo_id})
        return _json({"error": f"todo not found: {todo_id}"})


@tool
def delete_todo(todo_id: str, agent_name: str = "default") -> str:
    """Delete a TODO item.

    Args:
        todo_id: The ID of the TODO to delete
        agent_name: The agent that owns the TODO
    """
    with _lock:
        todos = _load(agent_name)
        original_len = len(todos)
        todos = [t for t in todos if t["id"] != todo_id]
        if len(todos) == original_len:
            return _json({"error": f"todo not found: {todo_id}"})
        _save(agent_name, todos)
    return _json({"deleted": True, "id": todo_id})


TODO_TOOLS = [create_todo, list_todos, mark_todo_done, delete_todo]
