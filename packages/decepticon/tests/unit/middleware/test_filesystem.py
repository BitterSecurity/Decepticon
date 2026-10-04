from __future__ import annotations

from types import SimpleNamespace

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import (
    EditResult,
    FileDownloadResponse,
    FileInfo,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.middleware.filesystem import FilesystemMiddleware as BaseFilesystemMiddleware

from decepticon.agents.middleware_slots import _make_filesystem
from decepticon.backends.http_sandbox import HTTPSandbox
from decepticon.middleware.filesystem import (
    EngagementFilesystemBackend,
    FilesystemMiddleware,
    _rebind_sandbox_per_run,
    _workspace_from_runtime,
)


class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def ls_info(self, path: str) -> list[FileInfo]:
        self.calls.append(("ls_info", path))
        return [{"path": f"{path}/plan/roe.json", "is_dir": False}]

    def ls(self, path: str) -> LsResult:
        return LsResult(entries=self.ls_info(path))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        self.calls.append(("read", (file_path, offset, limit)))
        return ReadResult(file_data={"content": f"read:{file_path}", "encoding": "utf-8"})

    def write(self, file_path: str, content: str) -> WriteResult:
        self.calls.append(("write", (file_path, content)))
        return WriteResult(path=file_path)

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        self.calls.append(("glob_info", (pattern, path)))
        return [{"path": "plan/roe.json", "is_dir": False}]

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=self.glob_info(pattern, path))

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None):
        self.calls.append(("grep_raw", (pattern, path, glob)))
        suffix = "roe.json" if (path or "").endswith("/plan") else "plan/roe.json"
        return [{"path": f"{path}/{suffix}", "line": 1, "text": "target"}]

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return GrepResult(matches=self.grep_raw(pattern, path, glob))

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        self.calls.append(("edit", (file_path, old_string, new_string, replace_all)))
        return EditResult(path=file_path, occurrences=1)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        self.calls.append(("download_files", paths))
        return [FileDownloadResponse(path=p, content=b"image") for p in paths]


def test_maps_virtual_workspace_paths_to_engagement_root() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    result = scoped.read("/workspace/plan/roe.json")

    assert result.file_data == {
        "content": "read:/workspace/test/plan/roe.json",
        "encoding": "utf-8",
    }
    assert backend.calls[-1] == ("read", ("/workspace/test/plan/roe.json", 0, 2000))


def test_soundwave_filesystem_reads_roe_and_edits_planning_documents() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test", plan_only=True)

    assert scoped.read("/workspace/plan/roe.json").file_data == {
        "content": "read:/workspace/test/plan/roe.json",
        "encoding": "utf-8",
    }
    assert scoped.edit("/workspace/plan/conops.json", "old", "new").path == "/workspace/plan/conops.json"
    assert scoped.grep("scope", path="/workspace").matches == [
        {"path": "/workspace/plan/roe.json", "line": 1, "text": "target"}
    ]
    assert backend.calls[-1] == ("grep_raw", ("scope", "/workspace/test/plan", None))


def test_soundwave_cannot_change_roe_even_when_it_is_missing() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test", plan_only=True)

    assert "read-only" in (scoped.write("/workspace/plan/roe.json", "{}").error or "")
    assert "read-only" in (scoped.edit("/workspace/plan/roe.json", "old", "new").error or "")
    assert not any(call[0] in {"write", "edit"} for call in backend.calls)
    assert scoped.write("/workspace/plan/conops.json", "{}").path == "/workspace/plan/conops.json"

    runtime_scoped = EngagementFilesystemBackend(backend, "/workspace/test")
    assert "read-only" in (runtime_scoped.edit("/workspace/plan/roe.json", "old", "new").error or "")


def test_soundwave_filesystem_rejects_paths_outside_plan() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test", plan_only=True)

    rejected = [
        scoped.read("/workspace/plan/../credentials.json").error,
        scoped.read("/workspace/credentials.json").error,
        scoped.read("/workspace/test/credentials.json").error,
        scoped.write("/workspace/exploit/probe.txt", "x").error,
        scoped.edit("/workspace/credentials.json", "old", "new").error,
        scoped.ls("/workspace/private").error,
        scoped.glob("/workspace/private/**/*.json").error,
        scoped.glob("../credentials*").error,
        scoped.grep("token", glob="../credentials*").error,
        scoped.download_files(["/workspace/credentials.json"])[0].error,
    ]
    assert all(rejected)
    assert not any(call[0] in {"read", "write", "edit", "glob_info"} for call in backend.calls)


