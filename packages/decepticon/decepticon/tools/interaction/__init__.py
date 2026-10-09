"""Operator-interaction tools — agent-driven CLI prompts.

Tools in this package pause graph execution and surface a structured prompt
to the human operator. The CLI renders an interactive picker and resumes the
graph with the operator's choice.
"""

from decepticon.tools.interaction.ask_user import ask_user_question
from decepticon.tools.interaction.complete_planning import (
    complete_engagement_planning,
)
from decepticon.tools.interaction.steering import (
    STEERING_TOOLS,
    acknowledge_guidance,
    list_operator_guidance,
    send_operator_guidance,
)

__all__ = [
    "STEERING_TOOLS",
    "acknowledge_guidance",
    "ask_user_question",
    "complete_engagement_planning",
    "list_operator_guidance",
    "send_operator_guidance",
]
