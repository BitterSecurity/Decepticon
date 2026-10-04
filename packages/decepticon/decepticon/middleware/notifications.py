"""Push background-job completion notices into the agent message stream.

When a tmux session's background command finishes, this middleware:

  1. Auto-fetches the diff that accumulated in the sandbox while the
     command was running — the agent no longer needs to call
     ``bash_output`` to pull it.
  2. Injects a HumanMessage tagged ``<system-reminder>`` carrying the
     completion summary AND the captured output, so the agent has
     everything it needs on its very next inference turn.
  3. Emits a ``background_complete`` custom stream event so the CLI can
     render a Claude-Code-style ``● Background command "..." completed
     (exit code N)`` line in the activity transcript, with the output
     attached to that single visual unit instead of being scattered
     across the message stream.

Hook: ``before_model`` — runs every turn, so completions land on the
very next inference even if the user did nothing between turns.

Cursor semantics
----------------
``sandbox.read_session_log_diff`` ADVANCES the per-session byte offset
each time it's called. Because this middleware reads the diff, a later
``bash_output(session=...)`` from the agent on the same session will
return no new bytes — that's intentional. ``bash_output`` is now a
fallback for explicit re-fetch (e.g. after the agent decides to
re-inspect a session it already saw), not the primary delivery path.
"""

import logging
import re
import threading
from collections import OrderedDict
from functools import partial

from anyio import to_thread
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from decepticon.tools.bash.protocol import BashSandboxProtocol

log = logging.getLogger(__name__)

# Cap on how many job keys we track to avoid unbounded memory growth in
# long-lived agent sessions. We hold an ``OrderedDict`` keyed by job.key
# and evict in FIFO order — duplicate notifications for evicted keys are
# acceptable (the alternative is uncapped growth).
_NOTIFIED_KEYS_MAX = 4096

# Completion delivery is once-only (a job is marked consumed + notified after
# we push its output). If the diff read fails transiently AT delivery time we
# defer — leave the job pending + un-notified — and retry on the next
# before_model tick instead of burning the one delivery on an empty stub. After
# this many failed attempts we give up and deliver the "(no output captured)"
# stub so the job lifecycle still closes (the agent can bash_output to recover).
_PULL_RETRY_MAX = 3

# Output budget for the notification body. Anything larger gets sliced
# to a head+tail preview with a pointer to the on-disk session log so
# the agent has the option to read the full content if it cares.
_INLINE_LIMIT = 15_000
_HEAD_CHARS = 2_000
_TAIL_CHARS = 1_000

# Strip terminal control sequences — agents waste tokens on bracketed
# paste markers (``\x1b[?2004l``), OSC application metadata
# (``\x1b]3008;...\x1b\\``), DEC charset selectors, etc. The pipe-pane
# log captures the raw stream so these all land in the diff. Patterns:
#   1. CSI: ESC [ <params with optional ?> <letter>
#   2. OSC: ESC ] ... terminated by BEL (\x07) or ST (ESC \)
#   3. G0/G1 charset: ESC ( <c> or ESC ) <c>
#   4. Application keypad mode: ESC = or ESC >
_ANSI_ESCAPE = re.compile(
    r"\x1b\[[?0-9;]*[a-zA-Z~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[()][AB012]"
    r"|\x1b[=>]"
)


def _sanitize(text: str) -> str:
    """Surrogate-safe + terminal-control-stripped variant of the captured output."""
    text = text.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
    return _ANSI_ESCAPE.sub("", text)


def _format_output(diff: str, log_path: str | None) -> str:
    """Return the diff trimmed to ``_INLINE_LIMIT`` with a hint to the
    session log when content was sliced."""
    if not diff:
        return "(no output captured)"
    if len(diff) <= _INLINE_LIMIT:
        return diff
    head = diff[:_HEAD_CHARS].rstrip()
    tail = diff[-_TAIL_CHARS:].lstrip()
    chars = len(diff)
    where = f" — full log at {log_path}" if log_path else ""
    return f"{head}\n\n[... {chars} chars truncated{where} ...]\n\n{tail}"


