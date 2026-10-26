"""SandboxNotificationMiddleware injects <system-reminder> for completed jobs."""

import asyncio
import threading
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from decepticon.middleware.notifications import (
    SandboxNotificationMiddleware,
)
from decepticon.sandbox_kernel import BackgroundJobTracker


def _state(*messages):
    return {"messages": list(messages)}


def _sandbox_with_tracker(diff: str = ""):
    """Fixture: a sandbox MagicMock whose job tracker is real and whose
    bash-output / log-path stubs return the given diff (default empty).

    The middleware pulls the session diff on completion, so any test that
    triggers a completion has to scaffold both ``read_session_log_diff``
    and ``session_log_path`` — the helper centralizes that.
    """
    sandbox = MagicMock()
    sandbox._jobs = BackgroundJobTracker()
    sandbox.poll_completion = MagicMock(side_effect=lambda s: sandbox._jobs.get(s))
    sandbox.read_session_log_diff = MagicMock(return_value=diff)
    sandbox.session_log_path = MagicMock(return_value="/workspace/.sessions/scan.log")
    return sandbox


def test_no_pending_completions_returns_no_update():
    sandbox = _sandbox_with_tracker()
    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    update = mw.before_model(_state(HumanMessage(content="hi")), runtime=None)

    assert update is None or not update.get("messages")


def test_pending_completion_appends_human_system_reminder():
    sandbox = _sandbox_with_tracker(diff="PORT  STATE SERVICE\n80/tcp open  http\n")
    sandbox._jobs.register("scan", command="nmap target", initial_markers=1)
    sandbox._jobs.mark_complete("scan", exit_code=0)
    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    update = mw.before_model(
        _state(HumanMessage(content="hi"), AIMessage(content="ok")), runtime=None
    )

    assert update is not None
    new_messages = update["messages"]
    msg = new_messages[0]
    assert isinstance(msg, HumanMessage)
    # Claude-Code-style bullet header
    assert "<system-reminder>" in msg.content
    assert "● Background command" in msg.content
    assert '"nmap target"' in msg.content
    assert "exit code 0" in msg.content
    assert "session=scan" in msg.content
    # Captured diff is inlined — the agent no longer needs bash_output
    # to retrieve it.
    assert "80/tcp open" in msg.content
    sandbox.read_session_log_diff.assert_called()


def test_already_notified_completions_are_not_re_emitted():
    sandbox = _sandbox_with_tracker()
    sandbox._jobs.register("scan", command="nmap", initial_markers=1)
    sandbox._jobs.mark_complete("scan", exit_code=0)
    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    mw.before_model(_state(HumanMessage(content="hi")), runtime=None)
    update2 = mw.before_model(_state(HumanMessage(content="hi")), runtime=None)

    assert update2 is None or not update2.get("messages")


def test_consumed_jobs_are_not_notified():
    sandbox = _sandbox_with_tracker()
    sandbox._jobs.register("scan", command="nmap", initial_markers=1)
    sandbox._jobs.mark_complete("scan", exit_code=0)
    sandbox._jobs.mark_consumed("scan")
    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    update = mw.before_model(_state(HumanMessage(content="hi")), runtime=None)

    assert update is None or not update.get("messages")


def test_multiple_completions_aggregate_into_one_message():
    sandbox = _sandbox_with_tracker()
    sandbox._jobs.register("a", command="cmd-a", initial_markers=1)
    sandbox._jobs.register("b", command="cmd-b", initial_markers=1)
    sandbox._jobs.mark_complete("a", exit_code=0)
    sandbox._jobs.mark_complete("b", exit_code=2)
    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    update = mw.before_model(_state(HumanMessage(content="hi")), runtime=None)

    assert update is not None
    msgs = update["messages"]
    assert len(msgs) == 1
    content = msgs[0].content
    assert "a" in content and "b" in content
    assert content.count("<system-reminder>") == 1


