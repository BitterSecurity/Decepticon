from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp.client.transports import StdioTransport
from fastmcp.server.providers.proxy import ProxyClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

WORKSPACE = Path(os.environ.get("REA_WORKSPACE_ROOT", "/workspace")).resolve()
ENTRY = os.environ.get("REA_ENTRY_PATH", "/opt/rea/node_modules/rea-agents/scripts/rea.mjs")
LIFECYCLE_TOOLS = frozenset(
    {"open_binary", "close_binary", "export_evidence_bundle", "import_evidence_bundle"}
)


async def ignore_progress(_progress: float, _total: float | None, _message: str | None) -> None:
    return None


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validated_source(raw: str) -> Path:
    source = Path(raw).resolve(strict=True)
    if not source.is_file() or not source.is_relative_to(WORKSPACE):
        raise ValueError("binary must be a file inside /workspace")
    return source


def result_text(result: Any) -> dict[str, Any]:
    for item in result.content:
        if item.type == "text":
            try:
                value = json.loads(item.text)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("REA returned no JSON result")


def trace_references(evidence: dict[str, Any], source_sha256: str) -> tuple[list[str], set[str]]:
    digest = evidence.get("subject", {}).get("digest", {})
    graph = evidence.get("normalized_result")
    if (
        digest.get("sha256") != source_sha256
        or evidence.get("operation") != "trace_native_values"
        or not isinstance(graph, dict)
    ):
        raise ValueError("REA trace source or graph is invalid")
    procedures = graph.get("procedures")
    if not isinstance(procedures, list) or not procedures or len(procedures) > 64:
        raise ValueError("REA trace procedure list is invalid")
    addresses: list[str] = []
    references: set[str] = set()
    for procedure in procedures:
        if not isinstance(procedure, dict) or not isinstance(procedure.get("address"), str):
            raise ValueError("REA trace procedure is invalid")
        addresses.append(procedure["address"])
    for field in ("procedures", "nodes", "edges"):
        rows = graph.get(field, [])
        if not isinstance(rows, list):
            raise ValueError("REA trace references are invalid")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("evidence_id"), str):
                raise ValueError("REA trace Evidence reference is invalid")
            references.add(row["evidence_id"])
    for reference in evidence.get("evidence_links", []):
        if not isinstance(reference, str):
            raise ValueError("REA trace Evidence link is invalid")
        references.add(reference)
    return list(dict.fromkeys(addresses)), references