def test_soundwave_root_and_glob_resolve_to_plan_directory() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test", plan_only=True)

    scoped.ls("/workspace")
    assert backend.calls[-1] == ("ls_info", "/workspace/test/plan")
    scoped.glob("/workspace/plan/**/*.json")
    assert backend.calls[-1] == ("glob_info", ("**/*.json", "/workspace/test/plan"))


def test_soundwave_drops_backend_results_outside_plan() -> None:
    scoped = EngagementFilesystemBackend(RecordingBackend(), "/workspace/test", plan_only=True)

    assert scoped._virtual("/workspace/test/private/secret.json") is None
    assert scoped._virtual("/workspace/test/plan/roe.json") == "/workspace/plan/roe.json"


def test_soundwave_role_binds_plan_only_filesystem_without_execute() -> None:
    backend = RecordingBackend()
    middleware = _make_filesystem(backend=backend, role="soundwave")
    scoped = middleware._get_backend(
        SimpleNamespace(state={"workspace_path": "/workspace/test"}, config={})
    )

    assert "execute" not in {tool.name for tool in middleware.tools}
    scoped.read("/workspace/plan/roe.json")
    assert backend.calls[-1] == ("read", ("/workspace/test/plan/roe.json", 0, 2000))
    assert scoped.read("/workspace/audit/log.json").error
    assert len(backend.calls) == 1


def test_soundwave_next_run_draft_reads_existing_document_from_nested_workspace() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(
        backend,
        "/workspace/org-1/eng-1/drafts/next",
        plan_only=True,
    )

    scoped.read("/workspace/plan/roe.json")
    assert backend.calls[-1] == (
        "read",
        ("/workspace/org-1/eng-1/drafts/next/plan/roe.json", 0, 2000),
    )
    assert scoped.edit("/workspace/plan/conops.json", "old", "new").path == "/workspace/plan/conops.json"
    assert scoped.read("/workspace/org-1/eng-1/plan/roe.json").error


def test_real_path_is_accepted_idempotently() -> None:
    """Passing the already-real engagement path must not double the slug.

    Regression: agent prompts historically advertised the real per-engagement
    path (e.g. ``/workspace/test``) and instructed sub-agents to use it as
    their workspace root. The agent then passed
    ``/workspace/test/exploit/x.txt`` to filesystem tools. Without
    idempotency, ``_real()`` re-prefixed ``self._root`` and produced
    ``/workspace/test/test/exploit/x.txt`` — a duplicated nested directory
    visible on the host as ``~/.decepticon/workspace/test/test/...``. The
    backend now detects "already inside ``self._root``" and returns the
    path unchanged, so both virtual (``/workspace/...``) and real
    (``/workspace/test/...``) inputs converge on the same on-disk file.
    """
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    result = scoped.read("/workspace/test/plan/roe.json")

    assert result.file_data == {
        "content": "read:/workspace/test/plan/roe.json",
        "encoding": "utf-8",
    }
    # Critical: the real-path input is not re-prefixed into
    # /workspace/test/test/plan/roe.json.
    assert backend.calls[-1] == ("read", ("/workspace/test/plan/roe.json", 0, 2000))


def test_returns_virtual_paths_to_agent() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    assert scoped.ls("/workspace").entries == [
        {"path": "/workspace/plan/roe.json", "is_dir": False}
    ]
    write_result = scoped.write("/workspace/findings/FIND-001.md", "x")

    assert write_result.path == "/workspace/findings/FIND-001.md"


def test_write_exists_error_is_actionable_and_masks_real_path() -> None:
    """A no-overwrite backend error is rewritten to point the agent at
    edit_file, using the virtual (not the real engagement) path."""

    class ExistsBackend(RecordingBackend):
        def write(self, file_path: str, content: str) -> WriteResult:
            # Mirror the deepagents sandbox backend's terse message shape,
            # which does not itself mention edit_file.
            return WriteResult(error=f"Error: File already exists: '{file_path}'")

    scoped = EngagementFilesystemBackend(ExistsBackend(), "/workspace/test")
    result = scoped.write("/workspace/findings/FIND-001.md", "x")

    assert result.error is not None
    assert "/workspace/findings/FIND-001.md already exists" in result.error
    assert "edit_file" in result.error
    # Real engagement path must not leak into the message.
    assert "/workspace/test" not in result.error


