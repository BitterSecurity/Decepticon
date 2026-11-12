"""TimeWindowMiddleware on a real langchain agent graph."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from decepticon.middleware.time_window import STOP_KEY, TimeWindowMiddleware

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def _roe(tmp_path: Path, **machine) -> str:
    (tmp_path / "plan").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plan" / "roe.json").write_text(
        json.dumps({"machine_enforcement": {"in_scope": ["example.com"], **machine}})
    )
    return str(tmp_path)


def _window(start: datetime, end: datetime) -> list[str]:
    return [start.isoformat(), end.isoformat()]


class _CountingModel(GenericFakeChatModel):
    calls: int = 0

    def _generate(self, *args, **kwargs):
        self.calls += 1
        return super()._generate(*args, **kwargs)

    def bind_tools(self, tools, **kwargs):
        return self


@tool
def step(note: str) -> str:
    """Record one step."""
    return f"done {note}"


def _agent(now, replies):
    model = _CountingModel(messages=iter(replies))
    mw = TimeWindowMiddleware(now=lambda: now)
    return create_agent(model=model, tools=[step], middleware=[mw]), model


def _stop_or_none(result) -> dict | None:
    last = result["messages"][-1]
    return last.additional_kwargs.get(STOP_KEY) if isinstance(last, AIMessage) else None


def _stop(result) -> dict:
    stop = _stop_or_none(result)
    assert stop is not None, "run did not end on a time-window stop"
    return stop


def test_outside_authorized_window_never_starts(tmp_path):
    ws = _roe(
        tmp_path, authorized_windows=[_window(NOW + timedelta(hours=1), NOW + timedelta(hours=2))]
    )
    agent, model = _agent(NOW, [AIMessage(content="should not run")])
    result = agent.invoke({"messages": [HumanMessage("go")], "workspace_path": ws})
    assert model.calls == 0
    assert _stop(result)["phase"] == "start"
    assert _stop(result)["code"] == "OUTSIDE_TESTING_WINDOW"


def test_blackout_window_never_starts(tmp_path):
    ws = _roe(
        tmp_path, blackout_windows=[_window(NOW - timedelta(minutes=5), NOW + timedelta(minutes=5))]
    )
    agent, model = _agent(NOW, [AIMessage(content="should not run")])
    result = agent.invoke({"messages": [HumanMessage("go")], "workspace_path": ws})
    assert model.calls == 0
    assert _stop(result)["code"] == "BLACKOUT_WINDOW"


def test_inside_window_runs_normally(tmp_path):
    ws = _roe(
        tmp_path, authorized_windows=[_window(NOW - timedelta(hours=1), NOW + timedelta(hours=1))]
    )
    agent, model = _agent(NOW, [AIMessage(content="finished")])
    result = agent.invoke({"messages": [HumanMessage("go")], "workspace_path": ws})
    assert model.calls == 1
    assert _stop_or_none(result) is None
    assert result["messages"][-1].content == "finished"


def test_no_windows_configured_is_unrestricted(tmp_path):
    ws = _roe(tmp_path)
    agent, model = _agent(NOW, [AIMessage(content="finished")])
    agent.invoke({"messages": [HumanMessage("go")], "workspace_path": ws})
    assert model.calls == 1


def test_window_closing_mid_run_stops_before_next_model_call(tmp_path):
    end = NOW + timedelta(minutes=10)
    ws = _roe(tmp_path, authorized_windows=[_window(NOW - timedelta(hours=1), end)])
    clock = {"now": NOW}

    @tool
    def long_step(note: str) -> str:
        """A step during which the window closes."""
        clock["now"] = end + timedelta(minutes=1)
        return f"done {note}"

    model = _CountingModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "long_step", "args": {"note": "1"}, "id": "c1"}],
                ),
                AIMessage(content="must not be produced"),
            ]
        )
    )
    agent = create_agent(
        model=model,
        tools=[long_step],
        middleware=[TimeWindowMiddleware(now=lambda: clock["now"])],
    )
    result = agent.invoke({"messages": [HumanMessage("go")], "workspace_path": ws})
    assert model.calls == 1  # the in-flight tool finished; the next model turn never ran
    assert any(getattr(m, "content", "") == "done 1" for m in result["messages"])
    assert _stop(result)["phase"] == "running"
    assert _stop(result)["code"] == "OUTSIDE_TESTING_WINDOW"


# --- pause / resume with a real checkpointer -----------------------------------

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from decepticon.middleware.time_window import PAUSE_KIND, next_allowed_at  # noqa: E402
from decepticon_core.types.roe import MachineEnforcement  # noqa: E402


def _pausing_agent(replies, clock, tools=(step,)):
    model = _CountingModel(messages=iter(replies))
    agent = create_agent(
        model=model,
        tools=list(tools),
        middleware=[TimeWindowMiddleware(now=lambda: clock["now"])],
        checkpointer=InMemorySaver(),
    )
    return agent, model


def _interrupt(result) -> dict:
    pending = result.get("__interrupt__") or []
    assert pending, "run did not pause"
    value = pending[0].value
    assert value["kind"] == PAUSE_KIND
    return value


def test_paused_before_start_resumes_when_the_window_opens(tmp_path):
    opens = NOW + timedelta(hours=1)
    ws = _roe(tmp_path, authorized_windows=[_window(opens, opens + timedelta(hours=2))])
    clock = {"now": NOW}
    agent, model = _pausing_agent([AIMessage(content="finished")], clock)
    cfg = {"configurable": {"thread_id": "t-start"}}

    paused = agent.invoke({"messages": [HumanMessage("go")], "workspace_path": ws}, cfg)
    payload = _interrupt(paused)
    assert payload["phase"] == "start" and model.calls == 0
    assert payload["resume_at"] == opens.isoformat()

    still_closed = agent.invoke(Command(resume={}), cfg)  # early resume pauses again
    assert _interrupt(still_closed)["resume_at"] == opens.isoformat() and model.calls == 0

    clock["now"] = opens
    done = agent.invoke(Command(resume={}), cfg)
    assert not done.get("__interrupt__")
    assert model.calls == 1 and done["messages"][-1].content == "finished"


def test_paused_mid_run_resumes_at_the_same_point(tmp_path):
    end = NOW + timedelta(minutes=10)
    reopen = NOW + timedelta(hours=1)
    ws = _roe(
        tmp_path,
        authorized_windows=[
            _window(NOW - timedelta(hours=1), end),
            _window(reopen, reopen + timedelta(hours=1)),
        ],
    )
    clock = {"now": NOW}

    @tool
    def long_step(note: str) -> str:
        """A step during which the window closes."""
        clock["now"] = end + timedelta(minutes=1)
        return f"done {note}"

    replies = [
        AIMessage(
            content="", tool_calls=[{"name": "long_step", "args": {"note": "1"}, "id": "c1"}]
        ),
        AIMessage(content="wrapped up"),
    ]
    agent, model = _pausing_agent(replies, clock, tools=(long_step,))
    cfg = {"configurable": {"thread_id": "t-mid"}}

    paused = agent.invoke({"messages": [HumanMessage("go")], "workspace_path": ws}, cfg)
    payload = _interrupt(paused)
    assert payload["phase"] == "running" and payload["resume_at"] == reopen.isoformat()
    assert model.calls == 1

    clock["now"] = reopen
    done = agent.invoke(Command(resume={}), cfg)
    assert model.calls == 2  # continued with the second model turn, no repeat
    contents = [getattr(m, "content", "") for m in done["messages"]]
    assert contents.count("done 1") == 1 and contents[-1] == "wrapped up"


def test_without_future_window_the_run_ends(tmp_path):
    ws = _roe(
        tmp_path, authorized_windows=[_window(NOW - timedelta(hours=2), NOW - timedelta(hours=1))]
    )
    clock = {"now": NOW}
    agent, model = _pausing_agent([AIMessage(content="never")], clock)
    result = agent.invoke(
        {"messages": [HumanMessage("go")], "workspace_path": ws},
        {"configurable": {"thread_id": "t-none"}},
    )
    assert not result.get("__interrupt__") and model.calls == 0
    assert _stop(result)["resume_at"] is None


def test_next_allowed_at() -> None:
    w1 = (NOW + timedelta(hours=1), NOW + timedelta(hours=2))
    blackout = (NOW + timedelta(hours=1), NOW + timedelta(hours=1, minutes=30))
    rules = MachineEnforcement.from_dict(
        {
            "in_scope": ["example.com"],
            "authorized_windows": [[w1[0].isoformat(), w1[1].isoformat()]],
            "blackout_windows": [[blackout[0].isoformat(), blackout[1].isoformat()]],
        }
    )
    assert next_allowed_at(NOW, rules) == blackout[1]
    assert next_allowed_at(NOW + timedelta(hours=3), rules) is None
    open_rules = MachineEnforcement.from_dict({"in_scope": ["example.com"]})
    assert next_allowed_at(NOW, open_rules) == NOW
