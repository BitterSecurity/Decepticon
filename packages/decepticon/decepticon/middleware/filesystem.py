"""FilesystemMiddleware without `execute`, scoped to the active engagement."""

from __future__ import annotations

import posixpath
from dataclasses import replace
from typing import Any

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import validate_path
from deepagents.middleware.filesystem import FilesystemMiddleware as BaseFilesystemMiddleware

from decepticon.sandbox_kernel.base import SandboxBase
from decepticon.tools.filesystem import filesystem_tools_without_execute

WORKSPACE = "/workspace"
NO_WORKSPACE_ERROR = (
    "No engagement workspace is set. Filesystem tools are scoped to the active "
    "engagement and cannot access the shared /workspace root."
)
PLAN_ONLY_ERROR = "Plan filesystem access is limited to /workspace/plan."
LOCKED_ROE_ERROR = "The operator-confirmed RoE is read-only. Change engagement rules outside Plan mode."


def _normalize_engagement_workspace(workspace_path: str | None) -> str | None:
    """Strict 4-case contract for resolving the engagement workspace root.

    - Empty / None → ``None`` (fail closed; no engagement configured).
    - ``/workspace`` → ``/workspace`` (launcher mode: the bind-mount IS the
      engagement root, so the literal root is a valid workspace).
    - ``/workspace/<safe-slug>`` → normalized path (web mode: shared root with
      per-engagement subdirectories).
    - Anything else (traversal like ``/workspace/../etc``, characters outside
      the slug regex, etc.) → ``None``. Invalid paths are NOT silently coerced
      to ``/workspace``.
    """
    path = (workspace_path or "").strip()
    if not path:
        return None
    if path == WORKSPACE:
        return WORKSPACE
    if not path.startswith(f"{WORKSPACE}/"):
        return None
    expected = path.rstrip("/")
    # posixpath, not os.path: these are virtual POSIX paths. os.path.normpath
    # rewrites "/" to "\" on Windows, which would reject every valid workspace.
    if posixpath.normpath(expected) != expected:
        return None
    normalized = SandboxBase._normalize_workspace_path(path)
    return normalized if normalized == expected else None