def _get_stream_writer():
    """Return ``get_stream_writer()`` or None if no graph context.

    Imported lazily because the LangGraph runtime context isn't available
    during unit-test instantiation of this middleware; callers fall back
    to the system-reminder path when no writer is available.
    """
    try:
        from langgraph.config import get_stream_writer  # noqa: PLC0415

        return get_stream_writer()
    except Exception:  # noqa: BLE001 — writer is optional; degrade gracefully
        return None


class SandboxNotificationMiddleware(AgentMiddleware):
    """Auto-deliver background-job completions to the agent + CLI."""

    def __init__(self, sandbox: BashSandboxProtocol) -> None:
        super().__init__()
        self._sandbox = sandbox
        # OrderedDict-as-set so we can both check membership and evict in
        # insertion order. Values are unused; we keep ``True`` to make the
        # intent explicit at call sites.
        self._notified: OrderedDict[str, bool] = OrderedDict()
        # Per-job failed-diff-read counter, so a transient read failure defers
        # delivery rather than consuming the once-only notification. Cleared on
        # successful delivery; bounded by _PULL_RETRY_MAX (entries are removed
        # once a job is delivered, so this cannot grow without bound).
        self._pull_attempts: dict[str, int] = {}
        self._lock = threading.Lock()

    def _jobs_view(self):
        """Defensive accessor for the sandbox's job registry.

        ``HTTPSandbox`` exposes ``_jobs`` as an internal attribute (mirrored
        from the daemon-side tracker). Going through ``getattr`` lets us
        survive a backend that has not yet attached the registry (e.g. a
        partially constructed sandbox in a test fixture) without crashing
        the middleware.
        """
        return getattr(self._sandbox, "_jobs", None)

    def _record_notified(self, keys) -> None:
        """Insert keys into the bounded notified-set, evicting oldest first."""
        for key in keys:
            self._notified[key] = True
        while len(self._notified) > _NOTIFIED_KEYS_MAX:
            self._notified.popitem(last=False)

    def _pull_diff(self, session: str, workspace_path: str | None) -> tuple[str, str | None, bool]:
        """Read the accumulated diff for ``session``.

        Returns ``(output, log_path, read_ok)``. ``read_ok`` is False ONLY when
        the diff read itself raised — distinct from a genuinely empty diff — so
        the caller can defer the once-only completion notification and retry
        next turn instead of delivering an empty stub. A transient sandbox blip
        still cannot crash the agent's model step.
        """
        kwargs = {"workspace_path": workspace_path} if workspace_path else {}
        read_ok = True
        try:
            diff = self._sandbox.read_session_log_diff(session, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("read_session_log_diff failed for session=%s: %s", session, exc)
            diff = ""
            read_ok = False
        try:
            log_path = self._sandbox.session_log_path(session, **kwargs)
        except Exception:  # noqa: BLE001
            log_path = None
        return _sanitize(diff), log_path, read_ok

    def _emit_stream_event(self, job, output: str) -> None:
        """Push a custom stream event for the CLI to render the ● bullet."""
        writer = _get_stream_writer()
        if writer is None:
            return
        try:
            writer(
                {
                    "type": "background_complete",
                    "agent": "sandbox",
                    "tool": "bash",
                    "session": job.session,
                    "command": job.command or "",
                    "exit_code": job.exit_code,
                    "elapsed": float(job.elapsed) if job.elapsed is not None else 0.0,
                    "content": output,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to emit background_complete stream event: %s", exc)

    def _format_block(self, job, output: str) -> str:
        """One ``●`` entry for the system-reminder body."""
        command = job.command or ""
        return (
            f'● Background command "{command}" completed '
            f"(exit code {job.exit_code}) — session={job.session} "
            f"elapsed={job.elapsed:.1f}s\n"
            f"```\n{output}\n```"
        )

    def _build_message(self) -> dict | None:
        """Inject completions + emit per-job stream events. Returns the
        state update (HumanMessage) or None when nothing changed.
        """
        jobs = self._jobs_view()
        if jobs is None:
            return None
        try:
            pending = list(jobs.pending_completions())
        except Exception as e:  # noqa: BLE001 — best-effort middleware
            log.warning("Failed to read pending completions from sandbox: %s", e)
            return None

        with self._lock:
            new = [j for j in pending if j.key not in self._notified]
            if not new:
                return None
            # NOTE: do NOT record_notified here — only after a job's output has
            # actually been delivered (below), so a transient diff-read failure
            # leaves the job pending + un-notified and is retried next tick.

        blocks: list[str] = []
        delivered: list = []
        for job in new:
            diff, log_path, read_ok = self._pull_diff(job.session, job.workspace_path)
            if not read_ok:
                attempts = self._pull_attempts.get(job.key, 0) + 1
                self._pull_attempts[job.key] = attempts
                if attempts < _PULL_RETRY_MAX:
                    # Defer: leave the completion pending + un-notified so the
                    # next before_model re-attempts the diff read. The once-only
                    # delivery is not spent on an empty stub.
                    log.info(
                        "deferring completion for session=%s (diff read failed %d/%d)",
                        job.session,
                        attempts,
                        _PULL_RETRY_MAX,
                    )
                    continue
                # Gave up after the retry cap — fall through and deliver the
                # "(no output captured)" stub so the job lifecycle still closes.
                log.warning(
                    "delivering stub completion for session=%s after %d failed diff reads",
                    job.session,
                    attempts,
                )
            formatted = _format_output(diff, log_path)
            blocks.append(self._format_block(job, formatted))
            self._emit_stream_event(job, formatted)
            delivered.append(job)
            self._pull_attempts.pop(job.key, None)
            # Mark the daemon-side mirror consumed so a later ``bash_output``
            # call returns ``(no new output)`` rather than re-delivering the
            # same diff, and so ``pending_completions`` skips it on subsequent
            # middleware ticks. ``self._notified`` (recorded below) is a
            # belt-and-suspenders dedupe for when mark_consumed silently fails.
            try:
                jobs.mark_consumed(session=job.session, key=job.key)
            except Exception:  # noqa: BLE001 — best-effort
                log.warning("mark_consumed failed for session=%s", job.session, exc_info=True)

        if not delivered:
            return None
        with self._lock:
            self._record_notified(j.key for j in delivered)

        body = "\n\n".join(blocks)
        reminder = (
            "<system-reminder>\n"
            "Background sandbox sessions completed. Output captured below "
            "(no need to call bash_output unless you want to re-inspect a "
            "session):\n\n"
            f"{body}\n"
            "</system-reminder>"
        )
        return {"messages": [HumanMessage(content=reminder)]}

    def _refresh_running_jobs(self) -> None:
        """Sync poll for still-running jobs; swallow per-job subprocess errors."""
        jobs = self._jobs_view()
        if jobs is None:
            return
        try:
            running = [j for j in jobs.all_jobs() if j.status == "running"]
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to enumerate sandbox jobs: %s", e)
            return
        for job in running:
            try:
                self._sandbox.poll_completion(job.session, workspace_path=job.workspace_path)
            except Exception as e:  # noqa: BLE001
                log.warning("poll_completion failed for session=%s: %s", job.session, e)

    async def _arefresh_running_jobs(self) -> None:
        """Async sibling of ``_refresh_running_jobs`` — same error semantics."""
        jobs = self._jobs_view()
        if jobs is None:
            return
        try:
            running = [j for j in jobs.all_jobs() if j.status == "running"]
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to enumerate sandbox jobs: %s", e)
            return
        for job in running:
            try:
                await to_thread.run_sync(
                    partial(
                        self._sandbox.poll_completion,
                        job.session,
                        workspace_path=job.workspace_path,
                    )
                )
            except Exception as e:  # noqa: BLE001
                log.warning("poll_completion failed for session=%s: %s", job.session, e)

    def before_model(self, state, runtime):  # type: ignore[override]
        self._refresh_running_jobs()
        return self._build_message()

    async def abefore_model(self, state, runtime):  # type: ignore[override]
        await self._arefresh_running_jobs()
        return self._build_message()
