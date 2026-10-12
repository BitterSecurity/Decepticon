from __future__ import annotations

import json
import os

import httpx
from langchain_core.tools import tool


def rea_configured() -> bool:
    return bool(os.environ.get("DECEPTICON_REA_URL"))


async def _request(method: str, path: str, payload: dict | None = None) -> str:
    base = os.environ.get("DECEPTICON_REA_URL")
    if not base:
        return json.dumps({"error": "REA reversing workload is not configured"})
    try:
        async with httpx.AsyncClient(base_url=base.rstrip("/"), timeout=300.0) as client:
            response = await client.request(method, path, json=payload)
            result = response.json()
            if response.is_error and isinstance(result, dict):
                result["statusCode"] = response.status_code
            return json.dumps(result, ensure_ascii=False)
    except (httpx.HTTPError, ValueError) as error:
        return json.dumps({"error": f"REA workload unavailable: {type(error).__name__}"})


@tool(
    description="Check whether the local REA/Ghidra reversing workload is running. Start the reversing workload before opening a binary when it is unavailable."
)
async def rea_status() -> str:
    return await _request("GET", "/health")


@tool(
    description="Open a binary found in /workspace with REA and search Ghidra-session-available tools. Use a specific query, then cursor to page through all matches. Each tool's complete schema is returned as JSON text; this call preserves the session and pins the binary bytes."
)
async def rea_find_tools(binary_path: str, query: str = "", cursor: int = 0, limit: int = 5) -> str:
    raw = await _request(
        "POST",
        "/find",
        {
            "binary_path": binary_path,
            "query": query,
            "cursor": cursor,
            "limit": limit,
        },
    )
    result = json.loads(raw)
    if isinstance(result, dict) and isinstance(result.get("tools"), list):
        result["tools"] = [
            {
                "name": item.get("name"),
                "description": item.get("description"),
                "toolSchemaJson": json.dumps(item, ensure_ascii=False, separators=(",", ":")),
            }
            for item in result["tools"]
            if isinstance(item, dict)
        ]
    return json.dumps(result, ensure_ascii=False)


@tool(
    description="Call a REA tool discovered by rea_find_tools for a binary in /workspace. Pass the tool's exact name and JSON object arguments. REA Evidence is saved before the result is delivered; the response includes the original structured Evidence or other result and bundle manifest."
)
async def rea_call_tool(binary_path: str, name: str, arguments_json: str = "{}") -> str:
    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError:
        return json.dumps({"error": "arguments_json must be valid JSON"})
    if not isinstance(arguments, dict):
        return json.dumps({"error": "arguments_json must be a JSON object"})
    return await _request(
        "POST",
        "/call",
        {
            "binary_path": binary_path,
            "name": name,
            "arguments": arguments,
        },
    )


REA_TOOLS = [rea_status, rea_find_tools, rea_call_tool]