def test_abefore_model_emits_same_reminder_as_before_model():
    sandbox = _sandbox_with_tracker()
    sandbox._jobs.register("scan", command="nmap target", initial_markers=1)
    sandbox._jobs.mark_complete("scan", exit_code=0)
    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    update = asyncio.run(mw.abefore_model(_state(HumanMessage(content="hi")), runtime=None))

    assert update is not None
    msg = update["messages"][0]
    assert isinstance(msg, HumanMessage)
    assert "<system-reminder>" in msg.content
    assert "scan" in msg.content


def test_concurrent_before_model_calls_each_session_notified_once():
    """Two threads both fire before_model on the same middleware after a job
    completed. Exactly one of them should emit; the other should see _notified
    already includes the session and return None."""
    sandbox = _sandbox_with_tracker()
    sandbox._jobs.register("scan", command="nmap", initial_markers=1)
    sandbox._jobs.mark_complete("scan", exit_code=0)
    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    barrier = threading.Barrier(2)
    results = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()
        r = mw.before_model(_state(HumanMessage(content="hi")), runtime=None)
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    emitted = [r for r in results if r is not None]
    assert len(emitted) == 1, f"Expected exactly one emission, got {len(emitted)}"


# ── Issue #157 regressions: defensive error-handling paths ──────────────
# Each test pins a fix that landed in current main so a future refactor
# cannot silently regress to the original crashy behaviour. Cross-ref
# the audit table in issue #157.


def test_poll_completion_exception_does_not_crash_middleware() -> None:
    """HIGH #1: ``poll_completion`` runs subprocess (docker exec) and can
    raise on container disconnect, invalid session, or timeout. The
    middleware must catch and log so a transient sandbox blip does not
    take down the whole agent step.
    """
    sandbox = _sandbox_with_tracker()
    sandbox._jobs.register("scan", command="nmap target", initial_markers=1)
    # Note: do NOT mark_complete — leave the job running so the
    # poll_completion path is exercised on enumerate.
    sandbox.poll_completion = MagicMock(side_effect=RuntimeError("docker exec disconnected"))

    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    # Must not raise — the middleware is best-effort and a poll error
    # cannot crash the model step.
    update = mw.before_model(_state(HumanMessage(content="hi")), runtime=None)

    # Nothing completed, nothing to notify.
    assert update is None or not update.get("messages")
    sandbox.poll_completion.assert_called()


def test_poll_completion_exception_does_not_crash_async_middleware() -> None:
    """HIGH #1 async sibling — same contract for ``abefore_model``."""
    sandbox = _sandbox_with_tracker()
    sandbox._jobs.register("scan", command="nmap", initial_markers=1)
    sandbox.poll_completion = MagicMock(side_effect=RuntimeError("session lost"))

    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    update = asyncio.run(mw.abefore_model(_state(HumanMessage(content="hi")), runtime=None))
    assert update is None or not update.get("messages")


def test_sandbox_without_jobs_attribute_returns_none() -> None:
    """MED #5: ``self._sandbox._jobs`` is accessed via ``getattr`` so a
    partially-constructed sandbox (e.g. test fixture, early init) does
    not raise ``AttributeError`` from the middleware. Pin that contract.
    """
    sandbox = MagicMock(spec=[])  # spec=[] → no attributes whatsoever
    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    update = mw.before_model(_state(HumanMessage(content="hi")), runtime=None)
    assert update is None


def test_command_none_does_not_crash_message_builder() -> None:
    """MED #6: ``job.command`` can be None on jobs registered without an
    explicit command. Slicing ``None[:80]`` would raise TypeError; the
    fix uses ``(job.command or "")[:80]``. Pin it.
    """
    sandbox = _sandbox_with_tracker()
    # Register with no command — BackgroundJobTracker accepts command=""
    # and we patch the resulting job's ``command`` attribute to None to
    # simulate the legacy/edge case the audit flagged.
    sandbox._jobs.register("scan", command="", initial_markers=1)
    sandbox._jobs.mark_complete("scan", exit_code=0)
    for job in sandbox._jobs.all_jobs():
        job.command = None  # type: ignore[assignment]

    mw = SandboxNotificationMiddleware(sandbox=sandbox)
    update = mw.before_model(_state(HumanMessage(content="hi")), runtime=None)

    assert update is not None
    msg = update["messages"][0]
    # Empty command renders as ``"..."`` with an empty quoted name; no crash.
    assert '""' in msg.content
    assert "exit code 0" in msg.content