class ReaSession:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.client: ProxyClient | None = None
        self.source: Path | None = None
        self.source_sha256: str | None = None
        self.run_id: str | None = None
        self.pinned: tempfile.TemporaryDirectory[str] | None = None
        self.tools: dict[str, dict[str, Any]] = {}
        self.available: frozenset[str] = frozenset()
        self.required_evidence_ids: set[str] = set()
        self.manifest: dict[str, Any] | None = None

    async def start(self) -> None:
        transport = StdioTransport(
            command="node",
            args=[ENTRY, "mcp"],
            env={
                **os.environ,
                "REA_ANALYSIS_PROVIDER": "ghidra",
                "GHIDRA_INSTALL_DIR": "/opt/ghidra",
            },
            keep_alive=True,
        )
        self.client = ProxyClient(transport, mode="legacy")
        await self.client.__aenter__()
        self.tools = {
            tool.name: tool.model_dump(by_alias=True) for tool in await self.client.list_tools()
        }

    async def stop(self) -> None:
        async with self.lock:
            await self._close()
            if self.client is not None:
                await self.client.__aexit__(None, None, None)
                self.client = None

    async def _close(self) -> None:
        if self.source is None or self.client is None:
            return
        await self._persist()
        closed = await self.client.call_tool(
            "close_binary", {}, progress_handler=ignore_progress, raise_on_error=False
        )
        if closed.is_error:
            raise RuntimeError("REA could not close the previous binary")
        if self.pinned is not None:
            self.pinned.cleanup()
        self.source = None
        self.source_sha256 = None
        self.run_id = None
        self.pinned = None
        self.available = frozenset()
        self.required_evidence_ids.clear()

    async def ensure_open(self, raw: str) -> None:
        if self.client is None:
            raise RuntimeError("REA is not connected")
        source = validated_source(raw)
        source_sha256 = digest_file(source)
        if self.source == source and self.source_sha256 == source_sha256:
            return
        await self._close()
        pinned = tempfile.TemporaryDirectory(prefix="decepticon-rea-source-")
        copy = Path(pinned.name) / source.name
        try:
            with source.open("rb") as original, copy.open("xb") as target:
                shutil.copyfileobj(original, target, 1024 * 1024)
            if digest_file(copy) != source_sha256:
                raise ValueError("binary changed while REA copied it")
            copy.chmod(0o400)
            opened = await self.client.call_tool(
                "open_binary",
                {"path": str(copy)},
                progress_handler=ignore_progress,
                raise_on_error=False,
            )
            identity = opened.structured_content
            if (
                opened.is_error
                or not isinstance(identity, dict)
                or not isinstance(identity.get("result"), dict)
                or identity["result"].get("sha256") != source_sha256
            ):
                raise RuntimeError("REA opened a different binary")
            session = await self.client.call_tool(
                "binary_session", {}, progress_handler=ignore_progress, raise_on_error=False
            )
            rows = result_text(session).get("result", {}).get("tool_availability")
            if session.is_error or not isinstance(rows, list):
                raise RuntimeError("REA did not report available tools")
            available = frozenset(
                row["name"]
                for row in rows
                if isinstance(row, dict)
                and isinstance(row.get("name"), str)
                and row.get("available") is True
            )
            if not available:
                raise RuntimeError("REA reported no available tools")
        except Exception:
            pinned.cleanup()
            raise
        self.source = source
        self.source_sha256 = source_sha256
        self.run_id = f"run_{uuid.uuid4().hex}"
        self.pinned = pinned
        self.available = available
        self.manifest = None

    async def _persist(self) -> dict[str, Any]:
        if self.client is None or self.run_id is None or self.source_sha256 is None:
            raise RuntimeError("REA analysis is not open")
        output = WORKSPACE / ".analysis" / "rea" / self.run_id
        output.mkdir(parents=True, exist_ok=True)
        if not output.resolve().is_relative_to(WORKSPACE):
            raise ValueError("REA Evidence output escaped the workspace")
        with tempfile.TemporaryDirectory(prefix="decepticon-rea-export-") as staging:
            staged = Path(staging) / "bundle.json"
            exported = await self.client.call_tool(
                "export_evidence_bundle",
                {"path": str(staged)},
                progress_handler=ignore_progress,
                raise_on_error=False,
            )
            if exported.is_error or not staged.is_file():
                raise RuntimeError("REA Evidence export failed")
            body = staged.read_bytes()
        bundle = json.loads(body)
        records = bundle.get("records") if isinstance(bundle, dict) else None
        if not isinstance(records, list):
            raise ValueError("REA Evidence bundle is invalid")
        ids = {record.get("evidence_id") for record in records if isinstance(record, dict)}
        if not self.required_evidence_ids.issubset(ids):
            raise RuntimeError("REA trace source Evidence is missing from the bundle")
        digest = hashlib.sha256(body).hexdigest()
        artifact = output / f"evidence-{digest}.json"
        if artifact.exists() and artifact.read_bytes() != body:
            raise RuntimeError("REA Evidence digest collision")
        if not artifact.exists():
            temporary = output / f".{uuid.uuid4().hex}.tmp"
            temporary.write_bytes(body)
            temporary.replace(artifact)
        manifest = {
            "runId": self.run_id,
            "sourceSha256": self.source_sha256,
            "reaPackageVersion": "6.3.0",
            "evidenceBundle": artifact.name,
            "evidenceSha256": digest,
            "evidenceBytes": len(body),
            "evidencePath": str(artifact),
            "recordCount": len(records),
        }
        temporary = output / f".{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        temporary.replace(output / "manifest.json")
        self.manifest = manifest
        return manifest

    async def find(self, raw: str, query: str, cursor: int, limit: int) -> dict[str, Any]:
        await self.ensure_open(raw)
        names = [
            name
            for name, tool in self.tools.items()
            if name in self.available
            and name not in LIFECYCLE_TOOLS
            and (not query or query.lower() in (name + " " + tool.get("description", "")).lower())
        ]
        names.sort()
        page = names[cursor : cursor + limit]
        return {
            "runId": self.run_id,
            "sourceSha256": self.source_sha256,
            "total": len(names),
            "nextCursor": cursor + limit if cursor + limit < len(names) else None,
            "tools": [self.tools[name] for name in page],
        }

    async def call(self, raw: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_open(raw)
        if (
            self.client is None
            or name not in self.tools
            or name not in self.available
            or name in LIFECYCLE_TOOLS
        ):
            raise ValueError("REA tool is unavailable for the Ghidra session")
        result = await self.client.call_tool(
            name, arguments, progress_handler=ignore_progress, raise_on_error=False
        )
        if result.is_error:
            return {
                "isError": True,
                "diagnostic": [block.text for block in result.content if block.type == "text"],
            }
        response = result.structured_content
        if not isinstance(response, dict):
            raise RuntimeError("REA returned no structured result")
        if name == "trace_native_values":
            addresses, references = trace_references(response, self.source_sha256 or "")
            for index, address in enumerate(addresses):
                selector = (
                    response.get("parameters", {}).get("procedure") if index == 0 else address
                )
                observed = await self.client.call_tool(
                    "analyze_function",
                    {"procedure": selector},
                    progress_handler=ignore_progress,
                    raise_on_error=False,
                )
                if observed.is_error:
                    raise RuntimeError("REA could not materialize trace source Evidence")
            self.required_evidence_ids.update(references)
        manifest = await self._persist()
        return {
            "isError": False,
            "manifest": manifest,
            "evidence" if isinstance(response.get("evidence_id"), str) else "result": response,
        }


session = ReaSession()


async def health(_request: Request) -> JSONResponse:
    return JSONResponse(
        {"ready": session.client is not None, "backend": "rea", "provider": "ghidra"}
    )


async def find(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        path, query = body["binary_path"], body.get("query", "")
        cursor, limit = body.get("cursor", 0), body.get("limit", 20)
        if (
            not isinstance(path, str)
            or not isinstance(query, str)
            or not isinstance(cursor, int)
            or cursor < 0
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("Invalid REA search request")
        async with session.lock:
            result = await session.find(path, query, cursor, limit)
        return JSONResponse(result)
    except (KeyError, TypeError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception:
        return JSONResponse({"error": "REA search failed"}, status_code=503)


async def call(request: Request) -> JSONResponse:
    try:
        body = await request.json()
        path, name, arguments = body["binary_path"], body["name"], body.get("arguments", {})
        if (
            not isinstance(path, str)
            or not isinstance(name, str)
            or not isinstance(arguments, dict)
        ):
            raise ValueError("Invalid REA call request")
        async with session.lock:
            result = await session.call(path, name, arguments)
        return JSONResponse(result, status_code=422 if result["isError"] else 200)
    except (KeyError, TypeError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception:
        return JSONResponse({"error": "REA call or Evidence persistence failed"}, status_code=503)


@asynccontextmanager
async def lifespan(_app: Starlette):
    await session.start()
    try:
        yield
    finally:
        await session.stop()


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/find", find, methods=["POST"]),
        Route("/call", call, methods=["POST"]),
    ],
    lifespan=lifespan,
)
