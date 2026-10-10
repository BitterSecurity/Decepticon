"""Scanner adapter registry + dispatcher + built-in adapters.

The single ``kg_ingest`` tool (PR-B.3) routes here via
:func:`ingest`. Adapters are registered under a ``scanner_kind`` string;
plugin authors can extend the registry via the
``decepticon.kg.ingesters`` entry-point group.

Built-in adapters in PR-B.2:

  - ``nmap_xml``     — Nmap XML output (hosts, services, web entrypoints)
  - ``nuclei_jsonl`` — Nuclei JSONL output (vulnerabilities, entrypoints)
  - ``httpx_jsonl``  — httpx JSONL output (hosts, services, URLs, entrypoints)
  - ``sarif``        — SARIF v2.1.0 JSON (vulnerabilities, code locations)

The remaining scanner kinds from the design notes (subfinder, dnsx,
katana, masscan, ffuf, testssl, crackmapexec, asrep_hashes) can be added
incrementally in follow-up commits or as plugin packages — they share
the same adapter signature.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import defusedxml.ElementTree as ET
import yaml

from decepticon.middleware.kg_internal.ai_surface import (
    technology_for_banner,
    technology_for_headers,
    technology_for_path,
    technology_for_port,
    technology_for_title,
)
from decepticon.middleware.kg_internal.store import KGStore
from decepticon_core.utils.logging import get_logger

log = get_logger("kg.ingest")


# ── Adapter signature + registry ───────────────────────────────────────


ScannerAdapter = Callable[
    [Path, KGStore, str, str, str],
    dict[str, Any],
]
"""Signature: ``(path, store, engagement, created_by, source_episode_id) -> summary``.

