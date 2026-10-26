"""Framework import and isolated boot contracts; SDK-only tests stay independent."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def _fresh_boot(script: str) -> None:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    env.pop("DECEPTICON_SKIP_BOOT", None)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_all_16_agent_factories_importable() -> None:
    """The 16 agent factories listed in spec §6.2 import cleanly."""
    from decepticon.agents import (
        create_ad_operator_agent,
        create_analyst_agent,
        create_cloud_hunter_agent,
        create_contract_auditor_agent,
        create_decepticon_agent,
        create_detector_agent,
        create_exploit_agent,
        create_exploiter_agent,
        create_patcher_agent,
        create_postexploit_agent,
        create_recon_agent,
        create_reverser_agent,
        create_scanner_agent,
        create_soundwave_agent,
        create_verifier_agent,
        create_vulnresearch_agent,
    )
    from decepticon.agents.build import build_middleware, build_tools

    factories = (
        create_ad_operator_agent,
        create_analyst_agent,
        create_cloud_hunter_agent,
        create_contract_auditor_agent,
        create_decepticon_agent,
        create_detector_agent,
        create_exploit_agent,
        create_exploiter_agent,
        create_patcher_agent,
        create_postexploit_agent,
        create_recon_agent,
        create_reverser_agent,
        create_scanner_agent,
        create_soundwave_agent,
        create_verifier_agent,
        create_vulnresearch_agent,
    )
    assert len(factories) == 16
    assert all(callable(f) for f in factories)
    assert callable(build_middleware)
    assert callable(build_tools)


def test_framework_backends_importable() -> None:
    """Spec §6.2 backend public API surface."""
    from decepticon.backends import (
        SKILLS_LOCAL_PATH,
        HTTPSandbox,
        build_sandbox_backend,
        make_agent_backend,
    )

    assert HTTPSandbox is not None
    assert isinstance(SKILLS_LOCAL_PATH, str)
    assert callable(build_sandbox_backend)
    assert callable(make_agent_backend)


def test_framework_middleware_importable() -> None:
    """Spec §6.2 middleware classes — 11 standard slot implementations."""
    from decepticon.middleware import (
        EngagementContextMiddleware,
        FilesystemMiddleware,
        OPPLANMiddleware,
        SkillsMiddleware,
    )

    for cls in (
        EngagementContextMiddleware,
        FilesystemMiddleware,
        OPPLANMiddleware,
        SkillsMiddleware,
    ):
        assert isinstance(cls, type), f"{cls.__name__} is not a class"


def test_framework_llm_importable() -> None:
    """Spec §6.2 LLMFactory + create_llm."""
    from decepticon.llm import LLMFactory, create_llm

    assert isinstance(LLMFactory, type)
    assert callable(create_llm)


def test_compat_register_legacy_imports_idempotent() -> None:
    _fresh_boot("""
        import decepticon
        import warnings
        from decepticon.compat import register_legacy_imports
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            register_legacy_imports()
            register_legacy_imports()
        assert not [w for w in captured if "Phase 1 compat shims" in str(w.message)]
    """)


def test_role_registry_seeded_with_oss_roles() -> None:
    _fresh_boot("""
        import decepticon
        from decepticon_core.contracts.slots import SLOTS_PER_ROLE
        from decepticon_core.registry import RoleRegistry
        for role in SLOTS_PER_ROLE:
            assert RoleRegistry.get(role) is not None, role
    """)


def test_plugin_registry_finds_oss_subagents() -> None:
    """Spec §14 #1 / gap #4 — PluginRegistry walks the live
    entry-point graph and surfaces the OSS subagent plugins."""
    import decepticon  # boot fires

    del decepticon  # silence unused-import lint

    from decepticon_core.registry import PluginRegistry

    # Discard any cached snapshot left over from monkeypatched
    # collision tests in the core test suite; rebuild against the
    # live entry-point graph.
    PluginRegistry.reset()
    reg = PluginRegistry.load()
    plugins = reg.list_plugins()
    # 13 OSS subagents register via ``decepticon.subagents`` per
    # packages/decepticon/pyproject.toml entry-points.
    subagent_plugins = [p for p in plugins if "decepticon.subagents" in p.groups]
    assert len(subagent_plugins) >= 13, (
        f"expected >=13 OSS subagent plugins discovered, found "
        f"{len(subagent_plugins)}: {[p.name for p in subagent_plugins]}"
    )
    # No collisions for the bare OSS install.
    assert reg.detect_collisions() == ()
