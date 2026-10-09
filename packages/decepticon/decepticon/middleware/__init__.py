"""Decepticon middleware - custom AgentMiddleware implementations."""

from decepticon.middleware.budget import BudgetEnforcementMiddleware
from decepticon.middleware.budget_pause import BudgetPauseManager, BudgetPolicy
from decepticon.middleware.context_engineering import (
    ModelCapabilities,
    build_cache_optimized_prompt,
    coerce_json_string,
    coerce_nullish,
    handle_hallucinated_tool,
)
from decepticon.middleware.engagement import EngagementContextMiddleware
from decepticon.middleware.event_logging import EventLogMiddleware
from decepticon.middleware.filesystem import FilesystemMiddleware
from decepticon.middleware.hitl import (
    DEFAULT_HIGH_IMPACT_POLICY,
    ApprovalDecision,
    ApprovalPolicyRule,
    ApprovalRequest,
    ApprovalTransport,
    FileBackedApprovalTransport,
    HITLApprovalMiddleware,
    InProcessApprovalTransport,
)
from decepticon.middleware.kg import KGMiddleware
from decepticon.middleware.notifications import (
    SandboxNotificationMiddleware,
)
from decepticon.middleware.operator_steering import OperatorSteeringMiddleware
from decepticon.middleware.opplan import OPPLANMiddleware
from decepticon.middleware.output_bounds import BoundedOutputMiddleware
from decepticon.middleware.parallel_dispatch import ParallelDispatchMiddleware
from decepticon.middleware.prompt_injection_shield import (
    PromptInjectionShieldMiddleware,
)
from decepticon.middleware.roe import (
    RoEEnforcementMiddleware,  # compat alias, removed at 2.0.0
    RoEGuardrailMiddleware,
)
from decepticon.middleware.scan_resume import ScanResumeManager
from decepticon.middleware.skillogy import SkillogyMiddleware, maybe_install_skillogy
from decepticon.middleware.skills import SkillsMiddleware
from decepticon.middleware.untrusted_output import UntrustedOutputMiddleware

__all__ = [
    "ApprovalDecision",
    "ApprovalPolicyRule",
    "ApprovalRequest",
    "ApprovalTransport",
    "BudgetEnforcementMiddleware",
    "DEFAULT_HIGH_IMPACT_POLICY",
    "EngagementContextMiddleware",
    "EventLogMiddleware",
    "FileBackedApprovalTransport",
    "FilesystemMiddleware",
    "HITLApprovalMiddleware",
    "InProcessApprovalTransport",
    "KGMiddleware",
    "OPPLANMiddleware",
    "PromptInjectionShieldMiddleware",
    "RoEEnforcementMiddleware",
    "RoEGuardrailMiddleware",
    "OperatorSteeringMiddleware",
    "ParallelDispatchMiddleware",
    "SandboxNotificationMiddleware",
    "SkillogyMiddleware",
    "SkillsMiddleware",
    "UntrustedOutputMiddleware",
    "maybe_install_skillogy",
    "BoundedOutputMiddleware",
    "ScanResumeManager",
    "BudgetPauseManager",
    "BudgetPolicy",
    "ModelCapabilities",
    "coerce_nullish",
    "coerce_json_string",
    "handle_hallucinated_tool",
    "build_cache_optimized_prompt",
]
