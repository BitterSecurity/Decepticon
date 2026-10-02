"""TmuxSessionManager + execution helpers — drives tmux from inside the
sandbox container via `exec_prefix=[]` (local subprocess). The HTTP
sandbox daemon (`sandbox_server/`) is the only production caller.

Historical note: the original implementation lived in
`decepticon/backends/docker_sandbox.py` and ran from the agent host via
`exec_prefix=["docker", "exec", <ctn>]`. That `DockerSandbox` transport
has been retired in favor of `HTTPSandbox` + the in-container daemon,
so the docker-exec branch is no longer reachable from any production
caller (`exec_prefix` defaults to `[]`).

The semantics — PS1 marker parsing, stall detection, size watchdog,
output truncation, auto-background after 60 s — are unchanged from the
docker_sandbox.py original. The polling cadence is adaptive: while the
command has produced no output yet it backs off geometrically (see
``_next_poll_interval``); the moment any output appears it returns to
``POLL_INTERVAL`` and stays there, so stall/interactive-prompt detection
keeps its original timing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from collections.abc import Callable

log = logging.getLogger("decepticon.sandbox_kernel.tmux")

# ─── Tunable timing constants (patched in tests) ────────────────────────

# CWD group is a negated class (no ']', no newline) rather than non-greedy
# '.+?': $PWD is always a single line and never contains a literal ']', so
# this shape rejects malformed/half-markers stitched together by capture-pane
# joining without relying on backtracking or on '.' implicitly excluding '\n'.
PS1_PATTERN = re.compile(r"\[DCPTN:(\d+):([^\]\n]+)\]")

# ─── Terminal-noise sanitizer (for the pipe-pane session log) ───────────
#
# ``capture-pane`` (foreground bash) renders to a clean screen, so its
# output is already escape-free. ``pipe-pane`` (the streaming session log
# behind ``bash_output``) instead streams the RAW pane bytes, which carry
# every control sequence the shell emits: OSC shell-integration markers
# (incl. the ``OSC 3008`` ``type=shell;cwd=…`` ones), CSI sequences
# (colors, cursor moves, bracketed-paste ``?2004h/l`` toggles), the
# ``[DCPTN:…]`` PS1 markers, and stray C0 control chars. Returned verbatim
# they reach the agent / LangSmith as unreadable noise — so the log diff
# is run through ``strip_terminal_noise`` before it is handed back.
#
# OSC: ESC ] … terminated by BEL or ST (ESC \).
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# CSI: ESC [ params intermediates final. Covers SGR, cursor, and the
# private ``?2004h/l`` bracketed-paste toggles.
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Other two-char ESC sequences (RIS ``ESC c``, save/restore cursor, etc.).
# The final-byte class deliberately excludes ``[`` (0x5b) so it never eats
# a CSI introducer; OSC is handled above.
_ESC_RE = re.compile(r"\x1b[@-Z\\-_]")
# Remaining C0 control chars EXCEPT tab (0x09) and newline (0x0a); carriage
# returns are normalized away separately.
_C0_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_terminal_noise(text: str) -> str:
    """Strip raw-terminal control noise from a pipe-pane log diff.

    Removes OSC / CSI / two-char ESC sequences, the ``[DCPTN:…]`` PS1
    markers, and stray C0 control chars; normalizes ``\\r\\n`` → ``\\n``.
    Plain text, newlines, and tabs are preserved.
    """
    if not text:
        return text
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _ESC_RE.sub("", text)
    text = PS1_PATTERN.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "")
    return _C0_RE.sub("", text)


POLL_INTERVAL: float = 0.5
# Adaptive backoff: while the command has produced no output yet and the
# screen is unchanged between polls, the interval grows geometrically
# (×POLL_BACKOFF_FACTOR) up to POLL_INTERVAL × POLL_BACKOFF_MAX_MULTIPLIER,
# and snaps back to POLL_INTERVAL as soon as any output appears. Every poll
# is a full `tmux capture-pane -S -` subprocess (scrollback dump), so a
# silent long-running command (sleep, blocking connect, slow scan that
# hasn't printed yet) at the fixed cadence costs 2 captures/s for its whole
# life; backoff cuts that ~4×. Backoff is deliberately NOT applied once
# output exists, so stall/interactive-prompt detection (which only triggers
# after output) keeps the original 0.5s cadence. The cap is a multiplier
# (not an absolute) so tests that patch POLL_INTERVAL down to milliseconds
# keep their fast cadence.
POLL_BACKOFF_FACTOR: float = 1.5
POLL_BACKOFF_MAX_MULTIPLIER: float = 4.0
STALL_SECONDS: float = 3.0
# Fallback after STALL_SECONDS when the tail is NOT a recognizable prompt:
# treat as possibly-hung instead of mislabeling as interactive.
HUNG_PROCESS_SECONDS: float = 30.0
MAX_OUTPUT_CHARS: int = 30_000
AUTO_BACKGROUND_SECONDS: float = 60.0
SIZE_WATCHDOG_CHARS: int = 5_000_000
# Consecutive transient capture failures (docker-exec stall / tmux-server
# unresponsive) tolerated inside the poll loop before we fail fast. Without
# this cap a wedged `docker exec` resets the stall timer every iteration and
# the command spins until the full `timeout`, pinning a core the whole time.
MAX_CONSECUTIVE_CAPTURE_FAILURES: int = 5

# Common shell / REPL / offensive-tool prompt tails. Matched against the
# last non-blank line of a stalled screen. The first alternative covers
# generic shells ("$ ", "# ", "> ", ": ", "? "); the second covers
# named tool prompts (msf6, meterpreter, sliver[, sliver (X)], Pdb,
# Python REPL, mysql, ftp). Kept deliberately conservative — anything
# that does not match is treated as a possibly-hung process, not as an
# interactive prompt.
_PROMPT_TAIL_RE = re.compile(
    r"(?:[\$#>:?]\s*$)"
    r"|(?:^(?:msf\d*|meterpreter|sliver(?:\s*\([^)]+\))?|mysql|ftp)\s*>\s*$)"
    r"|(?:^\(Pdb\)\s*$)"
    r"|(?:^>>>\s*$)"
)


def _next_poll_interval(current: float) -> float:
    """Grow the poll interval geometrically, capped at a POLL_INTERVAL multiple.

    Reads the module-level constants at call time so tests that patch
    ``POLL_INTERVAL`` get a proportionally scaled cap. Incremental
    pane capture was considered as the complementary optimization and
    deliberately rejected: completion detection counts PS1 markers over
    the *whole* scrollback, and a partial capture can split a marker
    across reads — backoff keeps captures whole and merely less frequent.
    """
    return min(current * POLL_BACKOFF_FACTOR, POLL_INTERVAL * POLL_BACKOFF_MAX_MULTIPLIER)


def _looks_like_interactive_prompt(screen: str) -> bool:
    """Return True if the screen's last non-blank line looks like a prompt.

    Used by stall detection to distinguish a real interactive prompt
    (msf6, meterpreter, ``$``, ``Password:`` …) from a silently hung
    program that simply stopped producing output.
    """
    for line in reversed(screen.splitlines()):
        stripped = line.rstrip()
        if not stripped.strip():
            continue
        return _PROMPT_TAIL_RE.search(stripped) is not None
    return False


# ─── Sandbox env passthrough allowlist ──────────────────────────────────
#
# Engagements often configure an intercepting proxy (e.g. via HTTPS_PROXY)
# so the orchestrator can capture and replay traffic generated by sandbox
# commands. The tmux shell that hosts those commands must therefore see
# those env vars; otherwise traffic bypasses the proxy and is lost.
#
# The set is intentionally narrow and explicit. We never forward arbitrary
# parent environment into the sandbox.

_PROXY_ENV_KEYS: frozenset[str] = frozenset(
    {
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "no_proxy",
        "all_proxy",
    }
)
_DECEPTICON_ENV_PREFIX = "DECEPTICON_"

# Defense-in-depth: even though only allowlisted names reach the renderer,
# we still validate the resulting key against the POSIX shell variable
# grammar before emitting an ``export`` line, so a future allowlist edit
# cannot accidentally introduce a shell-injection vector.
_VALID_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _allowed_passthrough_env() -> dict[str, str]:
    """Return only the parent env vars that are safe to forward into the sandbox.

    Strict allowlist:

    * Proxy variables (upper- and lower-case): ``HTTPS_PROXY``, ``HTTP_PROXY``,
      ``NO_PROXY``, ``ALL_PROXY`` and their lowercase counterparts.
    * Any variable whose name starts with ``DECEPTICON_``.

    No other parent environment variables are forwarded. Values are returned
    verbatim and must be shell-quoted before injection (see
    :func:`_shell_export_command`).
    """
    out: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _PROXY_ENV_KEYS or key.startswith(_DECEPTICON_ENV_PREFIX):
            out[key] = value
    return out


def _shell_export_command(env: dict[str, str]) -> str:
    """Render ``env`` as a single ``export`` line safe to send into a shell.

    The variable name is validated against :data:`_VALID_ENV_NAME`; the value
    is shell-quoted with :func:`shlex.quote` so URL credentials, spaces, or
    special characters cannot break out of the assignment. Returns an empty
    string when ``env`` is empty so callers can avoid sending an empty
    command into tmux. Keys are sorted for deterministic output (testing +
    stable session logs).
    """
    parts: list[str] = []
    for key in sorted(env):
        if not _VALID_ENV_NAME.match(key):
            continue
        parts.append(f"{key}={shlex.quote(env[key])}")
    if not parts:
        return ""
    return "export " + " ".join(parts)


def _safe_log(value: object) -> str:
    """Escape control chars so user-controlled strings cannot forge log lines.

    The bash tool and session naming both flow caller-supplied text into
    ``log.<level>(...)`` calls. CodeQL's log-injection rule (medium) flags
    every such site because a ``\\n`` in the value can splice a fake log
    line into structured log readers (Cloud Logging, Loki). The fix is
    cheap and applied uniformly; see callers in this file + base.py.
    """
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


class TmuxCommandError(RuntimeError):
    """Raised when a tmux command fails inside the sandbox container."""

    def __init__(self, args: list[str], returncode: int, output: str) -> None:
        self.args_list = args
        self.returncode = returncode
        self.output = output
        super().__init__(output)


# ─── Semantic exit code interpretation (Claude Code best practice) ────────
_EXIT_CODE_MESSAGES: dict[int, str] = {
    1: "general error",
    2: "misuse of shell builtin",
    126: "permission denied (not executable)",
    127: "command not found — tool may not be installed (try: apt-get install -y <pkg>)",
    128: "invalid exit argument",
    130: "interrupted by Ctrl+C (SIGINT)",
    137: "killed (SIGKILL) — likely OOM or size limit exceeded",
    139: "segmentation fault (SIGSEGV)",
    143: "terminated (SIGTERM)",
}


def _interpret_exit_code(code: int) -> str:
    """Convert exit code to human-readable message for agent context."""
    if code == 0:
        return ""
    if code in _EXIT_CODE_MESSAGES:
        return f" — {_EXIT_CODE_MESSAGES[code]}"
    if code > 128:
        signal_num = code - 128
        return f" — killed by signal {signal_num}"
    return ""


# ─── TmuxSessionManager ───────────────────────────────────────────────────


class TmuxSessionManager:
    """Manages a single named tmux session inside the sandbox container.

    Runs tmux subcommands via ``subprocess.run([*self._exec_prefix, "tmux", …])``.
    In production ``_exec_prefix`` is empty — the daemon process lives in
    the sandbox container, next to tmux — so subprocess invokes tmux
    directly. The arg supports a non-empty prefix for parity with the
    retired DockerSandbox transport, which is no longer used in production.

    Thread-safety: ``_initialized`` is process-wide shared state. The
    ``_init_lock`` (threading.RLock) guards add/discard/clear so concurrent
    sessions cannot race during init or cache invalidation.
    """

    _initialized: set[str] = set()
    _init_lock: threading.RLock = threading.RLock()

    def __init__(
        self,
        session: str,
        container_name: str,
        workspace_path: str = "/workspace",
        log_name: str | None = None,
        exec_prefix: list[str] | None = None,
    ) -> None:
        self.session = session
        self._container = container_name
        self._workspace_path = workspace_path.rstrip("/") or "/workspace"
        self._log_name = log_name or session
        self._pane_id: str | None = None
        # Default to `[]` (local execution): the HTTP sandbox daemon runs
        # *inside* the sandbox container and drives tmux directly. The old
        # `["docker", "exec", container_name]` default served the
        # retired DockerSandbox transport and is no longer reachable from
        # any production caller (HTTPSandbox is the agent-side client).
        self._exec_prefix: list[str] = list(exec_prefix) if exec_prefix is not None else []

    # ── tmux helpers ──

    def _tmux(self, args: list[str], timeout: int = 10) -> str:
        """Run a tmux subcommand against the session's target.

        Production prefixes the command with an empty list — tmux runs as
        a local subprocess inside the sandbox container (the daemon's
        process tree). A non-empty `_exec_prefix` would route through a
        remote transport instead; that branch existed for the retired
        DockerSandbox path and is no longer reachable in production.
        """
        result = subprocess.run(
            [*self._exec_prefix, "tmux", "-L", self.session, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout
            raise TmuxCommandError(args, result.returncode, error_msg)
        return result.stdout

    def _target(self) -> str:
        """Return the stable tmux target for command dispatch."""
        return self.session

    def _forget_cached_state(self) -> None:
        self._pane_id = None
        with TmuxSessionManager._init_lock:
            TmuxSessionManager._initialized.discard(self.session)

    def _resolve_pane_id(self) -> str:
        try:
            return self._tmux(
                ["display-message", "-p", "-t", self.session, "#{pane_id}"],
                timeout=5,
            ).strip()
        except subprocess.TimeoutExpired:
            self._forget_cached_state()
            time.sleep(1.0)
            return self._tmux(
                ["display-message", "-p", "-t", self.session, "#{pane_id}"],
                timeout=10,
            ).strip()

    def _cached_pane_is_alive(self) -> bool:
        if self.session not in TmuxSessionManager._initialized:
            return False
        if self._pane_id is None:
            try:
                self._pane_id = self._resolve_pane_id()
            except (RuntimeError, subprocess.TimeoutExpired):
                self._forget_cached_state()
                return False
        try:
            self._tmux(
                ["display-message", "-p", "-t", self._pane_id, "#{pane_id}"],
                timeout=5,
            )
            return True
        except subprocess.TimeoutExpired:
            self._forget_cached_state()
            time.sleep(1.0)
            try:
                self._tmux(
                    ["display-message", "-p", "-t", self._pane_id, "#{pane_id}"],
                    timeout=10,
                )
                return True
            except (subprocess.TimeoutExpired, RuntimeError):
                raise TmuxCommandError(
                    ["display-message", "-p", "-t", self._pane_id, "#{pane_id}"],
                    -1,
                    "tmux pane probe timed out after retry — sandbox infra fault",
                )
        except RuntimeError:
            return False

    def _send(self, text: str, enter: bool = True) -> None:
        """Send keystrokes using -l (literal) to prevent tmux escaping bugs."""
        target = self._target()
        self._tmux(["send-keys", "-t", target, "-l", text])
        if enter:
            self._tmux(["send-keys", "-t", target, "Enter"])

    def _sync_passthrough_env(self) -> None:
        """Inject the allowlisted parent env vars into the live tmux shell.

        No-op when the allowlist is empty. Send errors are logged; once
        sent, wait for the export's PS1 marker before execution can capture
        its baseline. A missing completion marker fails initialization.
        """
        env = _allowed_passthrough_env()
        cmd = _shell_export_command(env)
        if not cmd:
            return
        initial_count = len(PS1_PATTERN.findall(self._capture()))
        try:
            self._send(cmd, enter=True)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            log.warning(
                "Could not sync passthrough env into tmux session '%s': %s",
                _safe_log(self.session),
                exc,
            )
            return

        deadline = time.monotonic() + 10.0
        while len(PS1_PATTERN.findall(self._capture())) <= initial_count:
            if time.monotonic() >= deadline:
                raise TmuxCommandError(
                    ["capture-pane"], -1, "Timed out waiting for passthrough env export completion"
                )
            time.sleep(POLL_INTERVAL)

    def _clear_screen(self) -> None:
        target = self._target()
        try:
            self._tmux(["send-keys", "-t", target, "C-l"])
            time.sleep(0.1)
            self._tmux(["clear-history", "-t", target])
        except (TmuxCommandError, subprocess.TimeoutExpired, OSError) as e:
            log.warning("_clear_screen failed for '%s': %s", target, e)

    def _capture(self) -> str:
        return self._tmux(
            [
                "capture-pane",
                "-J",
                "-p",
                "-S",
                "-",
                "-E",
                "-",
                "-t",
                self._target(),
            ]
        )

    # ── session lifecycle ──

    def _ensure_session(self) -> bool:
        """Ensure the tmux session exists, creating it if needed.

        Returns ``True`` when the session already existed before this call
        (so the caller can skip first-run-only setup like pipe-pane logging).
        Sets ``self._pane_id`` as a side effect.
        """
        session_exists = False
        try:
            self._tmux(["has-session", "-t", self.session], timeout=5)
            session_exists = True
        except RuntimeError:
            session_exists = False

        if not session_exists:
            log.info("Creating tmux session: %s", self.session)
            try:
                if self._workspace_path != "/workspace":
                    subprocess.run(
                        [*self._exec_prefix, "mkdir", "-p", self._workspace_path],
                        capture_output=True,
                        timeout=5,
                        check=True,
                    )
                pane_id = self._tmux(
                    [
                        "new-session",
                        "-d",
                        "-s",
                        self.session,
                        "-c",
                        self._workspace_path,
                        "-P",
                        "-F",
                        "#{pane_id}",
                    ]
                ).strip()
                self._pane_id = pane_id or self.session
            except RuntimeError:
                try:
                    self._tmux(["has-session", "-t", self.session], timeout=5)
                    self._pane_id = self._resolve_pane_id()
                    session_exists = True
                    log.debug("Session %s already exists (race), reusing", self.session)
                except RuntimeError:
                    raise
            time.sleep(0.3)
        else:
            self._pane_id = self._resolve_pane_id()

        return session_exists

    def _inject_ps1_marker(self) -> None:
        """Inject the PS1 completion marker, disable PS2, and clear the screen."""
        ps1_cmd = "export PROMPT_COMMAND='export PS1=\"[DCPTN:$?:$PWD] \"'; export PS2=''; clear"
        self._send(ps1_cmd)
        time.sleep(0.5)
        self._clear_screen()
        time.sleep(0.2)

    def _setup_pipe_pane_logging(self) -> None:
        """Wire ``pipe-pane`` so the session's output streams to a host-tailable log."""
        log_path = f"{self._workspace_path}/.sessions/{self._log_name}.log"
        try:
            # Idempotent — the directory is bind-mounted to the host so
            # operators can tail the same file the agent reads. Use
            # the configured exec_prefix instead of a hardcoded
            # ``docker exec`` so this works both when the manager
            # wraps a sibling docker container AND when it runs
            # in-process inside the HTTP sandbox daemon (where
            # exec_prefix is empty and no docker socket is reachable).
            subprocess.run(
                [*self._exec_prefix, "mkdir", "-p", f"{self._workspace_path}/.sessions"],
                capture_output=True,
                timeout=5,
                check=True,
            )
            self._tmux(
                [
                    "pipe-pane",
                    "-t",
                    self.session,
                    "-o",
                    f"cat >> {log_path}",
                ]
            )
        except Exception as e:
            log.warning("pipe-pane setup failed for session '%s': %s", self.session, e)

    def initialize(self) -> None:
        """Create session if needed and inject PS1 marker (once per session)."""
        cached_alive = False
        with TmuxSessionManager._init_lock:
            if self._cached_pane_is_alive():
                cached_alive = True
            else:
                TmuxSessionManager._initialized.discard(self.session)
                self._pane_id = None

        if cached_alive:
            # Do NOT re-sync passthrough env here. initialize() runs on EVERY
            # non-input execute(), and _sync_passthrough_env() sends an
            # ``export DECEPTICON_… HTTP_PROXY=…`` line as its own command —
            # which produces its OWN PS1 completion marker. execute() captures
            # ``baseline`` immediately after initialize(), before that marker
            # lands, so it is excluded from ``initial_count``; the poll loop
            # then fires on the EXPORT's marker, not the user command's, and
            # _extract_output() returns the stray ``export DECEPTICON_SKIP_BOOT
            # =1`` + command echo BEFORE the real output appears (observed:
            # ``whois``/network commands returning only their echoed input).
            # A cached-alive session was always synced at creation (below) and
            # the env persists for the life of the persistent shell, so this
            # per-command re-sync is redundant AND a correctness bug.
            return

        session_exists = self._ensure_session()

        self._inject_ps1_marker()

        self._sync_passthrough_env()

        if not session_exists and self._workspace_path != "/workspace":
            self._setup_pipe_pane_logging()

        with TmuxSessionManager._init_lock:
            TmuxSessionManager._initialized.add(self.session)

    # ── execution ──

    def execute(
        self,
        command: str,
        is_input: bool,
        timeout: int,
    ) -> str:
        """Send a command/input and poll for PS1 completion marker.

        Polls until the PS1 marker appears (command complete) or *timeout*
        is reached.  If the tmux session is dead, attempts one automatic
        recovery before returning an error.
        """
        if not is_input:
            self.initialize()

        try:
            baseline = self._capture()
        except RuntimeError as e:
            log.warning("Session '%s' is not ready — attempting recovery: %s", self.session, e)
            self._forget_cached_state()
            try:
                self.initialize()
                baseline = self._capture()
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as retry_err:
                return (
                    f"[ERROR] Session recovery failed: {retry_err}\n"
                    f"The tmux session was destroyed or docker is overloaded. "
                    f"Try using a different session name."
                )
        except (OSError, subprocess.TimeoutExpired) as e:
            return (
                f"[ERROR] Sandbox capture failed: {e}\n"
                f"docker exec timed out or the tmux session is hung. "
                f'Retry, or terminate with bash_kill(session="{self.session}").'
            )

        initial_count = len(PS1_PATTERN.findall(baseline))

        if command:
            try:
                if is_input:
                    if command in ("C-c", "C-z", "C-d"):
                        self._tmux(["send-keys", "-t", self._target(), command])
                    else:
                        self._send(command, enter=True)
                else:
                    self._send(command, enter=True)
            except RuntimeError as e:
                return f"[ERROR] Could not send command to session '{self.session}': {e}"

        start = time.monotonic()
        prev_screen = baseline
        last_change_time = start
        consecutive_capture_failures = 0
        poll_interval = POLL_INTERVAL

        while time.monotonic() - start < timeout:
            time.sleep(poll_interval)
            try:
                screen = self._capture()
            except RuntimeError as poll_err:
                self._forget_cached_state()
                return (
                    f"[ERROR] tmux session '{self.session}' was destroyed mid-command: {poll_err}\n"
                    f"The command likely killed the shell process (e.g. pkill bash).\n"
                    f"Session will auto-recover on next bash() call."
                )
            except (OSError, subprocess.TimeoutExpired) as poll_err:
                consecutive_capture_failures += 1
                if consecutive_capture_failures >= MAX_CONSECUTIVE_CAPTURE_FAILURES:
                    self._forget_cached_state()
                    return (
                        f"[ERROR] Sandbox capture failed {consecutive_capture_failures} times "
                        f"in a row for session '{self.session}': {poll_err}\n"
                        f"docker exec is stalled or the tmux server is unresponsive — "
                        f"giving up rather than spinning until the {timeout}s timeout.\n"
                        f'Retry, or terminate with bash_kill(session="{self.session}").'
                    )
                log.debug("transient capture error in poll loop: %s", poll_err)
                last_change_time = time.monotonic()
                continue

            consecutive_capture_failures = 0
            current_count = len(PS1_PATTERN.findall(screen))

            if current_count > initial_count:
                output, exit_code, cwd = _extract_output(screen, command, initial_count)
                log.info(
                    "Command completed: exit=%s cwd=%s [%s]",
                    exit_code,
                    _safe_log(cwd),
                    _safe_log(command[:50]),
                )
                self._clear_screen()
                result = _truncate(output).strip()
                hint = _interpret_exit_code(exit_code)
                if not result:
                    result = f"[Command completed with no output. Exit code: {exit_code}{hint}]"
                elif exit_code != 0:
                    result += f"\n[Exit code: {exit_code}{hint}]"
                if cwd:
                    result += f"\n[cwd: {cwd}]"
                return result

            # Size watchdog: kill commands producing excessive output
            if len(screen) > SIZE_WATCHDOG_CHARS:
                log.warning(
                    "Size watchdog triggered (%d chars) — killing session [%s]",
                    len(screen),
                    _safe_log(command[:50]),
                )
                try:
                    self._tmux(["send-keys", "-t", self._target(), "C-c"])
                except RuntimeError as interrupt_err:
                    log.debug("Failed to interrupt oversized tmux command: %s", interrupt_err)
                output = _extract_interactive_output(screen, baseline)
                return (
                    f"{_truncate(output).strip()}\n\n"
                    f"[SIZE LIMIT] Output exceeded {SIZE_WATCHDOG_CHARS // 1_000_000}M chars. "
                    f"Command interrupted.\n"
                    f"Redirect output to a file: command > {self._workspace_path}/output.txt"
                )

            # Stall detection: if screen changed from baseline (program produced
            # output) but hasn't changed for STALL_SECONDS, the program is likely
            # waiting for input (interactive prompt like msf6>, sliver>) — but
            # only if the tail actually looks like a prompt. Otherwise keep
            # polling until HUNG_PROCESS_SECONDS, then surface a distinct
            # "may be hung" message instead of falsely claiming interactive.
            #
            # Backoff is applied ONLY while no output has appeared yet
            # (screen == baseline): in that window the command cannot be sitting
            # at an interactive prompt, so a lazier cadence is pure win. Once
            # output exists (screen != baseline) the next quiet stretch might be
            # a prompt, so we hold the base cadence to keep stall detection at
            # its original timing.
            if screen != prev_screen:
                last_change_time = time.monotonic()
                prev_screen = screen
                poll_interval = POLL_INTERVAL
            elif screen == baseline:
                poll_interval = _next_poll_interval(poll_interval)
            else:
                poll_interval = POLL_INTERVAL
                stalled_for = time.monotonic() - last_change_time
                if stalled_for >= STALL_SECONDS and _looks_like_interactive_prompt(screen):
                    log.info(
                        "Stall detected after %.1fs — interactive program [%s]",
                        time.monotonic() - start,
                        _safe_log(command[:50]),
                    )
                    output = _extract_interactive_output(screen, baseline)
                    return (
                        f"{_truncate(output).strip()}\n"
                        f"[session: {self.session} — interactive, "
                        f"send next command with is_input=True]"
                    )
                if stalled_for >= HUNG_PROCESS_SECONDS:
                    log.info(
                        "Prompt-less stall after %.1fs — may be hung [%s]",
                        stalled_for,
                        _safe_log(command[:50]),
                    )
                    output = _extract_interactive_output(screen, baseline)
                    return (
                        f"{_truncate(output).strip()}\n"
                        f"[session: {self.session} — no interactive prompt detected "
                        f"after {int(stalled_for)}s of silence; the program may be hung. "
                        f"Send input with is_input=True as an escape hatch, "
                        f'or terminate with bash_kill(session="{self.session}").]'
                    )

        # Full timeout — include screen capture
        try:
            final_screen = self._capture()
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            final_screen = ""
        screen_tail = final_screen.strip().split("\n")[-20:]
        screen_preview = "\n".join(screen_tail)

        return (
            f"[TIMEOUT] Command exceeded {timeout}s limit.\n"
            f"Session '{self.session}' is still running. "
            f'Send input with bash(command="<input>", is_input=True, session="{self.session}", description="<what you are sending>").\n'
            f'Read partial output with bash_output(session="{self.session}").\n'
            f"--- screen preview ---\n{screen_preview}"
        )

    async def execute_async(
        self,
        command: str,
        is_input: bool,
        timeout: int,
        on_auto_background: Callable[[str, str], None] | None = None,
    ) -> str:
        """Async version of execute() — non-blocking subprocess + cancellable polling.

        All subprocess calls are offloaded via asyncio.to_thread() to avoid blocking
        the ASGI event loop. asyncio.sleep() between polls allows CancelledError
        delivery when LangGraph cancels a run (Ctrl+C → cancelMany).

        Args:
            command: shell command to send (or empty / control sequence with is_input).
            is_input: True when ``command`` is keystrokes for an already-running process.
            timeout: max seconds to wait for command completion.
            on_auto_background: optional callback ``(command, baseline) -> None`` invoked
                exactly once when the auto-background threshold is crossed. ``baseline``
                is the screen capture taken before the command was sent — callers use it
                to derive a stable PS1-marker baseline (e.g. via PS1_PATTERN.findall).
        """
        if not is_input:
            await asyncio.to_thread(self.initialize)

        try:
            baseline = await asyncio.to_thread(self._capture)
        except RuntimeError as e:
            log.warning("Session '%s' is not ready — attempting recovery: %s", self.session, e)
            self._forget_cached_state()
            try:
                await asyncio.to_thread(self.initialize)
                baseline = await asyncio.to_thread(self._capture)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as retry_err:
                return (
                    f"[ERROR] Session recovery failed: {retry_err}\n"
                    f"The tmux session was destroyed or docker is overloaded. "
                    f"Try using a different session name."
                )
        except (OSError, subprocess.TimeoutExpired) as e:
            return (
                f"[ERROR] Sandbox capture failed: {e}\n"
                f"docker exec timed out or the tmux session is hung. "
                f'Retry, or terminate with bash_kill(session="{self.session}").'
            )

        initial_count = len(PS1_PATTERN.findall(baseline))

        if command:
            try:
                if is_input:
                    if command in ("C-c", "C-z", "C-d"):
                        await asyncio.to_thread(
                            self._tmux, ["send-keys", "-t", self._target(), command]
                        )
                    else:
                        await asyncio.to_thread(self._send, command, True)
                else:
                    await asyncio.to_thread(self._send, command, True)
            except RuntimeError as e:
                return f"[ERROR] Could not send command to session '{self.session}': {e}"

        start = time.monotonic()
        prev_screen = baseline
        last_change_time = start
        consecutive_capture_failures = 0
        poll_interval = POLL_INTERVAL

        while time.monotonic() - start < timeout:
            await asyncio.sleep(poll_interval)  # CancelledError delivered here
            try:
                screen = await asyncio.to_thread(self._capture)
            except RuntimeError as poll_err:
                self._forget_cached_state()
                return (
                    f"[ERROR] tmux session '{self.session}' was destroyed mid-command: {poll_err}\n"
                    f"The command likely killed the shell process (e.g. pkill bash).\n"
                    f"Session will auto-recover on next bash() call."
                )
            except (OSError, subprocess.TimeoutExpired) as poll_err:
                consecutive_capture_failures += 1
                if consecutive_capture_failures >= MAX_CONSECUTIVE_CAPTURE_FAILURES:
                    self._forget_cached_state()
                    return (
                        f"[ERROR] Sandbox capture failed {consecutive_capture_failures} times "
                        f"in a row for session '{self.session}': {poll_err}\n"
                        f"docker exec is stalled or the tmux server is unresponsive — "
                        f"giving up rather than spinning until the {timeout}s timeout.\n"
                        f'Retry, or terminate with bash_kill(session="{self.session}").'
                    )
                log.debug("transient capture error in poll loop: %s", poll_err)
                last_change_time = time.monotonic()
                continue

            consecutive_capture_failures = 0
            current_count = len(PS1_PATTERN.findall(screen))

            if current_count > initial_count:
                output, exit_code, cwd = _extract_output(screen, command, initial_count)
                log.info(
                    "Command completed: exit=%s cwd=%s [%s]",
                    exit_code,
                    _safe_log(cwd),
                    _safe_log(command[:50]),
                )
                await asyncio.to_thread(self._clear_screen)
                result = _truncate(output).strip()
                hint = _interpret_exit_code(exit_code)
                if not result:
                    result = f"[Command completed with no output. Exit code: {exit_code}{hint}]"
                elif exit_code != 0:
                    result += f"\n[Exit code: {exit_code}{hint}]"
                if cwd:
                    result += f"\n[cwd: {cwd}]"
                return result

            # Size watchdog: kill commands producing excessive output
            if len(screen) > SIZE_WATCHDOG_CHARS:
                log.warning(
                    "Size watchdog triggered (%d chars) — killing session [%s]",
                    len(screen),
                    _safe_log(command[:50]),
                )
                try:
                    await asyncio.to_thread(self._tmux, ["send-keys", "-t", self._target(), "C-c"])
                except RuntimeError as interrupt_err:
                    log.debug("Failed to interrupt oversized tmux command: %s", interrupt_err)
                output = _extract_interactive_output(screen, baseline)
                return (
                    f"{_truncate(output).strip()}\n\n"
                    f"[SIZE LIMIT] Output exceeded {SIZE_WATCHDOG_CHARS // 1_000_000}M chars. "
                    f"Command interrupted.\n"
                    f"Redirect output to a file: command > {self._workspace_path}/output.txt"
                )

            # Auto-background: convert blocking commands after threshold.
            # Exempt is_input: auto-background exists to detach a blocking
            # *foreground command* the agent is waiting on. is_input is the
            # agent interacting with an already-running process (msfconsole /
            # sliver / pdb); it emits no terminating foreground PS1 marker, so
            # backgrounding it would strand the job as forever-"running" and
            # corrupt the interactive session. Slow interactive turns fall
            # through to the stall/hung path below instead.
            elapsed = time.monotonic() - start
            if elapsed >= AUTO_BACKGROUND_SECONDS and command and not is_input:
                log.info(
                    "Auto-backgrounding after %.0fs [%s] in session '%s'",
                    elapsed,
                    _safe_log(command[:50]),
                    _safe_log(self.session),
                )
                if on_auto_background is not None:
                    try:
                        on_auto_background(command, baseline)
                    except Exception:
                        log.exception("auto-background callback failed")
                output = _extract_interactive_output(screen, baseline)
                preview = _truncate(output).strip()
                return (
                    f"[AUTO-BACKGROUND] Command running >{int(AUTO_BACKGROUND_SECONDS)}s "
                    f"in session '{self.session}'.\n"
                    f"--- partial output ---\n{preview[-1000:] if preview else '(no output yet)'}\n"
                    f"--- end ---\n"
                    f"You will be notified when it completes. Inspect early progress: "
                    f'bash_output(session="{self.session}").'
                )

            # Stall detection + adaptive backoff (see sync execute() for rationale).
            # Backoff only while no output has appeared yet (screen == baseline);
            # once output exists, hold base cadence so stall detection keeps its
            # original timing.
            if screen != prev_screen:
                last_change_time = time.monotonic()
                prev_screen = screen
                poll_interval = POLL_INTERVAL
            elif screen == baseline:
                poll_interval = _next_poll_interval(poll_interval)
            else:
                poll_interval = POLL_INTERVAL
                stalled_for = time.monotonic() - last_change_time
                if stalled_for >= STALL_SECONDS and _looks_like_interactive_prompt(screen):
                    log.info(
                        "Stall detected after %.1fs — interactive program [%s]",
                        time.monotonic() - start,
                        _safe_log(command[:50]),
                    )
                    output = _extract_interactive_output(screen, baseline)
                    return (
                        f"{_truncate(output).strip()}\n"
                        f"[session: {self.session} — interactive, "
                        f"send next command with is_input=True]"
                    )
                if stalled_for >= HUNG_PROCESS_SECONDS:
                    log.info(
                        "Prompt-less stall after %.1fs — may be hung [%s]",
                        stalled_for,
                        _safe_log(command[:50]),
                    )
                    output = _extract_interactive_output(screen, baseline)
                    return (
                        f"{_truncate(output).strip()}\n"
                        f"[session: {self.session} — no interactive prompt detected "
                        f"after {int(stalled_for)}s of silence; the program may be hung. "
                        f"Send input with is_input=True as an escape hatch, "
                        f'or terminate with bash_kill(session="{self.session}").]'
                    )

        # Full timeout — include screen capture
        try:
            final_screen = await asyncio.to_thread(self._capture)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            final_screen = ""
        screen_tail = final_screen.strip().split("\n")[-20:]
        screen_preview = "\n".join(screen_tail)

        return (
            f"[TIMEOUT] Command exceeded {timeout}s limit.\n"
            f"Session '{self.session}' is still running. "
            f'Send input with bash(command="<input>", is_input=True, session="{self.session}", description="<what you are sending>").\n'
            f'Read partial output with bash_output(session="{self.session}").\n'
            f"--- screen preview ---\n{screen_preview}"
        )

    def read_screen(self) -> str:
        """Read current screen without sending any command."""
        try:
            self.initialize()
            screen = self._capture()
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            return (
                f"[ERROR] Could not read screen for session '{self.session}': {e}\n"
                f"The tmux session may be hung or docker is overloaded. "
                f'Retry, or terminate the session with bash_kill(session="{self.session}").'
            )
        matches = list(PS1_PATTERN.finditer(screen))
        if matches:
            last = matches[-1]
            exit_code = int(last.group(1))
            cwd = last.group(2)
            recent = screen[last.end() :].strip()
            if recent:
                return f"[RUNNING] cwd={cwd}\n{_truncate(recent)}"
            return f"[IDLE] exit_code={exit_code} cwd={cwd}\nSession is ready for commands."
        return f"[UNKNOWN]\n{screen[-2000:]}"


