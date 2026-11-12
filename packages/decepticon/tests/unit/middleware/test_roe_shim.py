"""The deprecated ``decepticon.middleware.roe`` shim keeps downstream plugins loading."""

from __future__ import annotations

import importlib
import sys
import warnings


def _fresh_import():
    sys.modules.pop("decepticon.middleware.roe", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module("decepticon.middleware.roe")
    return module, caught


def test_import_warns_and_exposes_previous_names():
    roe, caught = _fresh_import()
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    for name in (
        "RoEGuardrailMiddleware",
        "RoEEnforcementMiddleware",
        "GATED_TOOL_NAMES",
        "NETWORK_TARGET_EXTRACTORS",
        "_hosts_from_url_arg",
        "build_default_sink",
    ):
        assert hasattr(roe, name), name


def test_downstream_subclass_pattern_still_constructs():
    """Mirror of a plugin that subclasses the guardrail and registers extractors."""
    roe, _ = _fresh_import()
    for tool in ("browser_navigate", "browser_open"):
        roe.NETWORK_TARGET_EXTRACTORS[tool] = roe._hosts_from_url_arg

    class PluginBrowserRoE(roe.RoEGuardrailMiddleware):
        def __init__(self) -> None:
            super().__init__(
                sink=roe.build_default_sink(None), gated_tools=frozenset({"browser_open"})
            )

    middleware = PluginBrowserRoE()
    assert roe.build_default_sink("/workspace/x") is None
    assert roe._hosts_from_url_arg({"url": "https://a.example/x"}) == ["a.example"]
    assert isinstance(middleware, roe.RoEGuardrailMiddleware)