The adapter parses the scanner output at ``path``, builds a list of
observations, and calls ``store.record_observations(...)``. It returns a
summary dict the tool layer surfaces to the LLM.
"""


_REGISTRY: dict[str, ScannerAdapter] = {}


def register_adapter(scanner_kind: str, adapter: ScannerAdapter) -> None:
    """Register a scanner adapter.

    Idempotent — re-registering the same ``scanner_kind`` replaces the
    previous entry. Plugin authors typically register at import time
    via the ``decepticon.kg.ingesters`` entry-point group.
    """
    if not isinstance(scanner_kind, str) or not scanner_kind:
        raise ValueError("scanner_kind must be a non-empty string")
    if not callable(adapter):
        raise ValueError("adapter must be callable")
    _REGISTRY[scanner_kind] = adapter


def available_scanners() -> list[str]:
    """Sorted list of registered ``scanner_kind`` names."""
    return sorted(_REGISTRY.keys())


def ingest(
    scanner_kind: str,
    path: str | Path,
    *,
    store: KGStore,
    engagement: str,
    created_by: str,
    source_episode_id: str,
) -> dict[str, Any]:
    """Dispatch to the adapter for ``scanner_kind``.

    Returns a dict whose shape is
    ``{"scanner", "path", **adapter_result}`` on success or
    ``{"error", "scanner", ...}`` on failure (unknown scanner_kind,
    missing file, parse error). The middleware tool layer (PR-B.3)
    forwards this to the LLM verbatim.
    """
    if scanner_kind not in _REGISTRY:
        return {
            "error": f"unknown scanner_kind: {scanner_kind!r}",
            "available": available_scanners(),
        }
    target = Path(path)
    if not target.exists():
        return {
            "error": f"file not found: {target}",
            "scanner": scanner_kind,
        }

    adapter = _REGISTRY[scanner_kind]
    try:
        result = adapter(target, store, engagement, created_by, source_episode_id)
    except Exception as exc:
        log.warning("kg_ingest adapter %s raised: %s", scanner_kind, exc)
        return {
            "error": f"adapter failure: {exc}",
            "scanner": scanner_kind,
            "path": str(target),
        }
    return {"scanner": scanner_kind, "path": str(target), **result}


# ── Built-in adapters ──────────────────────────────────────────────────


_WEB_PORTS = {80, 443, 8000, 8080, 8443, 8888}


def _adapt_nmap_xml(
    path: Path,
    store: KGStore,
    engagement: str,
    created_by: str,
    source_episode_id: str,
) -> dict[str, Any]:
    """Parse Nmap XML output and record host + service + entrypoint nodes."""
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"error": f"nmap xml parse failed: {exc}", "hosts": 0, "services": 0}
    if root is None:
        return {"error": "nmap xml parse failed: empty document", "hosts": 0, "services": 0}

    observations: list[dict[str, Any]] = []
    hosts_count = 0
    services_count = 0
    entrypoints_count = 0

    for host_el in root.findall("host"):
        status = host_el.find("status")
        if status is not None and status.get("state") not in {None, "up"}:
            continue
        addr_el = host_el.find("address[@addrtype='ipv4']")
        if addr_el is None:
            addr_el = host_el.find("address")
        if addr_el is None:
            continue
        ip = addr_el.get("addr")
        if not ip:
            continue
        hostname_el = host_el.find("hostnames/hostname")
        hostname = hostname_el.get("name") if hostname_el is not None else ""
        host_label = hostname or ip
        host_key = f"host::{ip}"

        host_edges: list[dict[str, Any]] = []
        for port_el in host_el.findall("ports/port"):
            state_el = port_el.find("state")
            if state_el is None or state_el.get("state") != "open":
                continue
            try:
                port = int(port_el.get("portid", "0"))
            except ValueError:
                continue
            proto = port_el.get("protocol", "tcp")
            svc_el = port_el.find("service")
            svc_name = svc_el.get("name") if svc_el is not None else "unknown"
            product = svc_el.get("product") if svc_el is not None else ""
            version = svc_el.get("version") if svc_el is not None else ""
            svc_key = f"service::{ip}:{port}"
            host_edges.append({"to_key": svc_key, "kind": "HOSTS", "weight": 0.5})
            service_obs: dict[str, Any] = {
                "kind": "Service",
                "key": svc_key,
                "label": f"{ip}:{port}/{proto}",
                "props": {
                    "port": port,
                    "protocol": proto,
                    "service": svc_name,
                    "product": product or "",
                    "version": version or "",
                    "source": "nmap",
                },
            }
            # AI-surface: a recognized AI port or a service banner naming an
            # AI runtime becomes a typed Technology the service RUNS, so the
            # llm-redteam plugin can find it (ADR-0007). Deduped by tech key
            # so a port+banner double-hit lands one node.
            banner = " ".join(p for p in (product, version, svc_name) if p)
            ai_nodes: dict[str, dict[str, Any]] = {}
            ai_edges: dict[str, dict[str, Any]] = {}
            for classified in (
                technology_for_port(port, "nmap"),
                technology_for_banner(banner, "nmap"),
            ):
                if classified is not None:
                    tech_node, runs_edge = classified
                    ai_nodes.setdefault(tech_node["key"], tech_node)
                    ai_edges.setdefault(runs_edge["to_key"], runs_edge)
            if ai_edges:
                service_obs["edges_out"] = list(ai_edges.values())
                observations.extend(ai_nodes.values())
            observations.append(service_obs)
            services_count += 1

            if port in _WEB_PORTS:
                scheme = "https" if port in {443, 8443} else "http"
                url = f"{scheme}://{host_label}:{port}/"
                ep_key = f"entrypoint::{url}"
                observations.append(
                    {
                        "kind": "Entrypoint",
                        "key": ep_key,
                        "label": url,
                        "props": {
                            "scheme": scheme,
                            "host": host_label,
                            "port": port,
                            "source": "nmap",
                        },
                    }
                )
                entrypoints_count += 1

        observations.append(
            {
                "kind": "Host",
                "key": host_key,
                "label": host_label,
                "props": {
                    "ip": ip,
                    "hostname": hostname or "",
                    "explored": True,
                    "source": "nmap",
                },
                "edges_out": host_edges,
            }
        )
        hosts_count += 1

    if not observations:
        return {"hosts": 0, "services": 0, "entrypoints": 0}

    records = store.record_observations(
        observations,
        engagement=engagement,
        created_by=created_by,
        source_episode_id=source_episode_id,
    )
    return {
        "hosts": hosts_count,
        "services": services_count,
        "entrypoints": entrypoints_count,
        "records": records,
    }


def _adapt_nuclei_jsonl(
    path: Path,
    store: KGStore,
    engagement: str,
    created_by: str,
    source_episode_id: str,
) -> dict[str, Any]:
    """Parse Nuclei JSONL and record vulnerability + entrypoint nodes."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"error": f"nuclei jsonl read failed: {exc}", "ingested": 0}

    observations: list[dict[str, Any]] = []
    parsed = 0
    skipped = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        parsed += 1

        info = record.get("info") if isinstance(record.get("info"), dict) else {}
        severity = str(info.get("severity") or "info").lower()
        rule_id = str(record.get("template-id") or "unknown-template")
        target = str(record.get("matched-at") or record.get("host") or "unknown-target")

        vuln_key = f"vuln::nuclei::{rule_id}::{target}"
        observations.append(
            {
                "kind": "Vulnerability",
                "key": vuln_key,
                "label": f"[nuclei:{rule_id}] {target}",
                "props": {
                    "scanner": "nuclei",
                    "rule_id": rule_id,
                    "severity": severity,
                    "target": target,
                    "tags": info.get("tags") if isinstance(info.get("tags"), list) else [],
                },
            }
        )

        parsed_target = urlparse(target)
        if parsed_target.scheme and parsed_target.netloc:
            ep_key = f"entrypoint::{target}"
            observations.append(
                {
                    "kind": "Entrypoint",
                    "key": ep_key,
                    "label": target,
                    "props": {
                        "scheme": parsed_target.scheme,
                        "host": parsed_target.hostname or "",
                        "port": parsed_target.port,
                        "source": "nuclei",
                    },
                    "edges_out": [{"to_key": vuln_key, "kind": "HAS_VULN", "weight": 0.4}],
                }
            )

    if not observations:
        return {"parsed": parsed, "skipped": skipped, "ingested": 0}

    records = store.record_observations(
        observations,
        engagement=engagement,
        created_by=created_by,
        source_episode_id=source_episode_id,
    )
    return {"parsed": parsed, "skipped": skipped, "records": records}