def test_scopes_glob_and_grep_without_exposing_real_engagement_path() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    assert scoped.glob("/workspace/**/*.json").matches == [
        {"path": "/workspace/plan/roe.json", "is_dir": False}
    ]
    assert backend.calls[-1] == ("glob_info", ("**/*.json", "/workspace/test"))

    assert scoped.grep("target", path="/workspace").matches == [
        {"path": "/workspace/plan/roe.json", "line": 1, "text": "target"}
    ]
    assert backend.calls[-1] == ("grep_raw", ("target", "/workspace/test", None))


def test_filesystem_middleware_removes_execute_without_rewriting_descriptions() -> None:
    base = BaseFilesystemMiddleware(backend=RecordingBackend())
    middleware = FilesystemMiddleware(backend=RecordingBackend())
    base_descriptions = {tool.name: tool.description for tool in base.tools}
    descriptions = {tool.name: tool.description for tool in middleware.tools}

    assert "execute" not in descriptions
    assert descriptions == {
        name: description for name, description in base_descriptions.items() if name != "execute"
    }


def test_missing_engagement_workspace_fails_closed() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, None)

    assert scoped.ls("/workspace").error is not None
    assert scoped.read("/workspace/plan/roe.json").error is not None
    assert scoped.glob("**/*.json").error is not None
    assert scoped.grep("target", path="/workspace").error is not None
    assert backend.calls == []


def test_root_workspace_accepted_as_engagement_root() -> None:
    """Launcher mode binds the engagement directory directly at ``/workspace``.

    The bare root must be accepted as a valid engagement root so filesystem
    tools work without a slug prefix. ``ls /workspace`` must hit the backend
    at ``/workspace`` (no doubling) and surface entries unchanged.
    """
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace")

    result = scoped.ls("/workspace")

    assert result.error is None
    assert result.entries == [{"path": "/workspace/plan/roe.json", "is_dir": False}]
    assert backend.calls[-1] == ("ls_info", "/workspace")


def test_root_workspace_no_prefix_doubling_under_engagement_root() -> None:
    """Reads under ``/workspace`` in launcher mode must not get prefixed twice."""
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace")

    scoped.read("/workspace/plan/roe.json")

    assert backend.calls[-1] == ("read", ("/workspace/plan/roe.json", 0, 2000))


def test_root_workspace_accepts_trailing_slash() -> None:
    """``/workspace/`` (trailing slash) is the same engagement root."""
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/")

    result = scoped.ls("/workspace")

    assert result.error is None
    assert backend.calls[-1] == ("ls_info", "/workspace")


def test_traversal_path_fails_closed_does_not_silently_coerce() -> None:
    """``..`` traversal must fail closed rather than collapse to ``/workspace``.

    Without the ``os.path.normpath`` guard the slug regex would accept the
    component, which would then resolve to a path outside the engagement.
    """
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/../etc")

    assert scoped.ls("/workspace").error is not None
    assert backend.calls == []


def test_invalid_path_outside_workspace_fails_closed() -> None:
    """Anything not under ``/workspace`` must fail closed (no silent coerce)."""
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/etc")

    assert scoped.ls("/workspace").error is not None
    assert backend.calls == []


def test_engagement_root_is_materialized_on_first_tool_call() -> None:
    """The engagement subdir under ``/workspace`` must exist by the time the
    first agent tool call lands. Backends create dirs lazily on the first
    ``write``, so without this materialization step the first ``ls`` (the
    planner's "what is here?" probe) trips ``path_not_found`` against the
    not-yet-created subdir. Verify a single marker write is issued on
    construction, and that it happens before any other backend call.
    """
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/eng-1")
    # No backend traffic until first tool call.
    assert backend.calls == []
    scoped.ls("/workspace")
    # Marker write precedes the ls.
    op_names = [name for (name, _) in backend.calls]
    assert op_names == ["write", "ls_info"]
    first_op, first_args = backend.calls[0]
    assert first_op == "write"
    assert first_args[0] == "/workspace/eng-1/.engagement"  # type: ignore[index]


def test_engagement_root_materialized_only_once_across_tool_calls() -> None:
    """The marker write is idempotent at the middleware level — running
    five tool calls in a row must yield exactly one materialization write."""
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/eng-1")
    scoped.ls("/workspace")
    scoped.ls("/workspace/plan")
    scoped.read("/workspace/plan/roe.json")
    scoped.glob("**/*.json")
    scoped.grep("target", path="/workspace")
    write_calls = [op for (op, _) in backend.calls if op == "write"]
    assert len(write_calls) == 1


