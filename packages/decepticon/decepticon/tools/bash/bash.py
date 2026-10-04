"""Bash tool for the Decepticon agent.

Thin wrapper around HTTPSandbox.execute_tmux(). All tmux session
management and PS1 polling logic lives in decepticon/sandbox_kernel/
(tmux.py + base.py), shared with the in-container daemon.

The sandbox instance is injected at agent startup via set_sandbox().

Context engineering: multi-tier output management
-------------------------------------------------
Inspired by Claude Code's bash tool best practices:

1. INLINE (≤15K chars) — returned directly in tool result
2. OFFLOAD (15K–100K chars) — saved to <engagement>/.scratch/, summary returned
3. HARD_LIMIT (>5M chars) — size watchdog in sandbox kills the command

Additional post-processing:
- ANSI escape code stripping (saves LLM tokens)
- Repetitive line compression (nmap, nuclei patterns)
- Surrogate character sanitization (UTF-8 safety)
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import os
import re
import time
from collections import OrderedDict
from functools import partial

from anyio import to_thread
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from decepticon.sandbox_kernel.base import SandboxBase
from decepticon.sandbox_kernel.tmux import _interpret_exit_code
from decepticon.tools.bash.protocol import BashSandboxProtocol

log = logging.getLogger("decepticon.tools.bash.bash")

# Sandbox lookup is a two-level chain:
#   1. ``_sandbox_var`` — per-context override. Honoured first so a
#      request-scoped middleware (multi-tenant) can pin a
#      different HTTPSandbox for the lifetime of a request without
#      touching the module-level default.
#   2. ``_sandbox_default`` — module-level fallback. Required because
#      Python ``contextvars`` do not propagate across thread boundaries,
#      and LangGraph platform dispatches sub-agent tool nodes in a
#      ThreadPoolExecutor whose workers inherit an empty context. Before
#      this fallback existed, every sub-agent ``bash()`` call raised
#      ``RuntimeError: HTTPSandbox not initialized`` even though
#      ``set_sandbox`` ran at agent-factory build time in the main
#      thread. Restoring the cross-thread default keeps the public
#      ContextVar API (used for per-request overrides) intact.
_sandbox_var: contextvars.ContextVar[BashSandboxProtocol | None] = contextvars.ContextVar(
    "decepticon_bash_sandbox",
    default=None,
)
_sandbox_default: BashSandboxProtocol | None = None
_current_workspace_path: contextvars.ContextVar[str] = contextvars.ContextVar(
    "decepticon_bash_workspace_path",
    default="/workspace",
)

# ─── Output size thresholds ──────────────────────────────────────────────
INLINE_LIMIT = 15_000  # ≤15K chars: return inline; >15K: offload to <engagement>/.scratch/
# >5M: size watchdog in sandbox_kernel/tmux.py kills the command (SIZE_WATCHDOG_CHARS)

# Tool-status control markers — NEVER offloaded to .scratch/ (they are
# messages the agent parses, not command output, and they carry their own
# truncation). _truncate() caps at 30K > INLINE_LIMIT (15K), so a status-
# bearing screen >15K could otherwise be re-offloaded and double-wrapped as if
# it were real output. This list must stay in sync with the full marker
# taxonomy emitted by sandbox_kernel/tmux.py + this module.
_STATUS_PREFIXES = (
    "[BACKGROUND]",
    "[RUNNING",
    "[DONE",
    "[IDLE]",
    "[KILLED]",
    "[EMPTY]",
    "[STALE]",
    "[AUTO-BACKGROUND]",
    "[SIZE LIMIT]",
    "[TIMEOUT]",
    "[ERROR]",
    "[UNKNOWN]",
    "[session:",
    "[cwd:",
)

# ─── Scratch-file TTL prune (bounds <engagement>/.scratch/ growth) ────────
# Files persist long enough for the agent's grep/read multi-pass workflow,
# then expire so the dir does not grow unboundedly across long engagements.
# Process-level throttle keeps the prune off the hot path of every bash call.
SCRATCH_TTL_MINUTES = 60
SCRATCH_PRUNE_INTERVAL = 600  # seconds between prune attempts (per process)
_scratch_prune_state: dict[str, float] = {}

# ─── Passive-read stale-poll detection ────────────────────────────────────
# Empty-command `bash` and `bash_output` are passive reads: they sample the
# session state without sending input. When the agent runs N consecutive
# passive reads on the same session and the underlying output is unchanged
# every time, the session is wedged (or the background job has gone quiet)
# and further polling cannot unwedge it — the documented next step is to
# kill and pivot. Track per-(workspace, session) hashes and inject a [STALE]
# hint once the threshold is hit. Resets on any state-changing event:
# non-empty command, output diff, kill, or new background job.
_STALE_PASSIVE_READS = 3  # consecutive identical reads before [STALE] hint
# Bound the tracker: a long-lived server otherwise accumulates one entry per
# (workspace, session) forever. Cap by both count (LRU) and age (TTL) so
# idle/abandoned sessions are reaped on access; `_passive_clock` is module-
# patchable so tests can drive time without sleeping.
_PASSIVE_MAX_ENTRIES = 128
_PASSIVE_TTL_SECONDS = 300.0
_passive_clock = time.monotonic
_passive_read_state: "OrderedDict[tuple[str, str], tuple[float, list[str]]]" = OrderedDict()


def _passive_key(workspace_path: str, session: str) -> tuple[str, str]:
    return (workspace_path, session)


def _prune_expired_passive(now: float) -> None:
    expired = [k for k, (ts, _) in _passive_read_state.items() if now - ts > _PASSIVE_TTL_SECONDS]
    for k in expired:
        del _passive_read_state[k]


def _track_passive_read(workspace_path: str, session: str, output: str) -> str | None:
    """Record a passive read; return [STALE] hint when threshold tripped."""
    key = _passive_key(workspace_path, session)
    digest = hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest()[:16]
    now = _passive_clock()
    _prune_expired_passive(now)

    entry = _passive_read_state.get(key)
    hashes = entry[1] if entry is not None else []
    hashes.append(digest)
    del hashes[: max(0, len(hashes) - _STALE_PASSIVE_READS)]
    _passive_read_state[key] = (now, hashes)
    _passive_read_state.move_to_end(key)

    while len(_passive_read_state) > _PASSIVE_MAX_ENTRIES:
        _passive_read_state.popitem(last=False)

    if len(hashes) < _STALE_PASSIVE_READS or len(set(hashes)) != 1:
        return None
    return (
        f"\n\n[STALE] session='{session}' returned identical output across "
        f"{_STALE_PASSIVE_READS} consecutive passive reads. Either the shell "
        f"is wedged or the background job is quiet — further polling cannot "
        f"unwedge it. Pivot now:\n"
        f"  (a) bash_kill('{session}') and try a different attack vector;\n"
        f"  (b) read the underlying log file directly "
        f"(e.g. cat .sessions/{session}.log) to confirm progress before resuming;\n"
        f"  (c) only continue waiting if a SEPARATE operation is making "
        f"concrete progress.\n"
    )


def _reset_passive_read(workspace_path: str, session: str) -> None:
    """Clear the stale-poll counter on any state-changing event."""
    _passive_read_state.pop(_passive_key(workspace_path, session), None)


# ─── ANSI escape code pattern ────────────────────────────────────────────
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape codes that waste LLM tokens."""
    return _ANSI_ESCAPE.sub("", text)