def _adapt_httpx_jsonl(
    path: Path,
    store: KGStore,
    engagement: str,
    created_by: str,
    source_episode_id: str,
) -> dict[str, Any]:
    """Parse httpx JSONL and record host + service + entrypoint nodes."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"error": f"httpx jsonl read failed: {exc}", "ingested": 0}

    observations: list[dict[str, Any]] = []
    parsed = 0
    skipped = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        parsed += 1

        url = str(row.get("url") or row.get("input") or "").strip()
        if not url:
            skipped += 1
            continue

        parsed_url = urlparse(url)
        host_value = (str(row.get("host") or parsed_url.hostname or "")).strip().lower()
        if not host_value:
            skipped += 1
            continue

        port_raw = row.get("port") or parsed_url.port
        try:
            port_int = (
                int(port_raw)
                if port_raw is not None
                else (443 if parsed_url.scheme == "https" else 80)
            )
        except (TypeError, ValueError):
            port_int = 443 if parsed_url.scheme == "https" else 80
        scheme = parsed_url.scheme or "http"
        status_code = row.get("status-code")

        host_key = f"host::{host_value}"
        svc_key = f"service::{host_value}:{port_int}"
        ep_key = f"entrypoint::{url}"

        service_obs: dict[str, Any] = {
            "kind": "Service",
            "key": svc_key,
            "label": f"{host_value}:{port_int}/tcp",
            "props": {
                "port": port_int,
                "scheme": scheme,
                "status_code": status_code,
                "source": "httpx",
            },
        }
        # AI-surface: a probed AI inference route (Ollama /api/tags, the
        # OpenAI-compatible /v1/* surface), a recognized AI web-UI title, or
        # an AI product's response headers becomes a typed Technology the
        # service RUNS, so the llm-redteam plugin can find it (ADR-0007).
        # Deduped by tech key so multiple signals for one product land one node.
        status_int = status_code if isinstance(status_code, int) else None
        ai_nodes: dict[str, dict[str, Any]] = {}
        ai_edges: dict[str, dict[str, Any]] = {}
        for classified in (
            technology_for_path(parsed_url.path, status_int, "httpx"),
            technology_for_title(row.get("title"), "httpx"),
            technology_for_headers(row.get("header"), "httpx"),
        ):
            if classified is not None:
                tech_node, runs_edge = classified
                ai_nodes.setdefault(tech_node["key"], tech_node)
                ai_edges.setdefault(runs_edge["to_key"], runs_edge)
        if ai_edges:
            service_obs["edges_out"] = list(ai_edges.values())
            observations.extend(ai_nodes.values())

        observations.extend(
            [
                {
                    "kind": "Host",
                    "key": host_key,
                    "label": host_value,
                    "props": {"hostname": host_value, "source": "httpx"},
                },
                service_obs,
                {
                    "kind": "Entrypoint",
                    "key": ep_key,
                    "label": url,
                    "props": {
                        "host": host_value,
                        "scheme": scheme,
                        "port": port_int,
                        "status_code": status_code,
                        "source": "httpx",
                    },
                },
            ]
        )

    if not observations:
        return {"parsed": parsed, "skipped": skipped, "ingested": 0}

    records = store.record_observations(
        observations,
        engagement=engagement,
        created_by=created_by,
        source_episode_id=source_episode_id,
    )
    return {"parsed": parsed, "skipped": skipped, "records": records}


class _SarifSource(Protocol):
    def read_text(self, encoding: str = "utf-8") -> str: ...


def _adapt_sarif(
    path: _SarifSource,
    store: KGStore,
    engagement: str,
    created_by: str,
    source_episode_id: str,
) -> dict[str, Any]:
    """Parse SARIF v2.1.0 JSON and record vulnerability + code-location nodes."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"sarif parse failed: {exc}", "ingested": 0}

    runs = data.get("runs") or []
    observations: list[dict[str, Any]] = []
    results_processed = 0

    severity_from_level = {
        "error": "high",
        "warning": "medium",
        "note": "low",
        "none": "info",
    }

    for run in runs:
        if not isinstance(run, dict):
            continue
        driver = (run.get("tool") or {}).get("driver") or {}
        scanner_name = str(driver.get("name") or "sarif")
        results = run.get("results") or []
        for result in results:
            if not isinstance(result, dict):
                continue
            results_processed += 1
            rule_id = str(result.get("ruleId") or "unknown")
            level = str(result.get("level") or "warning").lower()
            severity = severity_from_level.get(level, "info")
            message_obj = result.get("message") or {}
            message = (
                str(message_obj.get("text") or rule_id)
                if isinstance(message_obj, dict)
                else rule_id
            )

            locations = result.get("locations") or []
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                phys = loc.get("physicalLocation") or {}
                artifact = phys.get("artifactLocation") or {}
                file_uri = str(artifact.get("uri") or "")
                region = phys.get("region") or {}
                start_line = region.get("startLine") or 0
                try:
                    line_no = int(start_line)
                except (TypeError, ValueError):
                    line_no = 0

                code_loc_key = f"code_loc::{scanner_name}::{file_uri}::{line_no}"
                vuln_key = f"vuln::sarif::{scanner_name}::{rule_id}::{file_uri}::{line_no}"

                observations.append(
                    {
                        "kind": "Vulnerability",
                        "key": vuln_key,
                        "label": f"[{scanner_name}:{rule_id}] {file_uri}:{line_no}",
                        "props": {
                            "scanner": scanner_name,
                            "rule_id": rule_id,
                            "severity": severity,
                            "description": message,
                            "file": file_uri,
                            "line": line_no,
                        },
                        "edges_out": [
                            {"to_key": code_loc_key, "kind": "DEFINED_IN", "weight": 1.0}
                        ],
                    }
                )
                observations.append(
                    {
                        "kind": "CodeLocation",
                        "key": code_loc_key,
                        "label": f"{file_uri}:{line_no}",
                        "props": {"file": file_uri, "start_line": line_no},
                    }
                )

    if not observations:
        return {"results_processed": results_processed, "ingested": 0}

    records = store.record_observations(
        observations,
        engagement=engagement,
        created_by=created_by,
        source_episode_id=source_episode_id,
    )
    return {"results_processed": results_processed, "records": records}