class _ErrorBackend(RecordingBackend):
    """Surface a path_not_found error that interpolates the resolved real
    path. Mirrors what the HTTPSandbox backend does on a missing dir."""

    def ls(self, path: str) -> LsResult:
        self.calls.append(("ls_info", path))
        return LsResult(error=f"Path '{path}': path_not_found")


def test_backend_error_does_not_leak_engagement_internal_path() -> None:
    """Backend error strings interpolate the resolved real path (e.g.
    ``/workspace/<engagement-slug>``); the middleware must rewrite that back
    to the virtual path the agent originally asked for, so error output
    never exposes harness-internal naming."""
    backend = _ErrorBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/eng-1")

    result = scoped.ls("/workspace")

    assert result.error is not None
    # Agent asked for "/workspace" — error must echo "/workspace", not
    # "/workspace/eng-1".
    assert "/workspace/eng-1" not in result.error
    assert "/workspace" in result.error


class _EditErrorBackend(RecordingBackend):
    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        self.calls.append(("edit", (file_path, old_string, new_string, replace_all)))
        return EditResult(error=f"Path '{file_path}': not_found")


class _WriteErrorBackend(RecordingBackend):
    def write(self, file_path: str, content: str) -> WriteResult:
        self.calls.append(("write", (file_path, content)))
        if file_path.endswith(".engagement"):
            return WriteResult(path=file_path)
        return WriteResult(error=f"Path '{file_path}': permission_denied")


class _GrepErrorBackend(RecordingBackend):
    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        self.calls.append(("grep", (pattern, path, glob)))
        return GrepResult(error=f"Path '{path}': path_not_found")


class _GlobErrorBackend(RecordingBackend):
    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        self.calls.append(("glob", (pattern, path)))
        return GlobResult(error=f"Path '{path}': path_not_found")


class _GrepOutsideRootBackend(RecordingBackend):
    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        self.calls.append(("grep", (pattern, path, glob)))
        return GrepResult(matches=[{"path": "/elsewhere/secret", "line": 1, "text": "x"}])


class _RaisingWriteBackend(RecordingBackend):
    def write(self, file_path: str, content: str) -> WriteResult:
        raise RuntimeError("backend write exploded")


def test_edit_success_path_rewrite_maps_virtual_to_real_and_back() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    result = scoped.edit("/workspace/x.txt", "old", "new")

    assert backend.calls[-1] == ("edit", ("/workspace/test/x.txt", "old", "new", False))
    assert result.error is None
    assert result.path == "/workspace/x.txt"


def test_edit_error_masking_strips_real_engagement_path() -> None:
    backend = _EditErrorBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")
    scoped._root_ensured = True

    result = scoped.edit("/workspace/x.txt", "a", "b")

    assert result.error is not None
    assert "/workspace/test" not in result.error
    assert "/workspace/x.txt" in result.error


def test_edit_fail_closed_when_root_is_none() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, None)

    result = scoped.edit("/workspace/x.txt", "a", "b")

    assert result.error is not None
    assert backend.calls == []


def test_edit_replace_all_true_reaches_backend() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    scoped.edit("/workspace/x.txt", "a", "b", replace_all=True)

    assert backend.calls[-1] == ("edit", ("/workspace/test/x.txt", "a", "b", True))


def test_write_error_masking_strips_real_engagement_path() -> None:
    backend = _WriteErrorBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")
    scoped._root_ensured = True

    result = scoped.write("/workspace/findings/x.md", "data")

    assert result.error is not None
    assert "/workspace/test" not in result.error
    assert "/workspace/findings/x.md" in result.error


def test_grep_error_masking_strips_real_engagement_path() -> None:
    backend = _GrepErrorBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")
    scoped._root_ensured = True

    result = scoped.grep("pat", path="/workspace")

    assert result.error is not None
    assert "/workspace/test" not in result.error


def test_grep_raw_success_returns_list_of_matches() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    result = scoped.grep_raw("target", path="/workspace")

    assert isinstance(result, list)
    assert len(result) > 0
    assert result[0]["path"].startswith("/workspace/")


def test_grep_raw_error_returns_masked_error_string() -> None:
    backend = _GrepErrorBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")
    scoped._root_ensured = True

    result = scoped.grep_raw("p", path="/workspace")

    assert isinstance(result, str)
    assert "/workspace/test" not in result


