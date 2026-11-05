"""WirelessOperator Agent - Wi-Fi / BLE / Zigbee / sub-GHz lane.

Wireless attacks require real hardware (monitor-mode-capable Wi-Fi
adapters, BLE sniffers, RTL-SDR / HackRF). The OSS Docker sandbox can't
run them directly without USB passthrough. Two supported deployment
modes documented in agents/prompts/workflows/wireless_operator.md:

  1. **In-sandbox mode**: passthrough ``/dev/bus/usb`` to the sandbox
     container, run airmon-ng / hostapd-mana / etc. inside. Requires
     ``COMPOSE_PROFILES=wireless`` + ``--privileged`` (only on the
     wireless profile, not default).

  2. **Dropbox mode**: separate physical box (Raspberry Pi + adapter)
     running an SSH-accessible daemon; the agent SSHes in and drives
     the toolset. Matches real-world Red Team workflow. Operator
     pre-configures dropbox credentials in plan/roe.json under
     ``machine_enforcement.dropbox`` (block defined in
     decepticon-core.types.roe).

Tool surface (all via bash; no Python SDKs except where critical):

  - aircrack-ng / airmon-ng / airodump-ng / aireplay-ng
  - hcxdumptool / hcxpcapngtool for handshake/PMKID capture
  - hashcat (mode 22000) for offline cracking
  - hostapd-mana / eaphammer for evil-twin + EAP-MSCHAPv2 capture
  - reaver / bully for WPS Pixie Dust
  - Sniffle / Ubertooth for BLE sniffing (in-sandbox / dropbox)
  - KillerBee for Zigbee
  - URH / Inspectrum for sub-GHz signal analysis

The wireless skill tree under skills/standard/wireless/ mirrors the
operator's own skill set (offensive-wifi-recon, offensive-wpa2-psk,
offensive-wpa3-sae, offensive-wpa-enterprise, offensive-evil-twin,
offensive-deauth-disassoc, offensive-wps, offensive-krack-fragattacks,
offensive-bluetooth-ble, offensive-bluetooth-classic,
offensive-zigbee-thread-matter, offensive-z-wave,
offensive-lorawan-sub-ghz).
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from decepticon.agents._benchmark_mode import benchmark_skill_sources
from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.llm import LLMFactory
from decepticon.middleware.step_budget import StepBudgetMiddleware, effective_recursion_limit
from decepticon.tools.bash import BASH_TOOLS
from decepticon.tools.bash.bash import set_sandbox
from decepticon.tools.references.tools import methodology_lookup
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

# KG tools were removed pending the Neo4j middleware redesign (see
# docs/design/neo4j-research-notes.md). KG surface is currently limited
# to the analyst agent.
_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        methodology_lookup,
        *BASH_TOOLS,
    ]
}


_ROLE = "wireless_operator"
_RECURSION_LIMIT = 200
_SKILL_SOURCES: list[str] = ["/skills/standard/wireless/", "/skills/shared/"]


def create_wireless_operator_agent(
    *,
    backend: Any = None,
    llm: Any = None,
    fallback_models: list | None = None,
    sandbox: Any = None,
    tools: list[Any] | None = None,
    middleware: list[Any] | None = None,
    system_prompt: str | None = None,
    recursion_limit: int | None = None,
):
    """Build the WirelessOperator agent."""
    if llm is None or fallback_models is None:
        factory = LLMFactory()
        if llm is None:
            llm = factory.get_model(_ROLE)
        if fallback_models is None:
            fallback_models = factory.get_fallback_models(_ROLE)

    if sandbox is None:
        sandbox = build_sandbox_backend()
    set_sandbox(sandbox)

    if backend is None:
        backend = make_agent_backend(sandbox)

    if tools is None:
        tools = build_tools(role=_ROLE, standard_tools=_STANDARD_TOOLS)
    if middleware is None:
        middleware = build_middleware(
            role=_ROLE,
            skill_sources=[*_SKILL_SOURCES, *benchmark_skill_sources()],
            backend=backend,
            llm=llm,
            fallback_models=fallback_models,
            sandbox=sandbox,
        )
    if system_prompt is None:
        system_prompt = load_prompt(_ROLE, shared=["bash"])

    return create_agent(
        llm,
        system_prompt=system_prompt,
        tools=tools,
        middleware=[
            *middleware,
            StepBudgetMiddleware(total_steps=recursion_limit or _RECURSION_LIMIT),
        ],
        name=_ROLE,
    ).with_config(
        {
            "recursion_limit": effective_recursion_limit(recursion_limit or _RECURSION_LIMIT),
            "callbacks": load_plugin_callbacks(role=_ROLE, backend=backend),
        }
    )


# Module-level graph for LangGraph Platform (langgraph serve)
if is_bundle_enabled("standard"):
    graph = (
        create_wireless_operator_agent()
    )  # lgtm[py/unused-global-variable]  # consumed by langgraph at runtime


SUBAGENT_SPEC = SubAgentSpec(
    name="wireless_operator",
    description=(
        "Wi-Fi / BLE / Zigbee / sub-GHz wireless specialist. Use when "
        "the engagement scope includes wireless attack surfaces: "
        "WPA2-PSK handshake / PMKID capture + hashcat, WPA3-SAE "
        "downgrade, WPA-Enterprise evil-twin RADIUS, evil-twin / KARMA "
        "/ Mana, deauth / disassoc, WPS Pixie Dust, BLE GATT, Zigbee "
        "Touchlink, sub-GHz replay. Requires hardware passthrough or "
        "an SSH dropbox - see prompts/workflows/wireless_operator.md."
    ),
    factory=create_wireless_operator_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=85,
)
