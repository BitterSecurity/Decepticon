"""Deterministic CVSS 3.1 scoring — the **Severity** utility category.

A finding's severity is the single number a triager and a bounty table
key off, yet for most of bugclaw's life it was *guessed* by the drafting
LLM and bucketed by a crude letter-counting heuristic
(``adapters.hackerone._approx_cvss_score``). That is exactly the kind of
slop that gets a real bug mis-rated and closed: a Scope:Changed claimed
where it was never crossed, a blind/side-channel issue rated Medium when
the math says Low.

This module replaces the guess with the **official CVSS 3.1 formula**
(FIRST.org specification, §7) — base score plus the full environmental
score so the program's security requirements (``CR``/``IR``/``AR`` — the
asset weighting HackerOne applies) and any modified metrics are honoured.
Pure arithmetic, no I/O, deterministic.

The ``cvss_score`` ``@tool`` hands this to the validator and report
drafter: *compute, don't claim*. Give it a vector string, get back the
normalized vector, base + environmental score, the severity band each
falls in, and the per-metric breakdown — so the number written into the
report is the number the vector actually produces.
"""

from __future__ import annotations

import math
from typing import Any

from langchain_core.tools import tool

# ── Metric weights (CVSS 3.1 spec, table in §7.4) ──────────────────────
_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_AC = {"L": 0.77, "H": 0.44}
# Privileges Required is scope-dependent: the "changed" column gives the
# attacker more credit because the privilege was on a different component.
_PR_UNCHANGED = {"N": 0.85, "L": 0.62, "H": 0.27}
_PR_CHANGED = {"N": 0.85, "L": 0.68, "H": 0.5}
_UI = {"N": 0.85, "R": 0.62}
_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}

# Temporal (default "X" → 1.0, i.e. not-defined leaves the score at base)
_E = {"X": 1.0, "H": 1.0, "F": 0.97, "P": 0.94, "U": 0.91}
_RL = {"X": 1.0, "U": 1.0, "W": 0.97, "T": 0.96, "O": 0.95}
_RC = {"X": 1.0, "C": 1.0, "R": 0.96, "U": 0.92}

# Environmental security requirements
_REQ = {"X": 1.0, "H": 1.5, "M": 1.0, "L": 0.5}

# Allowed values per metric key, for validation + the modified ("M*")
# environmental overrides ("X" = use the base value).
_BASE_METRICS = {
    "AV": set(_AV),
    "AC": set(_AC),
    "PR": {"N", "L", "H"},
    "UI": set(_UI),
    "S": {"U", "C"},
    "C": set(_CIA),
    "I": set(_CIA),
    "A": set(_CIA),
}
_TEMPORAL_METRICS = {"E": set(_E), "RL": set(_RL), "RC": set(_RC)}
_ENV_METRICS = {
    "CR": set(_REQ),
    "IR": set(_REQ),
    "AR": set(_REQ),
    "MAV": set(_AV) | {"X"},
    "MAC": set(_AC) | {"X"},
    "MPR": {"N", "L", "H", "X"},
    "MUI": set(_UI) | {"X"},
    "MS": {"U", "C", "X"},
    "MC": set(_CIA) | {"X"},
    "MI": set(_CIA) | {"X"},
    "MA": set(_CIA) | {"X"},
}

_SEVERITY_BANDS = (
    (9.0, "Critical"),
    (7.0, "High"),
    (4.0, "Medium"),
    (0.1, "Low"),
    (0.0, "None"),
)


class CVSSError(ValueError):
    """Raised when a vector string is malformed or has an illegal value."""


def severity_band(score: float) -> str:
    """Map a 0.0–10.0 score to the CVSS qualitative band."""
    for floor, label in _SEVERITY_BANDS:
        if score >= floor:
            return label
    return "None"


def _roundup(value: float) -> float:
    """CVSS 3.1 Roundup — ceil to one decimal, integer-safe (spec §7.4)."""
    int_input = round(value * 100_000)
    if int_input % 10_000 == 0:
        return int_input / 100_000.0
    return (math.floor(int_input / 10_000) + 1) / 10.0


def parse_vector(vector: str) -> dict[str, str]:
    """Parse + validate a CVSS 3.0/3.1 vector into a metric dict.

    Raises ``CVSSError`` on an unsupported prefix, a malformed or duplicate
    metric, an unknown key, an illegal value, or a missing mandatory base metric.
    """
    if not isinstance(vector, str) or not vector.strip():
        raise CVSSError("empty CVSS vector")
    parts = vector.strip().split("/")
    if parts[0].upper() not in {"CVSS:3.0", "CVSS:3.1"}:
        raise CVSSError(f"vector must start with 'CVSS:3.0/' or 'CVSS:3.1/' (got {parts[0]!r})")
    metrics: dict[str, str] = {}
    for part in parts[1:]:
        if ":" not in part:
            raise CVSSError(f"malformed metric {part!r} (expected KEY:VALUE)")
        key, val = part.split(":", 1)
        key, val = key.upper(), val.upper()
        if key in metrics:
            raise CVSSError(f"duplicate metric key {key!r}")
        allowed = _BASE_METRICS.get(key) or _TEMPORAL_METRICS.get(key) or _ENV_METRICS.get(key)
        if allowed is None:
            raise CVSSError(f"unknown metric key {key!r}")
        if val not in allowed:
            raise CVSSError(f"illegal value {val!r} for metric {key!r}")
        metrics[key] = val
    missing = [k for k in _BASE_METRICS if k not in metrics]
    if missing:
        raise CVSSError(f"missing mandatory base metric(s): {', '.join(missing)}")
    return metrics