def test_glob_error_masking_strips_real_engagement_path() -> None:
    backend = _GlobErrorBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")
    scoped._root_ensured = True

    result = scoped.glob("**/*.json")

    assert result.error is not None
    assert "/workspace/test" not in result.error


def test_glob_info_success_returns_list_with_virtual_paths() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    result = scoped.glob_info("**/*.json")

    assert isinstance(result, list)
    assert result[0]["path"] == "/workspace/plan/roe.json"


def test_ls_info_success_returns_list_of_entries() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    result = scoped.ls_info("/workspace")

    assert result == [{"path": "/workspace/plan/roe.json", "is_dir": False}]


def test_ls_info_returns_empty_list_on_backend_error() -> None:
    backend = _ErrorBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/eng-1")

    result = scoped.ls_info("/workspace")

    assert result == []


def test_download_files_success_maps_real_paths_to_backend_and_returns_virtual() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    result = scoped.download_files(["/workspace/loot/a.bin", "/workspace/loot/b.bin"])

    assert backend.calls[-1] == (
        "download_files",
        ["/workspace/test/loot/a.bin", "/workspace/test/loot/b.bin"],
    )
    paths = [r.path for r in result]
    assert paths == ["/workspace/loot/a.bin", "/workspace/loot/b.bin"]
    assert result[0].content == b"image"
    assert result[1].content == b"image"


def test_download_files_invalid_path_when_root_is_none_returns_error_responses() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, None)

    result = scoped.download_files(["/workspace/a", "/workspace/b"])

    assert all(r.error == "invalid_path" for r in result)
    assert all(r.content is None for r in result)
    assert result[0].path == "/workspace/a"
    assert result[1].path == "/workspace/b"
    assert "download_files" not in [op for (op, _) in backend.calls]


def test_download_files_traversal_path_returns_invalid_path_error() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    result = scoped.download_files(["/workspace/../etc/passwd"])

    assert len(result) == 1
    assert result[0].error == "invalid_path"
    assert result[0].content is None


def test_virtual_returns_none_for_path_outside_root_and_grep_excludes_it() -> None:
    backend = _GrepOutsideRootBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")
    scoped._root_ensured = True

    result = scoped.grep("p", path="/workspace")

    assert result.error is None
    assert result.matches == []


def test_glob_root_pattern_translates_workspace_to_double_glob() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    scoped.glob("/workspace")

    assert backend.calls[-1] == ("glob_info", ("**/*", "/workspace/test"))


def test_glob_slash_root_also_translates_to_double_glob() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    scoped.glob("/")

    assert backend.calls[-1] == ("glob_info", ("**/*", "/workspace/test"))


def test_glob_relative_pattern_passes_through_unchanged() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    scoped.glob("*.json")

    assert backend.calls[-1] == ("glob_info", ("*.json", "/workspace/test"))


def test_glob_fail_closed_when_root_is_none() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, None)

    result = scoped.glob("**/*")

    assert result.error is not None
    assert backend.calls == []


def test_ensure_root_swallows_write_exception_and_marks_ensured() -> None:
    backend = _RaisingWriteBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/eng")

    scoped.ls("/workspace")

    assert scoped._root_ensured is True


def test_ensure_root_issues_no_second_write_across_multiple_calls() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/eng")
    scoped._root_ensured = True

    scoped.ls("/workspace")

    write_calls = [op for (op, _) in backend.calls if op == "write"]
    assert len(write_calls) == 0


def test_mask_returns_error_unchanged_when_root_is_none() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")
    scoped._root = None

    result = scoped._mask("some error mentioning /workspace/test/x", "/workspace/test/x")

    assert result == "some error mentioning /workspace/test/x"


def test_mask_returns_empty_error_unchanged() -> None:
    backend = RecordingBackend()
    scoped = EngagementFilesystemBackend(backend, "/workspace/test")

    result = scoped._mask("", "/workspace/test/x")

    assert result == ""


def test_workspace_from_runtime_reads_state_dict() -> None:
    class _FakeRuntime:
        state = {"workspace_path": "/workspace/eng"}
        config = {}

    assert _workspace_from_runtime(_FakeRuntime()) == "/workspace/eng"


def test_workspace_from_runtime_reads_config_configurable() -> None:
    class _FakeRuntime:
        state = {}
        config = {"configurable": {"workspace_path": "/workspace/web"}}

    assert _workspace_from_runtime(_FakeRuntime()) == "/workspace/web"


def test_workspace_from_runtime_returns_none_when_both_empty() -> None:
    class _FakeRuntime:
        state = {}
        config = {"configurable": {}}

    assert _workspace_from_runtime(_FakeRuntime()) is None