class EngagementFilesystemBackend(BackendProtocol):
    """Map virtual /workspace paths to /workspace/<engagement> internally."""

    def __init__(
        self, backend: BackendProtocol, workspace_path: str | None, *, plan_only: bool = False
    ) -> None:
        self._backend = backend
        self._root = _normalize_engagement_workspace(workspace_path)
        self._plan_only = plan_only
        # Engagement subdir under WORKSPACE is otherwise materialized lazily by
        # the first ``write`` — but the very first agent step is typically a
        # filesystem inspection (``ls`` / ``glob``), which the backend surfaces
        # as ``path_not_found`` against the not-yet-created subdir. Drop a
        # marker file at the root on construction so the workspace is always
        # observable. Backends auto-create parent dirs on write, so this is
        # the cheapest way to enforce the "workspace is reachable" invariant
        # without expanding the backend protocol.
        self._root_ensured = False

    def _ensure_root(self) -> None:
        if self._root_ensured or self._root is None:
            return
        self._root_ensured = True
        if self._plan_only:
            return
        try:
            self._backend.write(f"{self._root}/.engagement", "")
        except Exception:
            # Best-effort: a second engagement reusing the same backend will
            # find the marker already there and the write will refuse. That's
            # fine — the dir is materialized regardless.
            pass

    def _real(self, path: str | None) -> str:
        if self._root is None:
            raise ValueError(NO_WORKSPACE_ERROR)
        virtual = validate_path(path or WORKSPACE)
        if virtual in {"/", WORKSPACE}:
            return f"{self._root}/plan" if self._plan_only else self._root
        # Idempotent: if the path already points inside ``self._root`` it is
        # already a real engagement path — return as-is. Without this guard
        # the path gets re-prefixed and the engagement slug doubles, e.g.
        # ``/workspace/<engagement-slug>/exploit/x.txt`` would resolve to
        # ``/workspace/<engagement-slug>/<engagement-slug>/exploit/x.txt``.
        # Caller-side prompts no longer need to teach agents about virtual vs
        # real paths — the backend accepts both.
        if virtual == self._root or virtual.startswith(f"{self._root}/"):
            real = virtual
        else:
            rel = virtual.removeprefix(f"{WORKSPACE}/").lstrip("/")
            real = f"{self._root}/{rel}" if rel else self._root
        if (
            self._plan_only
            and real != f"{self._root}/plan"
            and not real.startswith(f"{self._root}/plan/")
        ):
            raise ValueError(PLAN_ONLY_ERROR)
        return real

    def _virtual(self, path: str) -> str | None:
        if self._root is None:
            return None
        normalized = path.replace("\\", "/").rstrip("/")
        if normalized and not normalized.startswith("/"):
            normalized = f"{self._root}/{normalized}"
        if (
            self._plan_only
            and normalized != f"{self._root}/plan"
            and not normalized.startswith(f"{self._root}/plan/")
        ):
            return None
        if normalized == self._root:
            return WORKSPACE
        if normalized.startswith(f"{self._root}/"):
            return f"{WORKSPACE}/{normalized[len(self._root) + 1 :]}"
        return None

    def _glob(self, pattern: str) -> str:
        if self._root is None:
            raise ValueError(NO_WORKSPACE_ERROR)
        if not pattern.startswith("/"):
            if self._plan_only and any(part in {".", ".."} for part in pattern.split("/")):
                raise ValueError(PLAN_ONLY_ERROR)
            return pattern.removeprefix("plan/") if self._plan_only else pattern
        virtual = validate_path(pattern)
        if virtual in {"/", WORKSPACE}:
            return "**/*"
        if self._plan_only:
            for prefix in (f"{WORKSPACE}/plan", f"{self._root}/plan"):
                if virtual == prefix:
                    return "**/*"
                if virtual.startswith(f"{prefix}/"):
                    return virtual[len(prefix) + 1 :]
            if virtual.startswith(f"{WORKSPACE}/**"):
                return virtual.removeprefix(f"{WORKSPACE}/")
            raise ValueError(PLAN_ONLY_ERROR)
        return virtual.removeprefix(f"{WORKSPACE}/").lstrip("/")

    def _info(self, info: FileInfo) -> FileInfo | None:
        path = self._virtual(info.get("path", ""))
        return {**info, "path": path} if path else None

    def _mask(self, error: str | None, real_path: str) -> str | None:
        """Backend errors interpolate the resolved real path (e.g.
        ``/workspace/<engagement-slug>/...``); rewrite it back to the virtual
        path the agent originally asked for so engagement-internal naming
        never leaks into tool output."""
        if not error or self._root is None:
            return error
        return error.replace(real_path, self._virtual(real_path) or real_path)

    def ls(self, path: str) -> LsResult:
        self._ensure_root()
        try:
            real_path = self._real(path)
        except ValueError as e:
            return LsResult(error=str(e))
        result = self._backend.ls(real_path)
        if result.error:
            return LsResult(error=self._mask(result.error, real_path))
        return LsResult(
            entries=[mapped for item in result.entries or [] if (mapped := self._info(item))]
        )

    def ls_info(self, path: str) -> list[FileInfo]:
        result = self.ls(path)
        return result.entries or []

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        self._ensure_root()
        try:
            real_path = self._real(file_path)
        except ValueError as e:
            return ReadResult(error=str(e))
        result = self._backend.read(real_path, offset=offset, limit=limit)
        return (
            replace(result, error=self._mask(result.error, real_path)) if result.error else result
        )

    def _roe_locked(self, real_path: str) -> bool:
        return real_path.endswith("/plan/roe.json")

    def write(self, file_path: str, content: str) -> WriteResult:
        self._ensure_root()
        try:
            real_path = self._real(file_path)
        except ValueError as e:
            return WriteResult(error=str(e))
        if self._roe_locked(real_path):
            return WriteResult(error=LOCKED_ROE_ERROR)
        result = self._backend.write(real_path, content)
        if result.error:
            masked = self._mask(result.error, real_path)
            if masked and "already exists" in masked.lower():
                # deepagents backends refuse to overwrite by design (an
                # overwrite would force the agent to re-emit the whole file for
                # a one-line change — wasteful, and it re-triggers max_tokens
                # truncation on large files). The raw backend messages vary
                # ("File already exists" from the sandbox backend, a longer one
                # from state/filesystem); normalize to a single actionable
                # instruction so the agent switches to edit_file instead of
                # guessing.
                virtual = self._virtual(real_path) or file_path
                masked = (
                    f"{virtual} already exists — use edit_file to modify it, "
                    "or delete it first if you intend to replace it."
                )
            return replace(result, error=masked)
        path = self._virtual(result.path or "") if result.path else None
        return replace(result, path=path) if path else result

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        self._ensure_root()
        try:
            real_path = self._real(file_path)
        except ValueError as e:
            return EditResult(error=str(e))
        if self._roe_locked(real_path):
            return EditResult(error=LOCKED_ROE_ERROR)
        result = self._backend.edit(real_path, old_string, new_string, replace_all)
        if result.error:
            return replace(result, error=self._mask(result.error, real_path))
        path = self._virtual(result.path or "") if result.path else None
        return replace(result, path=path) if path else result

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        self._ensure_root()
        try:
            real_path = self._real(path)
            safe_glob = self._glob(glob) if self._plan_only and glob is not None else glob
        except ValueError as e:
            return GrepResult(error=str(e))
        result = self._backend.grep(pattern, path=real_path, glob=safe_glob)
        if result.error:
            return GrepResult(error=self._mask(result.error, real_path))
        return GrepResult(
            matches=[
                {**match, "path": mapped}
                for match in result.matches or []
                if (mapped := self._virtual(match.get("path", "")))
            ]
        )

    def grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list[GrepMatch] | str:
        result = self.grep(pattern, path=path, glob=glob)
        return result.error if result.error else result.matches or []

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        # ``path`` is ``str | None`` to match the deepagents BackendProtocol
        # signature (it became optional in deepagents 0.6.x); None means the
        # engagement root, preserving the previous ``path="/"`` default.
        self._ensure_root()
        try:
            real_pattern = self._glob(pattern)
            real_path = self._real(path if path is not None else "/")
        except ValueError as e:
            return GlobResult(error=str(e))
        result = self._backend.glob(real_pattern, path=real_path)
        if result.error:
            return GlobResult(error=self._mask(result.error, real_path))
        return GlobResult(
            matches=[mapped for item in result.matches or [] if (mapped := self._info(item))]
        )

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        result = self.glob(pattern, path=path)
        return result.matches or []

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        try:
            real_paths = [self._real(path) for path in paths]
        except ValueError:
            return [
                FileDownloadResponse(path=path, content=None, error="invalid_path")
                for path in paths
            ]
        result = self._backend.download_files(real_paths)
        return [
            FileDownloadResponse(path=paths[i], content=response.content, error=response.error)
            for i, response in enumerate(result)
        ]


