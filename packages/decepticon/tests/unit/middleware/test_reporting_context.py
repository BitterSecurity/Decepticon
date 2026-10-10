"""Report tool dispatch binds and restores the current graph partition."""
from types import SimpleNamespace
import pytest
from decepticon.middleware.engagement import EngagementContextMiddleware, ReportingScopeError
from decepticon_core.utils.engagement_scope import get_active_engagement, reset_active_engagement, set_active_engagement

def request():
    return SimpleNamespace(tool=SimpleNamespace(name="report_executive"),
        state={"engagement_name": "audit-case", "kg_engagement": "tenant-a.audit-case", "workspace_path": "/workspace/audit-case"},
        runtime=SimpleNamespace(config={"configurable": {"kg_engagement": "tenant-a.audit-case"}}))

def test_report_uses_composite_partition_and_restores_previous_context():
    token = set_active_engagement("previous-case")
    try:
        answer = EngagementContextMiddleware().wrap_tool_call(request(), lambda _: get_active_engagement())
        assert answer == "tenant-a.audit-case"
        assert get_active_engagement() == "previous-case"
    finally:
        reset_active_engagement(token)

def test_report_rejects_conflicting_run_partition():
    call = request()
    call.runtime.config["configurable"]["kg_engagement"] = "other-case"
    with pytest.raises(ReportingScopeError):
        EngagementContextMiddleware().wrap_tool_call(call, lambda _: "unreachable")

def test_report_restores_context_when_handler_raises():
    def failing_handler(_):
        raise RuntimeError("report failed")
    token = set_active_engagement("previous-case")
    try:
        with pytest.raises(RuntimeError, match="report failed"):
            EngagementContextMiddleware().wrap_tool_call(request(), failing_handler)
        assert get_active_engagement() == "previous-case"
    finally:
        reset_active_engagement(token)

@pytest.mark.asyncio
async def test_async_report_uses_composite_partition_and_restores_context():
    async def handler(_):
        return get_active_engagement()
    token = set_active_engagement("previous-case")
    try:
        assert await EngagementContextMiddleware().awrap_tool_call(request(), handler) == "tenant-a.audit-case"
        assert get_active_engagement() == "previous-case"
    finally:
        reset_active_engagement(token)