def _compress_repetitive_lines(text: str, max_repeat: int = 5) -> str:
    """Compress blocks of repetitive lines from scan tools (nmap, nuclei, etc.).

    When >max_repeat consecutive lines share the same prefix pattern,
    keep the first and last few and summarize the middle.
    """
    lines = text.split("\n")
    if len(lines) <= max_repeat * 2:
        return text

    result: list[str] = []
    i = 0
    while i < len(lines):
        # Extract a "signature" — first 20 chars or up to first dynamic token
        line = lines[i]
        sig = line[:20].strip()

        if not sig:
            result.append(line)
            i += 1
            continue

        # Count consecutive lines with the same signature
        j = i + 1
        while j < len(lines) and lines[j][:20].strip() == sig:
            j += 1

        count = j - i
        if count > max_repeat * 2:
            # Keep first max_repeat + last max_repeat, summarize middle
            for k in range(i, i + max_repeat):
                result.append(lines[k])
            skipped = count - max_repeat * 2
            result.append(f"  [... {skipped} similar lines omitted ...]")
            for k in range(j - max_repeat, j):
                result.append(lines[k])
        else:
            for k in range(i, j):
                result.append(lines[k])

        i = j

    return "\n".join(result)


def _sanitize_output(text: str) -> str:
    """Clean tool output: strip surrogates, ANSI codes, compress repetition.

    Processing pipeline:
    1. Re-encode surrogates (UTF-8 safety)
    2. Strip ANSI escape codes (token savings)
    3. Compress repetitive lines (context efficiency)
    """
    # Step 1: surrogate safety
    text = text.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
    # Step 2: strip ANSI
    text = _strip_ansi(text)
    # Step 3: compress repetition
    text = _compress_repetitive_lines(text)
    return text