# ─── Output helpers (transplanted from tools/bash/tool.py) ───────────────


def _extract_interactive_output(screen: str, baseline: str) -> str:
    """Extract new output from an interactive program (no PS1 marker).

    Compares the current screen against the baseline to find new content
    produced by the interactive program since the command was sent.
    """
    # Find the PS1 marker in the baseline — everything after it is new
    matches = list(PS1_PATTERN.finditer(baseline))
    if matches:
        last = matches[-1]
        new_content = screen[last.end() :].strip()
        return new_content if new_content else screen.strip()
    # No PS1 in baseline either — return the diff
    baseline_lines = set(baseline.strip().split("\n"))
    screen_lines = screen.strip().split("\n")
    new_lines = [ln for ln in screen_lines if ln not in baseline_lines]
    return "\n".join(new_lines) if new_lines else screen.strip()


def _extract_output(screen: str, command: str, since_count: int = 0) -> tuple[str, int, str]:
    """Extract a foreground command's output, exit code, and cwd from the pane.

    ``since_count`` is the number of PS1 markers present in the *baseline*
    capture (before the command ran). Completion is detected upstream as
    "≥1 new marker since baseline" (``current_count > initial_count``), so the
    command's output is *everything between the baseline's last marker and the
    final marker* — regardless of how many markers landed in between.

    Slicing between only the final two markers (the old behaviour) silently
    dropped earlier output whenever a single ``bash()`` call produced 2+ new
    markers (a multi-line / compound command, a subshell that re-cycles the
    prompt, or any injected prompt line): the detector fires on the first new
    marker but the extractor would return only the last segment. Anchoring the
    slice on ``since_count`` keeps detection and extraction in lockstep.

    In the common single-new-marker case (``current_count == initial_count + 1``)
    this is identical to the old ``matches[-2]..last`` slice, so existing
    behaviour and tests are unchanged.
    """
    matches = list(PS1_PATTERN.finditer(screen))
    if not matches:
        return screen, -1, ""
    last = matches[-1]
    exit_code = int(last.group(1))
    cwd = last.group(2)
    # Start at the end of the baseline's last marker; the final marker is the
    # completion prompt. Fall back to start-of-screen when the baseline had no
    # marker (cold session) or when since_count is out of range.
    if 1 <= since_count <= len(matches) - 1:
        raw = screen[matches[since_count - 1].end() : last.start()]
    elif len(matches) >= 2:
        raw = screen[matches[-2].end() : last.start()]
    else:
        raw = screen[: last.start()]
    lines = raw.strip().split("\n")
    # Drop any intermediate PS1 marker lines (extra prompt cycles from a
    # multi-line command) so the sentinels never leak into returned output.
    lines = [ln for ln in lines if not PS1_PATTERN.search(ln)]
    if lines and command and lines[0].strip().endswith(command.strip()):
        lines = lines[1:]
    return "\n".join(lines).strip(), exit_code, cwd


def _truncate(text: str) -> str:
    """Truncate large outputs preserving head + tail for context efficiency.

    Observation masking: large tool outputs are the #1 context consumer.
    Keep the first and last portions (highest signal) and summarize the middle.
    """
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    # Asymmetric split: more head (often contains headers/structure) than tail
    head_chars = int(MAX_OUTPUT_CHARS * 0.6)
    tail_chars = MAX_OUTPUT_CHARS - head_chars
    mid_text = text[head_chars:-tail_chars]
    mid_lines = mid_text.count("\n")
    mid_chars = len(mid_text)
    return (
        f"{text[:head_chars]}\n\n"
        f"[... {mid_lines} lines / {mid_chars} chars truncated — "
        "save full output to file with -oN or redirect to a workspace file "
        f"to preserve complete results ...]\n\n"
        f"{text[-tail_chars:]}"
    )
