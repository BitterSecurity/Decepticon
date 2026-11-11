"""Stability snapshot — every public ``decepticon-sdk`` name imports.

Spec §6.3 lists the SDK single-import surface that plugin authors
rely on. This test locks in the plugin-author symbols, the testing fakes and the
``decepticon_sdk.host`` integration surface so a removed re-export fails CI.

See ``packages/decepticon-core/tests/test_public_api_stability.py``
for the corresponding core-layer snapshot.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

SDK_PUBLIC_API: tuple[tuple[str, str], ...] = (
    # Protocols (7)
    ("decepticon_sdk", "BackendProtocol"),
    ("decepticon_sdk", "MiddlewareProtocol"),
    ("decepticon_sdk", "ToolProtocol"),
    ("decepticon_sdk", "CallbackProtocol"),
    ("decepticon_sdk", "LLMProtocol"),
    ("decepticon_sdk", "SandboxProtocol"),
    ("decepticon_sdk", "AgentProtocol"),
    # Slot enum + constants (3)
    ("decepticon_sdk", "MiddlewareSlot"),
    ("decepticon_sdk", "SAFETY_CRITICAL_SLOTS"),
    ("decepticon_sdk", "SLOTS_PER_ROLE"),
    # Focused contributions (5)
    ("decepticon_sdk", "ToolContribution"),
    ("decepticon_sdk", "MiddlewareContribution"),
    ("decepticon_sdk", "PromptContribution"),
    ("decepticon_sdk", "SubAgentContribution"),
    ("decepticon_sdk", "SafetyDeclaration"),
    # Plugin loader (3)
    ("decepticon_sdk", "PluginBundle"),
    ("decepticon_sdk", "SubAgentSpec"),
    ("decepticon_sdk", "is_bundle_enabled"),
    # Registry (8)
    ("decepticon_sdk", "PluginRegistry"),
    ("decepticon_sdk", "PluginInfo"),
    ("decepticon_sdk", "PluginConflictWarning"),
    ("decepticon_sdk", "RoleRegistry"),
    ("decepticon_sdk", "RoleSpec"),
    ("decepticon_sdk", "RoleResolution"),
    ("decepticon_sdk", "SafetyRegistry"),
    ("decepticon_sdk", "SkillSourceRegistry"),
    # Testing fakes (3)
    ("decepticon_sdk.testing", "FakeBackend"),
    ("decepticon_sdk.testing", "FakeLLM"),
    ("decepticon_sdk.testing", "FakeSandbox"),
    # Scaffold entry (CLI also wired via [project.scripts])
    ("decepticon_sdk.scaffold", "app"),
    # Host integration surface (decepticon-sdk[host]) — runtime integration
    # for hosts that run agents in-process. Re-exports framework code.
    ("decepticon_sdk.host", "HOST_RUN_CONFIGURABLE_KEYS"),
    ("decepticon_sdk.host", "HostRunConfigurable"),
    ("decepticon_sdk.host", "SKILLS_LOCAL_PATH"),
    ("decepticon_sdk.host", "HTTPTransportSandbox"),
    ("decepticon_sdk.host", "OpenShellSandbox"),
    ("decepticon_sdk.host", "build_sandbox_backend"),
    ("decepticon_sdk.host", "configure_openshell_resolver"),
    ("decepticon_sdk.host", "get_sandbox"),
    ("decepticon_sdk.host", "is_openshell_sandbox"),
    ("decepticon_sdk.host", "make_agent_backend"),
    ("decepticon_sdk.host", "set_sandbox"),
    ("decepticon_sdk.host", "EngagementFilesystemBackend"),
    ("decepticon_sdk.host", "rebind_sandbox_per_run"),
    ("decepticon_sdk.host", "workspace_from_runtime"),
    ("decepticon_sdk.host", "LLMFactory"),
    ("decepticon_sdk.host", "SafetyOverrideViolation"),
    ("decepticon_sdk.host", "build_middleware"),
    ("decepticon_sdk.host", "build_tools"),
    ("decepticon_sdk.host", "create_soundwave_agent"),
    ("decepticon_sdk.host", "iter_override_bundles"),
    ("decepticon_sdk.host", "resolve_prompt_overrides"),
    ("decepticon_sdk.host", "resolve_role_model"),
    ("decepticon_sdk.host", "ModelOverridesState"),
    ("decepticon_sdk.host", "ProxyKeyState"),
    ("decepticon_sdk.host", "SubagentTranscriptState"),
    ("decepticon_sdk.host", "reduce_converging_value"),
)


@pytest.mark.parametrize(("module_name", "attr"), SDK_PUBLIC_API)
def test_sdk_public_name_importable(module_name: str, attr: str) -> None:
    module = importlib.import_module(module_name)
    value: Any = getattr(module, attr, None)
    assert value is not None, (
        f"{module_name}.{attr} returned None — accidental removal? "
        f"If intentional, update this manifest + CHANGELOG together."
    )


def test_sdk_manifest_count_unchanged() -> None:
    """Snapshot the manifest size."""
    expected = 56
    actual = len(SDK_PUBLIC_API)
    assert actual == expected, (
        f"SDK_PUBLIC_API has {actual} entries, expected {expected}. "
        f"If intentional, bump this number AND update the CHANGELOG."
    )
