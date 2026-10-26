from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from decepticon.agents.plugins.detector import create_detector_agent
    from decepticon.agents.plugins.exploiter import create_exploiter_agent
    from decepticon.agents.plugins.patcher import create_patcher_agent
    from decepticon.agents.plugins.scanner import create_scanner_agent
    from decepticon.agents.plugins.verifier import create_verifier_agent
    from decepticon.agents.plugins.vulnresearch import create_vulnresearch_agent
    from decepticon.agents.standard.ad_operator import create_ad_operator_agent
    from decepticon.agents.standard.analyst import create_analyst_agent
    from decepticon.agents.standard.autohunt import create_autohunt_agent
    from decepticon.agents.standard.cloud_hunter import create_cloud_hunter_agent
    from decepticon.agents.standard.contract_auditor import create_contract_auditor_agent
    from decepticon.agents.standard.decepticon import create_decepticon_agent
    from decepticon.agents.standard.exploit import create_exploit_agent
    from decepticon.agents.standard.forensicator import create_forensicator_agent
    from decepticon.agents.standard.ics_operator import create_ics_operator_agent
    from decepticon.agents.standard.iot_operator import create_iot_operator_agent
    from decepticon.agents.standard.mobile_operator import create_mobile_operator_agent
    from decepticon.agents.standard.osint_operator import create_osint_operator_agent
    from decepticon.agents.standard.phisher import create_phisher_agent
    from decepticon.agents.standard.postexploit import create_postexploit_agent
    from decepticon.agents.standard.recon import create_recon_agent
    from decepticon.agents.standard.reverser import create_reverser_agent
    from decepticon.agents.standard.soundwave import create_soundwave_agent
    from decepticon.agents.standard.supply_chain_operator import create_supply_chain_operator_agent
    from decepticon.agents.standard.wireless_operator import create_wireless_operator_agent

_FACTORY_MODULES: Final[dict[str, str]] = {
    "create_recon_agent": "decepticon.agents.standard.recon",
    "create_autohunt_agent": "decepticon.agents.standard.autohunt",
    "create_soundwave_agent": "decepticon.agents.standard.soundwave",
    "create_analyst_agent": "decepticon.agents.standard.analyst",
    "create_exploit_agent": "decepticon.agents.standard.exploit",
    "create_postexploit_agent": "decepticon.agents.standard.postexploit",
    "create_decepticon_agent": "decepticon.agents.standard.decepticon",
    "create_reverser_agent": "decepticon.agents.standard.reverser",
    "create_contract_auditor_agent": "decepticon.agents.standard.contract_auditor",
    "create_cloud_hunter_agent": "decepticon.agents.standard.cloud_hunter",
    "create_ad_operator_agent": "decepticon.agents.standard.ad_operator",
    "create_phisher_agent": "decepticon.agents.standard.phisher",
    "create_mobile_operator_agent": "decepticon.agents.standard.mobile_operator",
    "create_wireless_operator_agent": "decepticon.agents.standard.wireless_operator",
    "create_osint_operator_agent": "decepticon.agents.standard.osint_operator",
    "create_iot_operator_agent": "decepticon.agents.standard.iot_operator",
    "create_ics_operator_agent": "decepticon.agents.standard.ics_operator",
    "create_forensicator_agent": "decepticon.agents.standard.forensicator",
    "create_supply_chain_operator_agent": "decepticon.agents.standard.supply_chain_operator",
    "create_scanner_agent": "decepticon.agents.plugins.scanner",
    "create_detector_agent": "decepticon.agents.plugins.detector",
    "create_verifier_agent": "decepticon.agents.plugins.verifier",
    "create_patcher_agent": "decepticon.agents.plugins.patcher",
    "create_exploiter_agent": "decepticon.agents.plugins.exploiter",
    "create_vulnresearch_agent": "decepticon.agents.plugins.vulnresearch",
}

__all__ = [
    "create_recon_agent",
    "create_autohunt_agent",
    "create_soundwave_agent",
    "create_analyst_agent",
    "create_exploit_agent",
    "create_postexploit_agent",
    "create_decepticon_agent",
    "create_reverser_agent",
    "create_contract_auditor_agent",
    "create_cloud_hunter_agent",
    "create_ad_operator_agent",
    "create_phisher_agent",
    "create_mobile_operator_agent",
    "create_wireless_operator_agent",
    # WAVE-3 specialists — lit up over the pre-existing skill trees.
    "create_osint_operator_agent",
    "create_iot_operator_agent",
    "create_ics_operator_agent",
    "create_forensicator_agent",
    "create_supply_chain_operator_agent",
    # Vulnresearch pipeline (five-stage modular)
    "create_scanner_agent",
    "create_detector_agent",
    "create_verifier_agent",
    "create_patcher_agent",
    "create_exploiter_agent",
    "create_vulnresearch_agent",
]


def __getattr__(name: str) -> Callable[..., object]:
    module_name = _FACTORY_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    factory = getattr(import_module(module_name), name)
    if not callable(factory):
        raise TypeError(f"{module_name}.{name} is not callable")
    globals()[name] = factory
    return factory


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