def set_sandbox(
    sandbox: BashSandboxProtocol,
) -> contextvars.Token[BashSandboxProtocol | None]:
    """Inject the shared HTTPSandbox instance.

    Writes both:
      * ``_sandbox_var`` — the per-context value, returned token can be
        passed to ``_sandbox_var.reset()`` for scoped per-agent isolation.
      * ``_sandbox_default`` — the module-level fallback. Required so
        ``get_sandbox`` returns the right instance when called from a
        thread that did not inherit the calling context (e.g., a
        LangGraph tool node dispatched in a ``ThreadPoolExecutor``).
    """
    global _sandbox_default
    _sandbox_default = sandbox
    return _sandbox_var.set(sandbox)


def _sandbox_from_config(config: RunnableConfig | None) -> BashSandboxProtocol | None:
    """Resolve a per-engagement sandbox from the run's injected config.

    In a SHARED langgraph process serving many engagements concurrently, each
    run carries its own ``configurable.sandbox_url`` / ``sandbox_token`` — the
    endpoint of *that engagement's* isolated sandbox (VM/container). Resolving
    the sandbox from the per-call config — exactly how
    ``_workspace_path_from_config`` already resolves the workspace — routes
    every command to its OWN engagement sandbox, so one tenant's bash can never
    execute against another tenant's sandbox. The ``(url, token)``-keyed
    ``_shared_sandbox`` cache gives each engagement a stable client.

    Returns ``None`` when the config carries no sandbox endpoint (single-tenant
    / dev / OSS self-host), so the caller falls back to the process default.
    """
    configurable = (config or {}).get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    url = configurable.get("sandbox_url")
    if not isinstance(url, str) or not url:
        return None
    token = configurable.get("sandbox_token")
    from decepticon.backends.factory import _shared_sandbox

    return _shared_sandbox(url, token if isinstance(token, str) and token else None)


def get_sandbox(config: RunnableConfig | None = None) -> BashSandboxProtocol | None:
    """Return the HTTPSandbox for this call.

    Resolution order:
      1. the run's per-engagement config (``configurable.sandbox_url``) — so a
         SHARED process fans out to per-engagement sandboxes with no
         cross-tenant bleed;
      2. the per-context ``_sandbox_var`` (a request-scoped override);
      3. the module-level ``_sandbox_default`` for callers running outside that
         context — typically tool nodes the LangGraph runtime dispatched in a
         worker thread, where contextvars from the build-time context are not
         inherited. This is the single-tenant / dev path set by
         ``set_sandbox`` at agent construction.
    """
    from_config = _sandbox_from_config(config)
    if from_config is not None:
        return from_config
    sandbox = _sandbox_var.get()
    if sandbox is not None:
        return sandbox
    return _sandbox_default


def _workspace_path_from_config(config: RunnableConfig | None) -> str:
    configurable = (config or {}).get("configurable", {})
    workspace = configurable.get("workspace_path") if isinstance(configurable, dict) else None
    if isinstance(workspace, str) and workspace.startswith("/workspace"):
        normalized = SandboxBase._normalize_workspace_path(workspace)
        if normalized != "/workspace":
            return normalized

    env_workspace = os.environ.get("DECEPTICON_WORKSPACE_PATH")
    if env_workspace:
        normalized = SandboxBase._normalize_workspace_path(env_workspace)
        if normalized != "/workspace":
            return normalized

    env_slug = os.environ.get("DECEPTICON_ENGAGEMENT")
    if env_slug:
        normalized = SandboxBase._normalize_workspace_path(f"/workspace/{env_slug}")
        if normalized != "/workspace":
            return normalized

    return _current_workspace_path.get()


