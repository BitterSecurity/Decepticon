"""Shared notes — cross-agent knowledge store.

Lightweight scratchpad for unstructured coordination knowledge:
credentials, endpoint inventories, observed behaviors, methodology notes.
All agents can read and write.
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


VALID_CATEGORIES = {
    "general",
    "findings",
    "methodology",
    "questions",
    "plan",
    "wiki",
    "credentials",
}


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


def _notes_path() -> Path:
    return _workspace() / "notes.json"


_lock = threading.Lock()


def _load() -> list:
    p = _notes_path()
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return []


def _save(notes: list) -> None:
    p = _notes_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(notes, indent=2, default=str), "utf-8")
    tmp.replace(p)


@tool
def create_note(
    title: str, content: str, category: str = "general", tags: str = "", agent_name: str = ""
) -> str:
    """Create a shared note visible to all agents.

    WHEN TO USE: When you discover information other agents need:
    credentials, endpoint lists, observed behaviors, methodology decisions.

    Args:
        title: Short descriptive title
        content: The note content
        category: One of: general, findings, methodology, questions, plan, wiki, credentials
        tags: Comma-separated tags
    """
    cat = category.lower()
    if cat not in VALID_CATEGORIES:
        return _json({"error": f"invalid category: {cat}", "valid": sorted(VALID_CATEGORIES)})

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    note = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "content": content,
        "category": cat,
        "tags": tag_list,
        "author": agent_name,
        "created_at": time.time(),
    }
    with _lock:
        notes = _load()
        notes.append(note)
        _save(notes)
    return _json({"created": True, "note": {"id": note["id"], "title": title}})


@tool
def list_notes(category: str = "", tags: str = "", search: str = "") -> str:
    """List shared notes with optional filtering.

    Args:
        category: Filter by category
        tags: Filter by comma-separated tags (any match)
        search: Search in title and content
    """
    notes = _load()
    if category:
        notes = [n for n in notes if n["category"] == category.lower()]
    if tags:
        tag_set = {t.strip().lower() for t in tags.split(",") if t.strip()}
        notes = [n for n in notes if tag_set & {t.lower() for t in n.get("tags", [])}]
    if search:
        s = search.lower()
        notes = [
            n for n in notes if s in n.get("title", "").lower() or s in n.get("content", "").lower()
        ]

    return _json(
        {
            "total": len(notes),
            "notes": [
                {
                    "id": n["id"],
                    "title": n["title"],
                    "category": n["category"],
                    "tags": n.get("tags", []),
                    "author": n.get("author", ""),
                    "preview": n["content"][:280],
                }
                for n in notes
            ],
        }
    )


@tool
def get_note(note_id: str) -> str:
    """Get the full content of a specific note."""
    notes = _load()
    for n in notes:
        if n["id"] == note_id:
            return _json(n)
    return _json({"error": f"note not found: {note_id}"})


@tool
def update_note(note_id: str, content: str = "", title: str = "", tags: str = "") -> str:
    """Update an existing note."""
    with _lock:
        notes = _load()
        for n in notes:
            if n["id"] == note_id:
                if content:
                    n["content"] = content
                if title:
                    n["title"] = title
                if tags:
                    n["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
                n["updated_at"] = time.time()
                _save(notes)
                return _json({"updated": True, "id": note_id})
        return _json({"error": f"note not found: {note_id}"})


@tool
def delete_note(note_id: str) -> str:
    """Delete a note."""
    with _lock:
        notes = _load()
        original_len = len(notes)
        notes = [n for n in notes if n["id"] != note_id]
        if len(notes) == original_len:
            return _json({"error": f"note not found: {note_id}"})
        _save(notes)
    return _json({"deleted": True, "id": note_id})


NOTES_TOOLS = [create_note, list_notes, get_note, update_note, delete_note]
