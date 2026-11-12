"""Generic challenge / success validator (v2).

Layers (all generic, never site-specific):
  1. HTTP status semantics (rate-limit / auth / not-found / transient / blocked)
  2. HARD challenge markers (structural WAF containers — always decisive)
  3. Size fingerprints (known bad BYTE sizes hinted by caller)
  4. Content-Type / JSON awareness (small JSON APIs are NOT challenges)
  5. Caller success_selectors (strongest positive proof for HTML)
  6. SOFT markers + cookie sensor + tiny-body heuristics (only when no
     positive proof is available)

v2 changes vs v1 (per multi-AI review 2026-06-21):
  * `WEAK_OK` is reserved for genuinely clean responses. Ambiguous states
    (`_abck` unresolved, soft-block words without proof) now return the new
    non-terminal `SUSPECT_OK` so the fetch chain keeps searching instead of
    declaring a blocked page a success.
  * Small valid JSON (e.g. an internal API) is no longer mislabelled
    `CHALLENGE` — unblocks the R7 API-first route.
  * SOFT markers (e.g. the word "captcha" buried in a script) no longer
    override a matched success_selector.
  * Size compared in BYTES, not unicode char count.
  * Status codes differentiated (429/401/404/5xx) instead of one BLOCKED.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

try:
    from bs4 import BeautifulSoup
except ImportError:  # bs4 is a soft dep: only used when selectors given
    BeautifulSoup = None  # type: ignore


# HARD markers: structural challenge/block containers. Decisive on their own —
# these strings do not appear in legitimate page content. (WAF products only.)
HARD_CHALLENGE_MARKERS: list[str] = [
    "sec-if-cpt-container",
    "Powered and protected by Akamai",
    "Just a moment...",
    "cf-chl-bypass",
    "Attention Required! | Cloudflare",
    "<title>Bot Challenge</title>",
    "The requested URL was rejected",
    "Request unsuccessful. Incapsula",
    "Please enable JS and disable any ad blocker",
]

# SOFT markers: words that strongly suggest a challenge BUT can legitimately
# appear in real content (scripts, articles about bots, etc). Only decisive
# when the caller has no positive proof (success_selectors) that overrides.
SOFT_CHALLENGE_MARKERS: list[str] = [
    "access denied",
    "checking your browser",
    "datadome",
    "captcha",
]

# Backward-compatible export (some callers import CHALLENGE_MARKERS).
CHALLENGE_MARKERS: list[str] = HARD_CHALLENGE_MARKERS + SOFT_CHALLENGE_MARKERS

# Minimum BODY BYTE size below which we suspect a stub / challenge page.
SMALL_BODY_THRESHOLD = 3000


class Verdict(Enum):
    """Classification of a fetched response."""

    STRONG_OK = "strong_ok"  # positive proof present → terminal success
    WEAK_OK = "weak_ok"  # clean, no negative signal → terminal success
    SUSPECT_OK = "suspect_ok"  # ambiguous (abck unresolved / soft) → NON-terminal
    CHALLENGE = "challenge"  # WAF challenge (negative proof)
    BLOCKED = "blocked"  # generic non-2xx block
    RATE_LIMITED = "rate_limited"  # 429 — back off, do not hammer
    AUTH_REQUIRED = "auth_required"  # 401/407 — terminal, retrying TLS won't help
    NOT_FOUND = "not_found"  # 404/410 — terminal
    UNKNOWN = "unknown"  # exception / dependency missing


# Verdicts that mean "stop the grid — more TLS attempts cannot help".
TERMINAL_NONSUCCESS = frozenset(
    {
        Verdict.AUTH_REQUIRED,
        Verdict.NOT_FOUND,
        Verdict.RATE_LIMITED,
    }
)


@dataclass
class ValidationResult:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    matched_selectors: list[str] = field(default_factory=list)
    body_size: int = 0  # bytes
    status: int = 0

    @property
    def ok(self) -> bool:
        """Terminal success only. SUSPECT_OK is intentionally excluded."""
        return self.verdict in (Verdict.STRONG_OK, Verdict.WEAK_OK)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reasons": self.reasons,
            "matched_selectors": self.matched_selectors,
            "body_size": self.body_size,
            "status": self.status,
        }


def _hard_marker_hits(body_lower: str) -> list[str]:
    return [m for m in HARD_CHALLENGE_MARKERS if m.lower() in body_lower]


def _soft_marker_hits(body_lower: str) -> list[str]:
    return [m for m in SOFT_CHALLENGE_MARKERS if m in body_lower]


def _abck_unresolved(cookies: dict) -> bool:
    abck = cookies.get("_abck", "")
    return bool(abck) and "~-1~" in abck


def _content_type(resp) -> str:
    try:
        headers = {k.lower(): v for k, v in dict(getattr(resp, "headers", {}) or {}).items()}
        return str(headers.get("content-type", "")).lower()
    except Exception:
        return ""


def _looks_like_json(text: str, ctype: str) -> bool:
    if "json" in ctype:
        return True
    s = text.lstrip()[:1]
    return s in ("{", "[")


def _json_ok(text: str) -> Optional[bool]:
    """True if text parses as non-empty JSON, False if parses-but-empty,
    None if not parseable."""
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if obj in (None, {}, [], ""):
        return False
    return True


def _byte_size(resp, text: str) -> int:
    content = getattr(resp, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return len(content)
    return len(text.encode("utf-8", "ignore"))


def _looks_complete_content_page(text: str, lowered: str) -> bool:
    """True when a SMALL body is still a real (short) page, not a challenge stub.

    A genuine page is a COMPLETE HTML document (closes `</html>`/`</body>`) that
    carries meaningful visible text — e.g. example.com at ~600B. A WAF interstitial
    that slipped past the marker checks is typically script-only, empty, or an
    incomplete fragment, so it has little visible text and returns False."""
    if "</html>" not in lowered and "</body>" not in lowered:
        return False
    visible = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    visible = re.sub(r"(?s)<[^>]+>", " ", visible)
    visible = re.sub(r"\s+", " ", visible).strip()
    return len(visible) >= 64


def _selector_hits(body: str, selectors: list[str]) -> Optional[list[str]]:
    """Return matched-selector list, or None if BS4 is unavailable."""
    if BeautifulSoup is None:
        return None
    try:
        soup = BeautifulSoup(body, "html.parser")
    except Exception:
        return []
    hits: list[str] = []
    for sel in selectors:
        try:
            if soup.select(sel):
                hits.append(sel)
        except Exception:
            continue
    return hits


def validate(
    resp,
    *,
    success_selectors: Optional[list[str]] = None,
    known_bad_sizes: Optional[list[int]] = None,
    size_tolerance: int = 20,
) -> ValidationResult:
    """Validate a `curl_cffi` / `requests` response (v2)."""
    try:
        status = int(getattr(resp, "status_code", 0) or 0)
        text = getattr(resp, "text", "") or ""
        size = _byte_size(resp, text)
    except Exception as e:
        return ValidationResult(verdict=Verdict.UNKNOWN, reasons=[f"parse_error:{e}"])

    r = ValidationResult(verdict=Verdict.UNKNOWN, body_size=size, status=status)

    # --- Layer 1: status semantics ----------------------------------------
    if status == 429:
        r.verdict = Verdict.RATE_LIMITED
        r.reasons.append("status=429")
        return r
    if status in (401, 407):
        r.verdict = Verdict.AUTH_REQUIRED
        r.reasons.append(f"status={status}")
        return r
    if status in (404, 410):
        r.verdict = Verdict.NOT_FOUND
        r.reasons.append(f"status={status}")
        return r
    if 500 <= status <= 599:
        r.verdict = Verdict.BLOCKED
        r.reasons.append(f"status={status}")
        return r
    if status == 0:
        r.verdict = Verdict.UNKNOWN
        r.reasons.append("status=0")
        return r
    # 403/406/etc fall through to marker analysis (often a WAF challenge body).

    lowered = text.lower()

    # --- Layer 2: HARD markers (decisive) ---------------------------------
    hard = _hard_marker_hits(lowered)
    if hard:
        r.verdict = Verdict.CHALLENGE
        r.reasons.extend(f"hard:{m}" for m in hard[:3])
        return r

    # --- Layer 3: size fingerprint (bytes, tolerant) ----------------------
    if known_bad_sizes:
        for bad in known_bad_sizes:
            if abs(size - bad) <= size_tolerance:
                r.verdict = Verdict.CHALLENGE
                r.reasons.append(f"size_fp:{size}~{bad}")
                return r

    # --- Layer 4: JSON awareness (before tiny-body heuristic) -------------
    ctype = _content_type(resp)
    if _looks_like_json(text, ctype):
        j = _json_ok(text)
        if j is not None and not 200 <= status <= 299:
            # A JSON error body (proxy refusal, API error) is never success.
            r.verdict = Verdict.BLOCKED
            r.reasons.append(f"status={status}:json_error_body")
            return r
        if j is True:
            # A 2xx with non-empty parseable JSON is a successful API hit even
            # if tiny. CSS selectors don't apply to JSON, so WEAK_OK is the
            # ceiling here (no HTML positive-proof concept).
            r.verdict = Verdict.WEAK_OK
            r.reasons.append("json_ok")
            return r
        if j is False:
            r.verdict = Verdict.SUSPECT_OK
            r.reasons.append("json_empty")
            return r
        # j is None → not actually JSON; fall through to HTML handling.

    cookies = _extract_cookies(resp)
    abck_bad = _abck_unresolved(cookies)

    # --- Layer 5: caller positive proof (HTML) ----------------------------
    if success_selectors:
        hits = _selector_hits(text, success_selectors)
        if hits is None:
            r.verdict = Verdict.UNKNOWN
            r.reasons.append("bs4_missing")
            return r
        if hits:
            r.matched_selectors = hits
            # Selector matched → soft markers are ignored (they were likely in
            # a script or unrelated text). But an unresolved sensor cookie
            # still demotes us to NON-terminal SUSPECT_OK.
            if abck_bad:
                r.reasons.append("abck_unresolved")
                r.verdict = Verdict.SUSPECT_OK
                return r
            r.verdict = Verdict.STRONG_OK
            return r
        # Selectors requested but none matched → challenge.
        r.verdict = Verdict.CHALLENGE
        r.reasons.append("no_success_selector")
        return r

    # --- Layer 6: no positive proof — heuristics --------------------------
    soft = _soft_marker_hits(lowered)
    if soft:
        r.verdict = Verdict.CHALLENGE
        r.reasons.extend(f"soft:{m}" for m in soft[:3])
        return r

    if size < SMALL_BODY_THRESHOLD:
        # A small body is only weak evidence of a challenge stub. A COMPLETE,
        # content-bearing HTML document that just happens to be short (e.g.
        # example.com ~600B) is a real page → clean weak success. Only an
        # incomplete / script-only / empty small body stays suspicious.
        if _looks_complete_content_page(text, lowered):
            r.verdict = Verdict.WEAK_OK
            r.reasons.append(f"small_but_complete:{size}")
            return r
        r.verdict = Verdict.CHALLENGE
        r.reasons.append(f"tiny_body:{size}")
        return r

    if abck_bad:
        # Unresolved Akamai sensor with no positive proof: do NOT declare
        # success. Non-terminal so the chain keeps trying.
        r.reasons.append("abck_unresolved")
        r.verdict = Verdict.SUSPECT_OK
        return r

    # Clean, sizeable, no negative signal, no sensor problem → terminal weak ok.
    r.verdict = Verdict.WEAK_OK
    return r


def _extract_cookies(resp) -> dict:
    try:
        return {c.name: c.value for c in resp.cookies.jar}
    except Exception:
        try:
            return dict(resp.cookies) if hasattr(resp, "cookies") else {}
        except Exception:
            return {}