def test_workspace_from_runtime_returns_none_when_attrs_absent() -> None:
    class _FakeRuntime:
        pass

    assert _workspace_from_runtime(_FakeRuntime()) is None


def test_filesystem_middleware_get_backend_wraps_in_engagement_backend() -> None:
    class _FakeRuntime:
        state = {}
        config = {"configurable": {"workspace_path": "/workspace/eng"}}

    mw = FilesystemMiddleware(backend=RecordingBackend())
    result = mw._get_backend(_FakeRuntime())

    assert isinstance(result, EngagementFilesystemBackend)
    assert result._root == "/workspace/eng"


def test_rebind_passes_through_non_sandbox_backend() -> None:
    """A plain (non-sandbox) backend is returned untouched — single-tenant /
    dev / test setups that don't use ``HTTPSandbox`` are never rebound."""
    backend = RecordingBackend()
    assert _rebind_sandbox_per_run(backend) is backend


def test_rebind_reresolves_bare_http_sandbox_per_run(monkeypatch) -> None:
    """A bare ``HTTPSandbox`` (the construction-time env endpoint) is replaced
    by the per-run resolution of ``build_sandbox_backend()`` — which consults
    ``configurable.sandbox_url`` first, env second."""
    per_run = HTTPSandbox("http://per-run-vm:9999", token="t")
    monkeypatch.setattr("decepticon.backends.build_sandbox_backend", lambda config=None: per_run)

    env = HTTPSandbox("http://shared-sidecar:9999")
    assert _rebind_sandbox_per_run(env) is per_run


def test_rebind_swaps_composite_default_preserving_routes(monkeypatch) -> None:
    """For the real agent backend — a ``CompositeBackend`` whose ``/workspace``
    default is the env ``HTTPSandbox`` and whose ``/skills/`` route reads the
    local tree — only the workspace default is re-resolved per-run. The
    ``/skills/`` route and ``artifacts_root`` are preserved, and the cached
    construction-time composite is left untouched."""
    per_run = HTTPSandbox("http://per-run-vm:9999", token="t")
    monkeypatch.setattr("decepticon.backends.build_sandbox_backend", lambda config=None: per_run)

    skills = RecordingBackend()
    env = HTTPSandbox("http://shared-sidecar:9999")
    composite = CompositeBackend(default=env, routes={"/skills/": skills}, artifacts_root="/art")

    rebound = _rebind_sandbox_per_run(composite)

    assert isinstance(rebound, CompositeBackend)
    assert rebound.default is per_run  # workspace transport re-resolved per-run
    assert rebound.routes == {"/skills/": skills}  # /skills route preserved
    assert rebound.artifacts_root == "/art"  # artifacts_root preserved
    assert composite.default is env  # original (cached) composite untouched


def test_rebind_leaves_composite_with_non_sandbox_default_untouched(monkeypatch) -> None:
    """A composite whose default is NOT a sandbox (e.g. a dev StateBackend) is
    returned unchanged — we never inject a sandbox where there wasn't one."""
    monkeypatch.setattr(
        "decepticon.backends.build_sandbox_backend",
        lambda config=None: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    state_like = RecordingBackend()
    composite = CompositeBackend(default=state_like, routes={"/skills/": RecordingBackend()})

    assert _rebind_sandbox_per_run(composite) is composite


def test_get_backend_reresolves_sandbox_transport_per_run(monkeypatch) -> None:
    """End-to-end: a shared langgraph (one construction-time composite bound to
    the env sidecar) re-resolves each run's filesystem transport to that run's
    own sandbox before scoping it to the engagement workspace."""
    per_run = HTTPSandbox("http://per-run-vm:9999", token="t")
    monkeypatch.setattr("decepticon.backends.build_sandbox_backend", lambda config=None: per_run)

    env = HTTPSandbox("http://shared-sidecar:9999")
    composite = CompositeBackend(default=env, routes={"/skills/": RecordingBackend()})

    class _FakeRuntime:
        state = {}
        config = {"configurable": {"workspace_path": "/workspace/eng"}}

    mw = FilesystemMiddleware(backend=composite)
    result = mw._get_backend(_FakeRuntime())

    assert isinstance(result, EngagementFilesystemBackend)
    assert isinstance(result._backend, CompositeBackend)
    assert result._backend.default is per_run  # routed to THIS run's sandbox
    assert result._root == "/workspace/eng"