def _workspace_from_runtime(runtime: Any) -> str | None:
    state = getattr(runtime, "state", {}) or {}
    if hasattr(state, "get") and state.get("workspace_path"):
        return str(state["workspace_path"])
    configurable = (getattr(runtime, "config", {}) or {}).get("configurable", {})
    if isinstance(configurable, dict) and configurable.get("workspace_path"):
        return str(configurable["workspace_path"])
    return None


def _rebind_sandbox_per_run(backend: BackendProtocol, runtime: Any = None) -> BackendProtocol:
    """Re-resolve the workspace sandbox transport from THIS run's config.

    The agent backend is composed ONCE at construction
    (``make_agent_backend(build_sandbox_backend())``) — no run context exists
    yet, so its ``/workspace`` ``HTTPSandbox`` binds to the process-wide env
    endpoint (``SANDBOX_URL``). In a SHARED langgraph serving many engagements
    that single endpoint cannot reach a per-engagement sandbox, so every
    filesystem op (read / write / ls / glob / grep / edit / download) would land
    in the shared sidecar instead of the run's OWN sandbox — even though the bash
    tool already routes per-run via ``configurable.sandbox_url`` (see
    ``tools/bash/bash.py:get_sandbox``). That left bash and the filesystem
    pointing at different sandboxes for the same run.

    Re-running ``build_sandbox_backend()`` here resolves the endpoint against the
    current run's config first (``configurable.sandbox_url`` / ``sandbox_token``)
    and the env second — so a multi-tenant run reaches its own per-engagement
    sandbox while single-tenant / dev runs (no per-run url) resolve to the same
    env endpoint as before (behaviour unchanged). Only the ``/workspace`` default
    is swapped; ``/skills/`` and any other routes are preserved. Non-sandbox
    backends (e.g. a plain ``StateBackend`` in tests) are returned untouched.

    ``runtime`` is the middleware runtime passed by ``_get_backend``. We forward
    its ``runtime.config`` explicitly to ``build_sandbox_backend`` so the endpoint
    is resolved from the run config the middleware ALREADY holds — reliable inside
    a SUB-AGENT, where the ambient ``get_config()`` contextvar is not seeded and
    would otherwise fall back to the env sidecar (that split filesystem ops from
    the bash tool, which reads its injected config and reached the run's own VM).
    """
    # Lazy imports: avoid any import-ordering coupling between the middleware and
    # the backends package, and keep the env-resolution call out of import time.
    from deepagents.backends import CompositeBackend

    from decepticon.backends import build_sandbox_backend
    from decepticon.backends.http_sandbox import HTTPSandbox

    config = getattr(runtime, "config", None)
    if isinstance(backend, HTTPSandbox):
        return build_sandbox_backend(config)
    if isinstance(backend, CompositeBackend) and isinstance(backend.default, HTTPSandbox):
        return CompositeBackend(
            default=build_sandbox_backend(config),
            routes=backend.routes,
            artifacts_root=backend.artifacts_root,
        )
    return backend


class FilesystemMiddleware(BaseFilesystemMiddleware):
    """FilesystemMiddleware with Decepticon's bash tool as the only executor."""

    def __init__(self, *, plan_only: bool = False, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._plan_only = plan_only
        self.tools = filesystem_tools_without_execute(self.tools)

    def _get_backend(self, runtime) -> BackendProtocol:
        # Resolve the sandbox transport PER-RUN (config.sandbox_url) before
        # wrapping it in the engagement workspace scoper, so a shared langgraph
        # routes each engagement's filesystem ops to ITS OWN sandbox — matching
        # the bash tool. See _rebind_sandbox_per_run.
        return EngagementFilesystemBackend(
            _rebind_sandbox_per_run(super()._get_backend(runtime), runtime),
            _workspace_from_runtime(runtime),
            plan_only=self._plan_only,
        )