# ── Register built-ins at import time ──────────────────────────────────


_HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})


def _has_external_ref(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "$ref" and isinstance(nested, str):
                parsed = urlparse(nested)
                if parsed.scheme or parsed.netloc:
                    return True
            if _has_external_ref(nested):
                return True
    elif isinstance(value, list):
        return any(_has_external_ref(nested) for nested in value)
    return False


def _load_api_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    parsed: Any
    if path.suffix.lower() in {".yaml", ".yml"}:
        parsed = yaml.safe_load(text)
    else:
        parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("API document must be an object")
    if _has_external_ref(parsed):
        raise ValueError("external $ref is not supported")
    return parsed


def _postman_url(request: dict[str, Any]) -> str:
    url = request.get("url")
    if isinstance(url, str):
        return url
    if not isinstance(url, dict):
        return ""
    raw = url.get("raw")
    if isinstance(raw, str):
        return raw
    host = url.get("host", [])
    path = url.get("path", [])
    host_text = ".".join(str(part) for part in host) if isinstance(host, list) else str(host)
    path_text = "/".join(str(part) for part in path) if isinstance(path, list) else str(path)
    protocol = str(url.get("protocol") or "https")
    return f"{protocol}://{host_text}/{path_text}" if host_text else f"/{path_text}"


def _iter_postman_items(items: Any) -> list[tuple[str, str, str]]:
    operations: list[tuple[str, str, str]] = []
    if not isinstance(items, list):
        return operations
    for item in items:
        if not isinstance(item, dict):
            continue
        operations.extend(_iter_postman_items(item.get("item")))
        request = item.get("request")
        if not isinstance(request, dict):
            continue
        raw_url = _postman_url(request)
        if not raw_url:
            continue
        parsed = urlparse(raw_url)
        method = str(request.get("method") or "GET").upper()
        operations.append((method, parsed.path or "/", raw_url))
    return operations


def _adapt_api_spec(
    path: Path,
    store: KGStore,
    engagement: str,
    created_by: str,
    source_episode_id: str,
) -> dict[str, Any]:
    """Import a local OpenAPI or Postman document without contacting its target."""
    try:
        document = _load_api_document(path)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return {"error": f"API document parse failed: {exc}", "operations": 0}

    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    source_key = f"api-spec::{source_hash}"
    operations: list[tuple[str, str, str, dict[str, Any]]] = []
    source_kind = ""

    paths = document.get("paths")
    if isinstance(paths, dict) and ("openapi" in document or "swagger" in document):
        source_kind = "openapi"
        servers = document.get("servers")
        base_url = ""
        if isinstance(servers, list) and servers and isinstance(servers[0], dict):
            candidate = servers[0].get("url")
            if isinstance(candidate, str):
                base_url = candidate
        for route, item in paths.items():
            if not isinstance(route, str) or not isinstance(item, dict):
                continue
            for method, operation in item.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                    continue
                operations.append((method.upper(), route, base_url, operation))
    elif isinstance(document.get("info"), dict) and "item" in document:
        source_kind = "postman"
        for method, route, raw_url in _iter_postman_items(document.get("item")):
            operations.append((method, route, raw_url, {}))
    else:
        return {
            "error": "unsupported API document: expected OpenAPI or Postman collection",
            "operations": 0,
        }

    observations: list[dict[str, Any]] = []
    operation_keys: list[str] = []
    for method, route, base_url, operation in operations:
        operation_id = (
            operation.get("operationId") if isinstance(operation.get("operationId"), str) else ""
        )
        key = f"{source_key}::{method}::{route}"
        operation_keys.append(key)
        parameters = operation.get("parameters")
        observations.append(
            {
                "kind": "Entrypoint",
                "key": key,
                "label": f"{method} {route}",
                "props": {
                    "api_operation": True,
                    "method": method,
                    "path": route,
                    "base_url": base_url,
                    "operation_id": operation_id,
                    "parameter_count": len(parameters) if isinstance(parameters, list) else 0,
                    "source": source_kind,
                    "execution_state": "imported",
                    "requires_roe": True,
                },
            }
        )
    if not observations:
        return {
            "source": source_kind,
            "operations": 0,
            "records": {"created": 0, "merged": 0, "edges": 0},
        }
    observations.append(
        {
            "kind": "SourceFile",
            "key": source_key,
            "label": path.name,
            "props": {"source": source_kind, "sha256": source_hash},
            "edges_out": [
                {"to_key": key, "kind": "CONTAINS", "weight": 1.0} for key in operation_keys
            ],
        }
    )
    records = store.record_observations(
        observations,
        engagement=engagement,
        created_by=created_by,
        source_episode_id=source_episode_id,
    )
    return {"source": source_kind, "operations": len(operation_keys), "records": records}


register_adapter("api_spec", _adapt_api_spec)

register_adapter("nmap_xml", _adapt_nmap_xml)
register_adapter("nuclei_jsonl", _adapt_nuclei_jsonl)
register_adapter("httpx_jsonl", _adapt_httpx_jsonl)
register_adapter("sarif", _adapt_sarif)


# ── Plugin loading via entry-point group ───────────────────────────────


_PLUGIN_GROUP = "decepticon.kg.ingesters"


def load_plugin_adapters() -> int:
    """Discover and register adapters from the ``decepticon.kg.ingesters``
    entry-point group. Returns the number of adapters successfully
    loaded. Idempotent — re-registration overwrites silently.

    Plugin authors expose adapters as::

        # pyproject.toml
        [project.entry-points."decepticon.kg.ingesters"]
        my_scanner = "my_pkg.adapters:my_scanner_adapter"

    Failures during load are logged and skipped — one broken plugin
    does not stop others from registering.
    """
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover — Python <3.10 not supported
        return 0

    try:
        eps = entry_points(group=_PLUGIN_GROUP)
    except Exception as exc:  # pragma: no cover — defensive
        log.debug("entry_points() failed for %s: %s", _PLUGIN_GROUP, exc)
        return 0

    count = 0
    for ep in eps:
        try:
            adapter = ep.load()
            register_adapter(ep.name, adapter)
            count += 1
        except Exception as exc:
            log.warning("failed to load KG ingester plugin %s: %s", ep.name, exc)
    return count
