from __future__ import annotations

import hashlib
import json
import os
import platform as platform_module
import re
import subprocess
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from benchmark.schemas import RunProvenance
from decepticon_core.types.llm import AuthMethod

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|password|secret|session|token)",
    re.IGNORECASE,
)
_URL_USERINFO = re.compile(r"(?<=://)[^/@\s]+@")


class ProvenanceCaptureError(RuntimeError):
    """Mandatory benchmark identity could not be captured."""


def capture_declared_model_assignments() -> tuple[str, dict[str, list[str]]]:
    raw_priority = os.environ.get("DECEPTICON_AUTH_PRIORITY", "")
    tokens = [token.strip().lower() for token in raw_priority.split(",") if token.strip()]
    if not tokens:
        raise ProvenanceCaptureError(
            "DECEPTICON_AUTH_PRIORITY must be explicit for reproducible benchmarks"
        )
    try:
        methods = [AuthMethod(token) for token in tokens]
    except ValueError as exc:
        raise ProvenanceCaptureError("invalid model provenance configuration") from exc
    if len(set(methods)) != len(methods):
        raise ProvenanceCaptureError("DECEPTICON_AUTH_PRIORITY contains duplicates")
    stack_name = os.environ.get("DECEPTICON_STACK_NAME", "").strip()
    container = f"decepticon-{stack_name}-langgraph" if stack_name else "decepticon-langgraph"
    script = """import json, os
from decepticon.llm.factory import LLMFactory
factory = LLMFactory()
chains = {}
for role, assignment in factory._mapping.assignments.items():
    effective = factory._compose_assignment(role, assignment)
    chains[role] = [effective.primary, *effective.fallbacks]
os.write(1, ('DECEPTICON_PROVENANCE=' + json.dumps({'auth_priority': os.getenv('DECEPTICON_AUTH_PRIORITY', ''), 'profile': os.getenv('DECEPTICON_MODEL_PROFILE', 'eco'), 'assignments': chains})).encode())
"""
    try:
        completed = subprocess.run(
            ["docker", "exec", container, "python", "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        raw = next(
            line.removeprefix("DECEPTICON_PROVENANCE=")
            for line in reversed(completed.stdout.splitlines())
            if line.startswith("DECEPTICON_PROVENANCE=")
        )
        payload = json.loads(raw)
        profile = payload["profile"]
        assignments = payload["assignments"]
        if (
            payload["auth_priority"] != raw_priority
            or not isinstance(profile, str)
            or not isinstance(assignments, dict)
            or not assignments
        ):
            raise ValueError("empty model assignment map")
        return profile, {str(role): list(chain) for role, chain in assignments.items()}
    except (
        OSError,
        subprocess.CalledProcessError,
        StopIteration,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise ProvenanceCaptureError("cannot read effective models from langgraph") from exc


def capture_container_images(explicit_refs: list[str]) -> dict[str, str]:
    stack_name = os.environ.get("DECEPTICON_STACK_NAME", "").strip()
    project = os.environ.get("DECEPTICON_COMPOSE_PROJECT", "").strip()
    if not project:
        project = f"decepticon-{stack_name}" if stack_name else "decepticon"
    try:
        running = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.Image}}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProvenanceCaptureError("docker is unavailable for provenance capture") from exc

    refs = sorted({*explicit_refs, *running.stdout.splitlines()} - {""})
    if not refs:
        raise ProvenanceCaptureError("no benchmark container images were found")

    resolved: dict[str, str] = {}
    for ref in refs:
        try:
            inspected = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{json .RepoDigests}}|{{.Id}}",
                    ref,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            raw_digests, separator, image_id = inspected.stdout.strip().partition("|")
            digests = json.loads(raw_digests)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            raise ProvenanceCaptureError(f"cannot resolve container image: {ref}") from exc
        if not separator or not isinstance(digests, list):
            raise ProvenanceCaptureError(f"invalid container identity for: {ref}")
        immutable = sorted(str(digest) for digest in digests if digest)
        identity = immutable[0] if immutable else image_id
        if not identity.startswith("sha256:") and "@sha256:" not in identity:
            raise ProvenanceCaptureError(f"container image is not immutable: {ref}")
        resolved[ref] = identity
    return resolved


def _git(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProvenanceCaptureError(f"git provenance failed: {' '.join(args)}") from exc
    return completed.stdout.strip()


def _artifact_hash(path: Path) -> str:
    if not path.exists():
        raise ProvenanceCaptureError(f"provenance artifact does not exist: {path}")
    files = (
        [path]
        if path.is_file()
        else sorted(
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file()
            and "__pycache__" not in candidate.parts
            and candidate.suffix not in {".pyc", ".pyo"}
        )
    )
    if not files:
        raise ProvenanceCaptureError(f"provenance artifact has no files: {path}")
    digest = hashlib.sha256()
    for file_path in files:
        relative = file_path.name if path.is_file() else file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
    return digest.hexdigest()


def _sanitize(value: Any, *, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {
            str(item_key): (
                "<redacted>"
                if key.lower() == "headers"
                else _sanitize(item_value, key=str(item_key))
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        sanitized = _URL_USERINFO.sub("<redacted>@", value)
        if "://" not in sanitized:
            return sanitized
        parsed = urlsplit(sanitized)
        query = urlencode(
            [
                (query_key, "<redacted>")
                for query_key, query_value in parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                )
            ]
        )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    return value


def _benchmark_revisions(repo_root: Path) -> dict[str, str]:
    revisions: dict[str, str] = {}
    for line in _git(repo_root, "submodule", "status").splitlines():
        fields = line.lstrip("-+U ").split()
        if len(fields) >= 2:
            revisions[fields[1]] = fields[0]
    return revisions


def capture_run_provenance(
    *,
    repo_root: Path,
    config: Mapping[str, Any],
    model_profile: str,
    model_assignments: Mapping[str, list[str]],
    container_images: Mapping[str, str],
    artifact_paths: Mapping[str, Path],
    context_mode: str = "hinted",
) -> RunProvenance:
    """Capture the reproducibility envelope before a benchmark starts."""
    root = repo_root.resolve()
    source_commit = _git(root, "rev-parse", "HEAD")
    source_dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=normal"))
    artifact_hashes = {
        name: _artifact_hash(path.resolve()) for name, path in sorted(artifact_paths.items())
    }
    sanitized = _sanitize(config)
    if not isinstance(sanitized, dict):
        raise ProvenanceCaptureError("benchmark config must sanitize to an object")
    return RunProvenance(
        run_id=str(uuid.uuid4()),
        captured_at=datetime.now(timezone.utc),
        context_mode=context_mode,
        source_commit=source_commit,
        source_dirty=source_dirty,
        benchmark_revisions=_benchmark_revisions(root),
        model_profile=model_profile,
        model_assignments={name: list(chain) for name, chain in model_assignments.items()},
        artifact_hashes=artifact_hashes,
        container_images=dict(container_images),
        config=sanitized,
        python_version=platform_module.python_version(),
        platform=f"{sys.platform}-{platform_module.machine()}",
    )