def _with_workspace_kwargs(workspace_path: str) -> dict[str, str]:
    if workspace_path == "/workspace":
        return {}
    return {"workspace_path": workspace_path}


@contextlib.contextmanager
def bash_workspace(workspace_path: str):
    """Temporarily scope bash tools to one engagement workspace."""
    safe_path = SandboxBase._normalize_workspace_path(workspace_path)
    token = _current_workspace_path.set(safe_path)
    try:
        yield
    finally:
        _current_workspace_path.reset(token)


async def _prune_old_scratch(
    workspace_path: str = "/workspace", sandbox: BashSandboxProtocol | None = None
) -> None:
    """Drop scratch files older than SCRATCH_TTL_MINUTES.

    Throttled to SCRATCH_PRUNE_INTERVAL between attempts so the bash() hot
    path pays for cleanup at most every ~10 minutes per process. Best-effort:
    a failure here must never block the agent's command. ``sandbox`` is the
    already-resolved per-engagement client from the caller, so this never
    re-resolves to the process default (which would target the wrong tenant).
    """
    if sandbox is None:
        sandbox = get_sandbox()
    if sandbox is None or workspace_path == "/workspace":
        return

    now = time.monotonic()
    if now - _scratch_prune_state.get(workspace_path, 0.0) < SCRATCH_PRUNE_INTERVAL:
        return
    _scratch_prune_state[workspace_path] = now
    try:
        await to_thread.run_sync(
            partial(
                sandbox.execute,
                f"find {workspace_path}/.scratch -type f -mmin +{SCRATCH_TTL_MINUTES} "
                "-delete 2>/dev/null || true",
                timeout=5,
            )
        )
    except Exception as e:
        log.warning("scratch prune failed: %s", e)


async def _offload_large_output(
    output: str,
    command: str,
    session: str,
    workspace_path: str = "/workspace",
    sandbox: BashSandboxProtocol | None = None,
) -> str:
    """Save large output to scratch file in sandbox, return compact reference.

    Implements the filesystem-context "scratch pad" pattern:
    - Write full output to <engagement>/.scratch/ for later retrieval
    - Return preview (head 2K + tail 1K) + file path reference
    - Agent can use read_file or grep to access specific parts later

    ``sandbox`` is the already-resolved per-engagement client from the caller,
    so the offload writes into the right tenant's sandbox.
    """
    if sandbox is None:
        sandbox = get_sandbox()
    if sandbox is None:
        raise RuntimeError("bash tool invoked without a sandbox set in the contextvar")

    if workspace_path == "/workspace":
        head_preview = output[:2000].strip()
        tail_preview = output[-1000:].strip()
        line_count = output.count("\n") + 1
        char_count = len(output)
        return (
            f"{head_preview}\n\n"
            f"[... {line_count} lines / {char_count} chars truncated. "
            "No engagement workspace was available, so output was not written "
            "to the root scratch directory. ...]\n\n"
            f"...{tail_preview}"
        )

    # Generate unique filename
    ts = int(time.time())
    cmd_hash = hashlib.md5(command.encode(), usedforsecurity=False).hexdigest()[:6]
    filename = f"{workspace_path}/.scratch/{session}_{ts}_{cmd_hash}.txt"

    # Write via upload_files (docker cp) to avoid shell injection from output content
    await to_thread.run_sync(partial(sandbox.execute, f"mkdir -p {workspace_path}/.scratch"))
    await to_thread.run_sync(partial(sandbox.upload_files, [(filename, output.encode("utf-8"))]))

    # Build compact summary with generous preview (Claude Code: ~10KB preview)
    line_count = output.count("\n") + 1
    char_count = len(output)
    head_preview = output[:2000].strip()
    tail_preview = output[-1000:].strip()

    return (
        f"{head_preview}\n\n"
        f"[... {line_count} lines / {char_count} chars — full output saved to {filename} ...]\n\n"
        f"...{tail_preview}\n\n"
        f"[Full output: {filename} — use read_file or grep to search specific content]"
    )