def test_notified_set_evicts_oldest_when_capped() -> None:
    """LOW #9: ``_notified`` was unbounded; the fix caps at
    ``_NOTIFIED_KEYS_MAX`` and evicts FIFO. Verify the cap is enforced
    so a long-running agent session cannot leak memory through this set.
    """
    from decepticon.middleware.notifications import _NOTIFIED_KEYS_MAX

    sandbox = _sandbox_with_tracker()
    mw = SandboxNotificationMiddleware(sandbox=sandbox)

    # Insert one beyond the cap; the oldest must have been evicted.
    keys = [f"k{i}" for i in range(_NOTIFIED_KEYS_MAX + 5)]
    mw._record_notified(keys)

    assert len(mw._notified) == _NOTIFIED_KEYS_MAX
    # FIFO eviction: the first 5 keys are the ones evicted.
    for evicted in keys[:5]:
        assert evicted not in mw._notified
    # Keys past the eviction window are retained.
    for kept in keys[5:]:
        assert kept in mw._notified


def test_native_run_notifications_use_each_binding_and_never_the_construction_backend(monkeypatch):
    from types import SimpleNamespace

    from decepticon.backends import factory

    initial = _sandbox_with_tracker("WRONG")
    a, b = _sandbox_with_tracker("A-completed"), _sandbox_with_tracker("B-completed")
    a.id, b.id = "openshell:a:generation", "openshell:b:generation"
    for sandbox in (a, b):
        sandbox._jobs.register("same-session", command="owned-job", initial_markers=0)
        sandbox._jobs.mark_complete("same-session", exit_code=0)
    backends = {"a": a, "b": b}
    monkeypatch.setattr(
        factory,
        "build_sandbox_backend",
        lambda config: backends[config["configurable"]["openshell_binding_id"]],
    )
    middleware = SandboxNotificationMiddleware(initial)

    def runtime(binding):
        return SimpleNamespace(
            config={
                "configurable": {"sandbox_provider": "openshell", "openshell_binding_id": binding}
            }
        )

    first = middleware.before_model(_state(), runtime("a"))
    second = middleware.before_model(_state(), runtime("b"))
    assert "A-completed" in first["messages"][0].content
    assert "B-completed" not in first["messages"][0].content
    assert "B-completed" in second["messages"][0].content
    assert middleware.before_model(_state(), runtime("a")) is None
    initial.read_session_log_diff.assert_not_called()


def test_parallel_native_notification_hooks_keep_same_named_jobs_separate(monkeypatch):
    from types import SimpleNamespace

    from decepticon.backends import factory

    a, b = _sandbox_with_tracker("alpha-output"), _sandbox_with_tracker("beta-output")
    a.id, b.id = "openshell:a:generation", "openshell:b:generation"
    for sandbox in (a, b):
        sandbox._jobs.register("main", command="done", initial_markers=0)
        sandbox._jobs.mark_complete("main", exit_code=0)
    monkeypatch.setattr(
        factory,
        "build_sandbox_backend",
        lambda config: a if config["configurable"]["openshell_binding_id"] == "a" else b,
    )
    middleware = SandboxNotificationMiddleware(_sandbox_with_tracker("WRONG"))

    async def run():
        return await asyncio.gather(
            *(
                middleware.abefore_model(
                    _state(),
                    SimpleNamespace(
                        config={
                            "configurable": {
                                "sandbox_provider": "openshell",
                                "openshell_binding_id": binding,
                            }
                        }
                    ),
                )
                for binding in ("a", "b")
            )
        )

    first, second = asyncio.run(run())
    assert "alpha-output" in first["messages"][0].content
    assert "beta-output" not in first["messages"][0].content
    assert "beta-output" in second["messages"][0].content