def _impact_subscore(c: float, i: float, a: float) -> float:
    return 1.0 - (1.0 - c) * (1.0 - i) * (1.0 - a)


def base_score(metrics: dict[str, str]) -> float:
    """CVSS 3.1 base score from a parsed metric dict."""
    scope_changed = metrics["S"] == "C"
    pr_table = _PR_CHANGED if scope_changed else _PR_UNCHANGED

    exploitability = (
        8.22
        * _AV[metrics["AV"]]
        * _AC[metrics["AC"]]
        * pr_table[metrics["PR"]]
        * _UI[metrics["UI"]]
    )
    iss = _impact_subscore(_CIA[metrics["C"]], _CIA[metrics["I"]], _CIA[metrics["A"]])
    if scope_changed:
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        impact = 6.42 * iss

    if impact <= 0:
        return 0.0
    raw = (1.08 if scope_changed else 1.0) * (impact + exploitability)
    return _roundup(min(raw, 10.0))


def environmental_score(metrics: dict[str, str]) -> float:
    """CVSS 3.1 environmental score (modified base × temporal, spec §7.3)."""

    def mod(key: str, base_key: str) -> str:
        val = metrics.get(key, "X")
        return metrics[base_key] if val == "X" else val

    m_av, m_ac, m_pr, m_ui = (
        mod("MAV", "AV"),
        mod("MAC", "AC"),
        mod("MPR", "PR"),
        mod("MUI", "UI"),
    )
    m_s = mod("MS", "S")
    m_c, m_i, m_a = mod("MC", "C"), mod("MI", "I"), mod("MA", "A")
    scope_changed = m_s == "C"
    pr_table = _PR_CHANGED if scope_changed else _PR_UNCHANGED

    cr, ir, ar = (
        _REQ[metrics.get("CR", "X")],
        _REQ[metrics.get("IR", "X")],
        _REQ[metrics.get("AR", "X")],
    )
    miss = min(
        1.0 - (1.0 - _CIA[m_c] * cr) * (1.0 - _CIA[m_i] * ir) * (1.0 - _CIA[m_a] * ar),
        0.915,
    )
    if scope_changed:
        m_impact = 7.52 * (miss - 0.029) - 3.25 * (miss * 0.9731 - 0.02) ** 13
    else:
        m_impact = 6.42 * miss

    if m_impact <= 0:
        return 0.0
    m_exploit = 8.22 * _AV[m_av] * _AC[m_ac] * pr_table[m_pr] * _UI[m_ui]
    raw = (1.08 if scope_changed else 1.0) * (m_impact + m_exploit)
    temporal = _E[metrics.get("E", "X")] * _RL[metrics.get("RL", "X")] * _RC[metrics.get("RC", "X")]
    return _roundup(_roundup(min(raw, 10.0)) * temporal)


def score_vector(vector: str) -> dict[str, Any]:
    """Full deterministic scoring of a CVSS 3.1 vector string."""
    metrics = parse_vector(vector)
    base = base_score(metrics)
    has_env = any(k in metrics for k in (*_TEMPORAL_METRICS, *_ENV_METRICS))
    env = environmental_score(metrics) if has_env else base
    normalized = "CVSS:3.1/" + "/".join(
        f"{k}:{metrics[k]}"
        for k in (*_BASE_METRICS, *_TEMPORAL_METRICS, *_ENV_METRICS)
        if k in metrics
    )
    return {
        "valid": True,
        "normalized_vector": normalized,
        "base_score": base,
        "base_severity": severity_band(base),
        "environmental_score": env,
        "environmental_severity": severity_band(env),
        "has_environmental": has_env,
        "metrics": metrics,
    }


@tool("cvss_score")
def cvss_score_tool(vector: str) -> dict[str, Any]:
    """Compute the official CVSS 3.1 score for a vector — do not guess it.

    Use this at the Validate and Draft stages to derive the severity you
    write into a finding / report from the vector itself, instead of
    eyeballing a band. Pass the full vector string; the environmental
    score reflects any ``CR``/``IR``/``AR`` (the program/asset weighting)
    and ``M*`` modified metrics you include.

    Severity bands (CVSS 3.1 §5): 0.0 None · 0.1–3.9 Low · 4.0–6.9
    Medium · 7.0–8.9 High · 9.0–10.0 Critical.

    Args:
        vector: e.g. ``"CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N"``
            (optionally with temporal ``E/RL/RC`` and environmental
            ``CR/IR/AR/M*`` metrics appended).

    Returns:
        On success: ``{valid: true, normalized_vector, base_score,
        base_severity, environmental_score, environmental_severity,
        has_environmental, metrics}``. On a malformed/illegal vector:
        ``{valid: false, error: "<reason>"}`` — fix the vector and retry,
        do NOT fall back to a guessed number.
    """
    try:
        return score_vector(vector)
    except CVSSError as exc:
        return {"valid": False, "error": str(exc)}


CVSS_TOOLS = [cvss_score_tool]

__all__ = [
    "CVSSError",
    "CVSS_TOOLS",
    "base_score",
    "cvss_score_tool",
    "environmental_score",
    "parse_vector",
    "score_vector",
    "severity_band",
]