@tool
async def bash(
    command: str = "",
    is_input: bool = False,
    session: str = "main",
    timeout: int = 120,
    background: bool = False,
    *,
    description: str,
    config: RunnableConfig | None = None,
) -> str:
    """Execute a bash command in a persistent tmux session inside the Docker sandbox.

    See the <BASH_TOOLS> system-prompt block for tool semantics, return-value
    taxonomy, and exit-code hints — this docstring covers parameters only.

    Args:
        command: Shell command. Leave empty to read current screen output of the session.
        is_input: Set True ONLY when an existing command in this session is waiting
            for input (interactive prompt, password, or control sequence like
            'C-c' / 'C-z' / 'C-d'). Never True when starting a new command.
        session: Tmux session name. Different names run in parallel; same name shares cwd.
        timeout: Max seconds to wait for completion (default 120). Commands exceeding
            60s are auto-backgrounded regardless.
        background: Start a long-running command without waiting. Use a dedicated
            session name (not "main"). Check results later with bash_output.
        description: REQUIRED. A short one-line summary of what the command does and
            why. Drives the operator UI's "Run <description>" line. Omitting it fails
            schema validation and wastes the turn.
    """
    _sandbox = get_sandbox(config)
    if _sandbox is None:
        raise RuntimeError("HTTPSandbox not initialized. Call set_sandbox() first.")

    workspace_path = _workspace_path_from_config(config)
    # Best-effort TTL prune of <engagement>/.scratch/ (throttled internally)

    await _prune_old_scratch(workspace_path, _sandbox)

    # Strip leading/trailing newlines before sending to the sandbox.
    # LLM agents frequently wrap commands in block-form like
    # ``"\necho foo\n"`` (the trailing newline is especially common
    # because Claude likes to terminate code blocks with a newline);
    # the sandbox's PS1-marker output capture treats that trailing
    # newline as a fresh prompt cycle and swallows the real stdout,
    # so every block-formatted command returns
    # ``[Command completed with no output. Exit code: 0]`` despite
    # actually running. is_input=True is exempt because control
    # sequences (C-c / C-z / etc.) are literal byte payloads, not
    # commands; stripping them would corrupt the signal.
    if command and not is_input:
        command = command.strip("\n")

    # Background mode: send command and return immediately
    if background and command:
        _reset_passive_read(workspace_path, session)
        await to_thread.run_sync(
            partial(
                _sandbox.start_background,
                command=command,
                session=session,
                **_with_workspace_kwargs(workspace_path),
            )
        )
        return (
            f"[BACKGROUND] Command started in session '{session}'.\n"
            f"Do NOT poll — you will be notified when it completes.\n"
            f"Do productive work NOW (curl/dig/whois on 'main', enumerate other targets, etc).\n"
            f'Inspect early progress with bash_output(session="{session}").'
        )

    # Stale-poll tracking: empty command + is_input=False is a passive read.
    # Any state-changing path (non-empty command, control sequence) resets.
    is_passive_read = not command and not is_input
    if not is_passive_read:
        _reset_passive_read(workspace_path, session)

    result = await _sandbox.execute_tmux_async(
        command=command,
        session=session,
        timeout=timeout,
        is_input=is_input,
        **_with_workspace_kwargs(workspace_path),
    )

    # Sanitize: surrogates → ANSI strip → repetitive line compression
    result = _sanitize_output(result)

    if is_passive_read:
        hint = _track_passive_read(workspace_path, session, result)
        if hint:
            result = result + hint

    # Multi-tier output management:
    # Tier 1 (≤15K): return inline — fits comfortably in context
    # Tier 2 (>15K): offload to file, return preview + file reference
    # Tier 3 (>5M): handled by size watchdog in sandbox_kernel/tmux.py (command killed)
    if len(result) > INLINE_LIMIT and not result.startswith(_STATUS_PREFIXES):
        return await _offload_large_output(result, command, session, workspace_path, _sandbox)

    return result


