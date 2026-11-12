"""insane-search engine — generic WAF-profile-based fetch chain.

No site-specific logic lives here. Site specifics belong to runtime hints or
observations, never to code. See `../SKILL.md` for the No-Site-Name Rule.
"""

from .fetch_chain import Attempt, FetchResult, fetch
from .url_transforms import TRANSFORMS, apply_transform
from .validators import CHALLENGE_MARKERS, ValidationResult, Verdict, validate
from .waf_detector import detect

__all__ = [
    "Verdict",
    "ValidationResult",
    "validate",
    "CHALLENGE_MARKERS",
    "detect",
    "TRANSFORMS",
    "apply_transform",
    "fetch",
    "FetchResult",
    "Attempt",
]