@tool
async def bash_output(session: str = "main", config: RunnableConfig | None = None) -> str:
    """Retrieve new output from a sandbox session since the last call.

    WHEN TO USE:
    - After bash(..., background=True) to fetch progress or results.
    - After receiving a <system-reminder> notification that a session completed.
    - To re-read a session you've stepped away from while doing other work.

    RETURNS:
    - "[RUNNING elapsed=Ts] session=... command=...\\n<diff>"
    - "[DONE exit=N elapsed=Ts] session=... command=...\\n<diff>"
      (the DONE line is delivered ONCE — after this call the job is "consumed")
    - "[IDLE] No background job in session 'X'."

    Args:
        session: Session name passed to bash(..., background=True).
    """
    _sandbox = get_sandbox(config)
    if _sandbox is None:
        raise RuntimeError("HTTPSandbox not initialized.")

    workspace_path = _workspace_path_from_config(config)

    job = await to_thread.run_sync(
        partial(
            _sandbox.poll_completion,
            session,
            **_with_workspace_kwargs(workspace_path),
        )
    )
    diff_raw = await to_thread.run_sync(
        partial(
            _sandbox.read_session_log_diff,
            session,
            **_with_workspace_kwargs(workspace_path),
        )
    )
    diff = _sanitize_output(diff_raw) if diff_raw else ""

    if job is None:
        _reset_passive_read(workspace_path, session)
        if diff:
            return f"[IDLE] No background job in session '{session}'.\n{diff}"
        return f"[IDLE] No background job in session '{session}'."

    if job.status == "done":
        _sandbox._jobs.mark_consumed(session, key=job.key)
        _reset_passive_read(workspace_path, session)

        hint = _interpret_exit_code(job.exit_code) if job.exit_code is not None else ""
        body = diff if diff else "(no new output)"
        return (
            f"[DONE exit={job.exit_code}{hint} elapsed={job.elapsed:.1f}s] "
            f"session='{session}' command='{job.command}'\n{body}"
        )

    body = diff if diff else "(no new output yet)"
    response = (
        f"[RUNNING elapsed={job.elapsed:.1f}s] session='{session}' command='{job.command}'\n{body}"
    )
    # Track on the stable "no new bytes" signal — NOT the full response, whose
    # elapsed-time string changes every poll and would prevent the counter from
    # ever advancing.
    if not diff:
        stale_hint = _track_passive_read(workspace_path, session, "<<NO_NEW_BYTES>>")
        if stale_hint:
            response = response + stale_hint
    else:
        _reset_passive_read(workspace_path, session)
    return response


@tool
async def bash_kill(session: str, config: RunnableConfig | None = None) -> str:
    """Forcefully terminate a sandbox session.

    Sends Ctrl+C, kills the tmux session, and clears local job tracking.
    The pipe-pane log file is preserved at <engagement>/.sessions/<session>.log.

    Args:
        session: Session name to terminate.
    """
    _sandbox = get_sandbox(config)
    if _sandbox is None:
        raise RuntimeError("HTTPSandbox not initialized.")

    workspace_path = _workspace_path_from_config(config)

    await to_thread.run_sync(
        partial(_sandbox.kill_session, session, **_with_workspace_kwargs(workspace_path))
    )

    _reset_passive_read(workspace_path, session)
    log_path = await to_thread.run_sync(
        partial(
            _sandbox.session_log_path,
            session,
            workspace_path,
        )
    )
    return f"[KILLED] session '{session}' terminated. Log preserved at {log_path}."


@tool
async def bash_status(config: RunnableConfig | None = None) -> str:
    """List all known sandbox sessions with running and completed jobs.

    Use before launching a new background job to spot conflicts, or to
    detect stale sessions for cleanup.
    """
    _sandbox = get_sandbox(config)
    if _sandbox is None:
        raise RuntimeError("HTTPSandbox not initialized.")

    workspace_path = _workspace_path_from_config(config)

    # Poll all known running jobs first, then take ONE snapshot for the table.
    for job in _sandbox._jobs.all_jobs():
        if job.status == "running" and job.workspace_path == workspace_path:
            await to_thread.run_sync(
                partial(
                    _sandbox.poll_completion,
                    job.session,
                    **_with_workspace_kwargs(workspace_path),
                )
            )

    jobs = [job for job in _sandbox._jobs.all_jobs() if job.workspace_path == workspace_path]
    if not jobs:
        return "[EMPTY] No tracked background jobs."

    rows = ["session | status | elapsed | command", "--------+--------+---------+--------"]
    for j in jobs:
        if j.status == "running":
            status = "running"
        else:
            status = f"done(exit={j.exit_code})"
            if j.consumed:
                status += " consumed"
        rows.append(f"{j.session} | {status} | {j.elapsed:.1f}s | {j.command[:60]}")
    return "\n".join(rows)
