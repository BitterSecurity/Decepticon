"""Single entrypoint: insane-search generic fetch chain.

    from insane_search.engine import fetch
    result = fetch("https://example.com/path", success_selectors=["article"])

Public contract:
  * One function: `fetch(url, ...) -> FetchResult`.
  * Internal structure preserved as explicit phases so tests & debug logs
    can target each stage: probe → validate → detect → plan → execute → report.
  * `FetchResult.trace` exposes every attempt (transform × impersonate ×
    referer × executor) — callers can diagnose without re-running.

v2 scheduler (multi-AI review 2026-06-21):
  * `_build_plan` materializes the whole grid then orders it for DIVERSITY —
    one representative per TLS family across both URL transforms first, so a
    small attempt budget still touches every family/transform instead of
    burning out on the Safari family.
  * `tls_impersonate_avoid` entries are DEPRIORITIZED (moved last), never
    deleted — they are still attempted in exhaustive mode.
  * `max_attempts=None` (new default) means EXHAUSTIVE — run the full plan,
    honouring R6. A numeric cap is a *budget*, and exhaustion vs budget vs
    early-terminal is reported via `stop_reason` / `grid_exhausted`.
  * Jitter sleeps only on a CONTINUING (failed) attempt, never before a
    successful return.
  * `SUSPECT_OK` (abck unresolved / soft block) is NON-terminal: kept as
    best-effort, but the grid keeps searching for real proof.

No site-specific branching. Site knowledge enters only via:
  * `success_selectors` (caller-supplied positive proof)
  * `user_hint` (optional runtime hints; never persisted by this module)
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from .url_transforms import iter_transformed
from .validators import TERMINAL_NONSUCCESS, Verdict, validate
from .waf_detector import _load_profiles, detect, last_load_error, load_profile

_OK_VALUES = (Verdict.STRONG_OK.value, Verdict.WEAK_OK.value)
_TERMINAL_NONSUCCESS_VALUES = frozenset(v.value for v in TERMINAL_NONSUCCESS)

# Decepticon adaptation: an optional per-hop RoE scope predicate. Returns True
# if a host/URL is in engagement scope. When supplied (active target work) every
# transformed/redirected/phase0 host is gated; when None (OSINT web_fetch /
# web_search) only the SSRF safety layer (safety.classify_url, applied inside
# transport) governs egress. SSRF safety is ALWAYS on regardless of scope_check.
ScopeCheck = Callable[[str], bool]


def _scope_skip(url: str, transform: str, phase: str) -> "Attempt":
    """A non-network trace row recording that scope_check refused a host."""
    return Attempt(
        phase=phase,
        executor="scope_gate",
        url=url,
        url_transform=transform,
        impersonate=None,
        referer="",
        verdict=Verdict.BLOCKED.value,
        reasons=["roe_out_of_scope"],
        error="ROE_REFUSED: host not in engagement scope",
    )


# --- Referer strategies (name → function of original URL) --------------------
def _self_root(url: str) -> str:
    from urllib.parse import urlsplit

    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/"


REFERER_STRATEGIES = {
    "self_root": _self_root,
    "google_search": lambda _url: "https://www.google.com/",
    "none": lambda _url: "",
}


# --- Attempt & result schema -------------------------------------------------
@dataclass
class Attempt:
    phase: str  # probe | grid | fallback
    executor: str  # curl_cffi | playwright_real_chrome | ...
    url: str
    url_transform: str  # original | mobile_subdomain | ...
    impersonate: Optional[str]  # safari | chrome | ... | None (non-curl)
    referer: str
    status: int = 0
    body_size: int = 0
    verdict: str = ""
    reasons: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FetchResult:
    ok: bool
    content: str = ""
    final_url: str = ""
    verdict: str = ""
    profile_used: Optional[str] = None
    trace: list[Attempt] = field(default_factory=list)
    summary: str = ""
    # v2 scheduler diagnostics
    planned_attempts: int = 0
    executed_attempts: int = 0
    grid_exhausted: bool = False
    stop_reason: str = ""  # success | exhausted | budget | <terminal verdict> | error
    # Failure gate (R6): when ok=False these tell the caller it is NOT finished —
    # which escalation routes the engine could not perform itself remain to try.
    untried_routes: list[str] = field(default_factory=list)
    must_invoke_playwright_mcp: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "final_url": self.final_url,
            "verdict": self.verdict,
            "profile_used": self.profile_used,
            "trace": [a.to_dict() for a in self.trace],
            "summary": self.summary,
            "content_length": len(self.content),
            "planned_attempts": self.planned_attempts,
            "executed_attempts": self.executed_attempts,
            "grid_exhausted": self.grid_exhausted,
            "stop_reason": self.stop_reason,
            "untried_routes": self.untried_routes,
            "must_invoke_playwright_mcp": self.must_invoke_playwright_mcp,
        }


# --- curl_cffi probe executor ------------------------------------------------
def _curl_probe(
    url: str, *, impersonate: str, referer: str, timeout: int = 20
) -> tuple[Any, Optional[str]]:
    """Returns (response, error_str). response may be None on exception.

    Routes through the per-host SessionPool so cookies (WAF sensors) and the
    warm connection persist across attempts and across pages of the same host.
    The pool degrades to a one-shot GET when a Session can't be created.
    """
    from .transport import POOL

    return POOL.request(url, impersonate=impersonate, referer=referer, timeout=timeout)


def _run_attempt(
    url: str,
    *,
    transform_name: str,
    impersonate: str,
    referer_name: str,
    success_selectors: Optional[list[str]],
    known_bad_sizes: Optional[list[int]],
    timeout: int,
    phase: str,
) -> tuple[Attempt, Any]:
    """Execute one curl_cffi attempt and produce an Attempt record."""
    referer_url = REFERER_STRATEGIES.get(referer_name, REFERER_STRATEGIES["none"])(url)
    t0 = time.time()
    resp, err = _curl_probe(url, impersonate=impersonate, referer=referer_url, timeout=timeout)
    elapsed = round(time.time() - t0, 3)

    att = Attempt(
        phase=phase,
        executor="curl_cffi",
        url=url,
        url_transform=transform_name,
        impersonate=impersonate,
        referer=referer_name,
        elapsed_s=elapsed,
    )

    if err or resp is None:
        att.error = err or "no response"
        att.verdict = Verdict.UNKNOWN.value
        return att, None

    vr = validate(resp, success_selectors=success_selectors, known_bad_sizes=known_bad_sizes)
    att.status = vr.status
    att.body_size = vr.body_size
    att.verdict = vr.verdict.value
    att.reasons = vr.reasons
    return att, resp


# --- Diversity planner -------------------------------------------------------
@dataclass(frozen=True)
class _Cand:
    profile_id: str
    transform: str
    url: str
    impersonate: str
    referer: str
    known_bad_sizes: Optional[tuple]


_FAMILIES = ("safari_ios", "safari", "chrome_android", "chrome", "edge", "firefox")


def _family(tls: str) -> str:
    for fam in _FAMILIES:
        if tls.startswith(fam):
            return fam
    return tls


def _is_mobile_tls(t: str) -> bool:
    return ("ios" in t) or ("android" in t)


def _plan_for_profile(url: str, profile_id: str, profile: dict, device_class: str) -> list[_Cand]:
    groups: list[list[str]] = [
        list(g) for g in (profile.get("tls_impersonate_candidates") or [["safari", "chrome"]])
    ]
    avoid = set(profile.get("tls_impersonate_avoid") or [])
    referer_order = list(profile.get("referer_strategies") or ["self_root"])
    transform_order = list(profile.get("url_transform_order") or ["original"])
    kb = profile.get("known_bad_sizes") or None
    known_bad = tuple(kb) if kb else None

    # device_class shaping (fixes desktop/mobile drift)
    if device_class == "mobile":
        groups = [[t for t in g if _is_mobile_tls(t)] for g in groups]
        for extra in ("mobile_subdomain", "am_prefix"):
            if extra not in transform_order:
                transform_order.append(extra)
    elif device_class == "desktop":
        groups = [[t for t in g if not _is_mobile_tls(t)] for g in groups]
        transform_order = [
            t for t in transform_order if t not in ("mobile_subdomain", "am_prefix")
        ] or ["original"]

    # deprioritize (not delete) avoid targets within each family group
    def _reorder(g: list[str]) -> list[str]:
        return [t for t in g if t not in avoid] + [t for t in g if t in avoid]

    groups = [_reorder(g) for g in groups if g]
    if not groups:
        groups = [["safari", "chrome"]]

    transforms = iter_transformed(url, transform_order) or [("original", url)]

    # Diversity ordering: vary FAMILY fastest, then TRANSFORM, then version
    # DEPTH, then REFERER. A small budget thus touches every family/transform
    # before exhausting one family's old versions.
    max_depth = max(len(g) for g in groups)
    cands: list[_Cand] = []
    seen: set[tuple] = set()
    for ref in referer_order:
        for depth in range(max_depth):
            for t_name, t_url in transforms:
                for g in groups:
                    if depth >= len(g):
                        continue
                    imp = g[depth]
                    key = (t_url, imp, ref)
                    if key in seen:
                        continue
                    seen.add(key)
                    cands.append(_Cand(profile_id, t_name, t_url, imp, ref, known_bad))
    return cands


def _build_plan(
    url: str,
    hits: list,
    profiles: dict,
    device_class: str,
    probe_impersonate: str,
    probe_referer: str,
    priority: Optional[dict] = None,
) -> list[_Cand]:
    """Materialize a diversity-ordered candidate plan across the top profiles.

    Profiles are round-robin interleaved so a confident #1 profile cannot
    starve #2/#3. The probe combo is removed (already executed).

    `priority` (U5 self-learning): a previously-successful route
    ``{"transform","impersonate","referer"}`` for this host — the matching
    candidate is moved to the FRONT so a known-good route is retried first."""
    per: list[list[_Cand]] = []
    for hit in hits[:3]:
        pid = getattr(hit, "profile_id", None) or "unknown_challenge"
        prof = load_profile(pid, profiles=profiles)
        per.append(_plan_for_profile(url, pid, prof, device_class))

    probe_key = (url, probe_impersonate, probe_referer)
    merged: list[_Cand] = []
    seen: set[tuple] = set()
    i = 0
    while any(i < len(p) for p in per):
        for p in per:
            if i < len(p):
                c = p[i]
                key = (c.url, c.impersonate, c.referer)
                if key == probe_key or key in seen:
                    continue
                seen.add(key)
                merged.append(c)
        i += 1

    if priority:
        front = [
            c
            for c in merged
            if c.transform == priority.get("transform")
            and c.impersonate == priority.get("impersonate")
            and c.referer == priority.get("referer")
        ]
        if front:
            rest = [c for c in merged if c not in front]
            merged = front + rest
    return merged


# --- Public entrypoint: self-learning wrapper (U5) ---------------------------
def _winning_route(result: FetchResult) -> Optional[dict]:
    """Extract the curl route that produced the OK result, from the trace.

    Only probe/grid curl wins are learnable: Phase 0 always runs first anyway,
    and a browser win carries no reusable curl identity."""
    for att in reversed(result.trace):
        if (
            att.verdict in _OK_VALUES
            and att.phase in ("probe", "grid")
            and att.executor == "curl_cffi"
            and att.impersonate
        ):
            return {
                "transform": att.url_transform,
                "impersonate": att.impersonate,
                "referer": att.referer,
                "phase": att.phase,
            }
    return None


def fetch(
    url: str,
    *,
    success_selectors: Optional[list[str]] = None,
    device_class: str = "auto",
    user_hint: Optional[dict] = None,
    timeout: int = 25,
    max_attempts: Optional[int] = None,
    max_browser_attempts: int = 2,
    enable_playwright: bool = True,
    enable_phase0: bool = True,
    enable_learning: bool = True,
    scope_check: Optional[ScopeCheck] = None,
) -> FetchResult:
    """Public entrypoint — the generic grid wrapped with per-host self-learning.

    1. Before fetching, look up the route that last succeeded for this host and
       promote it: it becomes the probe identity AND the front of the grid.
    2. After fetching, record the winning route; or, if a learned route was
       promoted and the run hit a REAL block, strike it (evicted after two
       consecutive strikes — see `learning.py`).

    The store is a bounded, self-pruning JSON file; any error in it is swallowed
    so learning can never break a fetch. Disable per-call with
    ``enable_learning=False`` or globally with ``INSANE_LEARN=0``."""
    priority: Optional[dict] = None
    learned_existed = False
    uh = dict(user_hint or {})
    try:
        from . import learning

        if enable_learning and learning.enabled():
            priority = learning.lookup(url, device_class)
            if priority:
                learned_existed = True
                uh.setdefault("impersonate_first", priority.get("impersonate"))
                uh.setdefault("referer_strategy", priority.get("referer"))
    except Exception:
        priority = None

    result = _fetch_core(
        url,
        success_selectors=success_selectors,
        device_class=device_class,
        user_hint=uh,
        timeout=timeout,
        max_attempts=max_attempts,
        max_browser_attempts=max_browser_attempts,
        enable_playwright=enable_playwright,
        enable_phase0=enable_phase0,
        priority=priority,
        scope_check=scope_check,
    )

    try:
        from . import learning

        if enable_learning and learning.enabled():
            if result.ok:
                win = _winning_route(result)
                if win:
                    learning.record_success(url, device_class, win)
            elif learned_existed:
                learning.record_failure(
                    url, device_class, penalize=learning.is_real_failure(result.stop_reason)
                )
    except Exception:
        pass

    return result


# --- Main entrypoint ---------------------------------------------------------
def _fetch_core(
    url: str,
    *,
    success_selectors: Optional[list[str]] = None,
    device_class: str = "auto",  # "auto" | "desktop" | "mobile"
    user_hint: Optional[dict] = None,
    timeout: int = 25,
    max_attempts: Optional[int] = None,  # None = exhaustive (R6); int = budget
    max_browser_attempts: int = 2,
    enable_playwright: bool = True,
    enable_phase0: bool = True,
    priority: Optional[dict] = None,  # U5: learned route to retry first
    scope_check: Optional[ScopeCheck] = None,  # Decepticon: per-hop RoE gate
) -> FetchResult:
    """Fetch `url` using the generic diversity grid.

    max_attempts
        None (default) → run the whole plan (exhaustive, honours R6).
        int → TOTAL curl-attempt budget (probe included). On budget exit the
        result reports `stop_reason="budget"`, `grid_exhausted=False`, so
        callers never mistake a truncated run for a true exhaustive failure.
    """
    user_hint = user_hint or {}
    profiles = _load_profiles()
    trace: list[Attempt] = []
    last_resp = None
    last_attempt: Optional[Attempt] = None
    best_suspect: Optional[tuple] = None  # (resp, attempt)
    profile_used: Optional[str] = None

    _jmin = int(os.environ.get("INSANE_JITTER_MS_MIN", "150"))
    _jmax = int(os.environ.get("INSANE_JITTER_MS_MAX", "400"))

    def _jitter():
        time.sleep(random.uniform(_jmin / 1000.0, _jmax / 1000.0))

    # Surface profile-loader failures as a diagnostic trace entry (not counted
    # as a network attempt).
    load_err = last_load_error()
    if load_err:
        trace.append(
            Attempt(
                phase="probe",
                executor="profile_loader",
                url=url,
                url_transform="original",
                impersonate=None,
                referer="",
                verdict=Verdict.UNKNOWN.value,
                error=f"profiles_fallback: {load_err}",
            )
        )

    # -------- Decepticon: entry RoE gate ------------------------------------
    # Refuse an out-of-scope target host up front (when a scope_check is given).
    # SSRF safety is enforced separately inside transport on every hop.
    if scope_check is not None and not scope_check(url):
        trace.append(_scope_skip(url, "original", "probe"))
        return FetchResult(
            ok=False,
            content="",
            final_url=url,
            verdict=Verdict.BLOCKED.value,
            profile_used=None,
            trace=trace,
            summary="ROE_REFUSED: target host not in engagement scope",
            stop_reason="roe_refused",
        )

    # -------- Phase 0: official public-API router (R5; site-aware, sanctioned) --
    # For recognised platforms (Reddit/X/YouTube/...) try the official no-auth
    # endpoint BEFORE the generic grid. This is the *enforced* version of the
    # old agent-driven SKILL snippets — the agent can no longer skip it, which
    # is what made Reddit/X look "blocked" (grid 403'd .json; nobody tried .rss).
    # Phase 0 reaches THIRD-PARTY hosts (syndication CDNs, yt-dlp targets), so it
    # is gated by scope_check too — passed through to phase0.route below.
    if enable_phase0:
        try:
            from .phase0 import route as _phase0_route

            p0 = _phase0_route(url, timeout=timeout, scope_check=scope_check)
        except Exception as e:  # router must never break the generic chain
            p0 = None
            trace.append(
                Attempt(
                    phase="phase0",
                    executor="phase0",
                    url=url,
                    url_transform="original",
                    impersonate=None,
                    referer="",
                    verdict=Verdict.UNKNOWN.value,
                    error=f"{type(e).__name__}:{str(e)[:120]}",
                )
            )
        if p0 is not None:
            for a in p0["attempts"]:
                trace.append(
                    Attempt(
                        phase="phase0",
                        executor=a["route"],
                        url=url,
                        url_transform="-",
                        impersonate=None,
                        referer="",
                        status=a.get("status", 0),
                        body_size=a.get("bytes", 0),
                        verdict=(Verdict.STRONG_OK.value if a["ok"] else Verdict.BLOCKED.value),
                        reasons=[a["note"]] if a.get("note") else [],
                    )
                )
            if p0["ok"]:
                return FetchResult(
                    ok=True,
                    content=p0["content"],
                    final_url=p0["final_url"],
                    verdict=Verdict.STRONG_OK.value,
                    profile_used=f"phase0:{p0['platform']}",
                    trace=trace,
                    summary=f"Phase 0 official route: {p0['platform']}:{p0['route']}",
                    stop_reason="success",
                )
            # Recognised platform but every official route failed → fall through
            # to the generic grid (don't give up; R6).

    # -------- Phase 1: probe -------------------------------------------------
    base_impersonate = user_hint.get("impersonate_first") or (
        "safari_ios" if device_class == "mobile" else "safari"
    )
    base_referer = user_hint.get("referer_strategy") or "self_root"

    # Root warmup (deep URLs only): let a WAF sensor set a resolved cookie on
    # the probe identity's session before the deep request — the classic
    # first-hit rejection fix. Skipped when the target already IS the root.
    try:
        from .transport import POOL, _host_of, _root_of, pool_enabled

        if pool_enabled():
            _root = _root_of(url)
            if _root != url:
                POOL.warmup(_host_of(url), base_impersonate, _root, timeout=min(timeout, 15))
    except Exception:
        pass

    curl_attempts = 0
    probe_attempt, probe_resp = _run_attempt(
        url,
        transform_name="original",
        impersonate=base_impersonate,
        referer_name=base_referer,
        success_selectors=success_selectors,
        known_bad_sizes=None,
        timeout=timeout,
        phase="probe",
    )
    trace.append(probe_attempt)
    curl_attempts += 1
    if probe_resp is not None:
        last_resp, last_attempt = probe_resp, probe_attempt
        if probe_attempt.verdict in _OK_VALUES:
            return _build_result(
                probe_resp,
                probe_attempt,
                trace,
                profile_used=None,
                planned=0,
                executed=curl_attempts,
                grid_exhausted=False,
                stop_reason="success",
            )
        if probe_attempt.verdict == Verdict.SUSPECT_OK.value:
            best_suspect = (probe_resp, probe_attempt)
        elif probe_attempt.verdict in _TERMINAL_NONSUCCESS_VALUES:
            return _give_up(
                trace,
                profile_used,
                last_resp,
                last_attempt,
                best_suspect,
                planned=0,
                executed=curl_attempts,
                grid_exhausted=False,
                stop_reason=probe_attempt.verdict,
            )

    # -------- Phase 2: detect + plan + execute ------------------------------
    if last_resp is not None:
        hits = detect(last_resp, profiles=profiles)
    else:
        hits = [
            type(
                "H",
                (),
                {
                    "profile_id": "unknown_challenge",
                    "confidence": 0.1,
                    "signals": ["no_probe_response"],
                },
            )()
        ]
    profile_used = hits[0].profile_id if hits else None

    plan = _build_plan(
        url, hits, profiles, device_class, base_impersonate, base_referer, priority=priority
    )
    planned = len(plan)
    grid_exhausted = False
    stop_reason = ""

    for cand in plan:
        if max_attempts is not None and curl_attempts >= max_attempts:
            stop_reason = "budget"
            break
        # Decepticon: a url_transform (m.→ apex, drop-www) can change the host;
        # re-gate every transformed candidate against engagement scope.
        if scope_check is not None and not scope_check(cand.url):
            trace.append(_scope_skip(cand.url, cand.transform, "grid"))
            continue
        att, resp = _run_attempt(
            cand.url,
            transform_name=cand.transform,
            impersonate=cand.impersonate,
            referer_name=cand.referer,
            success_selectors=success_selectors,
            known_bad_sizes=list(cand.known_bad_sizes) if cand.known_bad_sizes else None,
            timeout=timeout,
            phase="grid",
        )
        trace.append(att)
        curl_attempts += 1
        if resp is not None:
            last_resp, last_attempt = resp, att
            if att.verdict in _OK_VALUES:
                return _build_result(
                    resp,
                    att,
                    trace,
                    profile_used=cand.profile_id,
                    planned=planned,
                    executed=curl_attempts,
                    grid_exhausted=False,
                    stop_reason="success",
                )
            if att.verdict == Verdict.SUSPECT_OK.value and best_suspect is None:
                best_suspect = (resp, att)
            if att.verdict in _TERMINAL_NONSUCCESS_VALUES:
                stop_reason = att.verdict
                break
        # continuing → polite jitter (only on non-terminal failure)
        _jitter()
    else:
        grid_exhausted = True
        stop_reason = "exhausted"

    # If a terminal-nonsuccess (404/auth/429) stopped us, browser won't help.
    skip_browser = stop_reason in _TERMINAL_NONSUCCESS_VALUES
    # Curl transport already refused a private/internal redirect hop. Do not
    # re-try the same attacker-controlled URL with the browser tier, which would
    # otherwise follow that redirect outside the curl SSRF guard.
    if any((a.error or "").startswith("ssrf_redirect_blocked:") for a in trace):
        skip_browser = True
        stop_reason = stop_reason or "ssrf_redirect_blocked"

    # -------- Phase 3: Playwright fallback ----------------------------------
    if enable_playwright and not skip_browser:
        browser_used = 0
        try:
            from .executor import run_playwright_fallback

            fb_profile = load_profile(profile_used or "unknown_challenge", profiles=profiles)
            fb_order = fb_profile.get("fallback_when_challenge") or ["playwright_real_chrome"]
            for fb_name in fb_order:
                if fb_name == "curl_grid_exhaust":
                    continue
                if browser_used >= max_browser_attempts:
                    break
                pw_attempt, pw_content = run_playwright_fallback(
                    url,
                    profile_id=profile_used or "unknown_challenge",
                    success_selectors=success_selectors,
                    device_class=device_class,
                    force_executor=fb_name,
                    timeout=timeout if timeout and timeout > 30 else 90,
                    scope_check=scope_check,
                )
                trace.append(pw_attempt)
                browser_used += 1
                if pw_attempt.verdict in _OK_VALUES:
                    return FetchResult(
                        ok=True,
                        content=pw_content,
                        final_url=pw_attempt.url,
                        verdict=pw_attempt.verdict,
                        profile_used=profile_used,
                        trace=trace,
                        summary=f"Playwright fallback succeeded via {fb_name}",
                        planned_attempts=planned,
                        executed_attempts=curl_attempts,
                        grid_exhausted=grid_exhausted,
                        stop_reason="success",
                    )
                if pw_attempt.verdict == Verdict.SUSPECT_OK.value and best_suspect is None:
                    best_suspect = (None, pw_attempt)
        except ImportError:
            trace.append(
                Attempt(
                    phase="fallback",
                    executor="playwright",
                    url=url,
                    url_transform="original",
                    impersonate=None,
                    referer="",
                    verdict=Verdict.UNKNOWN.value,
                    error="executor module not available",
                )
            )
        except Exception as e:
            trace.append(
                Attempt(
                    phase="fallback",
                    executor="playwright",
                    url=url,
                    url_transform="original",
                    impersonate=None,
                    referer="",
                    verdict=Verdict.UNKNOWN.value,
                    error=f"{type(e).__name__}:{str(e)[:200]}",
                )
            )

    # -------- Give up, return best we have ----------------------------------
    return _give_up(
        trace,
        profile_used,
        last_resp,
        last_attempt,
        best_suspect,
        planned=planned,
        executed=curl_attempts,
        grid_exhausted=grid_exhausted,
        stop_reason=stop_reason or "exhausted",
    )


def _untried_routes(stop_reason, grid_exhausted) -> tuple[list[str], bool]:
    """Failure gate (R6): name the escalation routes the engine itself could not
    perform, so the caller never mistakes give-up for "everything was tried".

    Returns (untried_routes, must_invoke_playwright_mcp).
    """
    routes: list[str] = []
    # 429 is TRANSIENT, not a wall — exclude it from terminal so the gate still
    # surfaces backoff/MCP instead of telling the agent to give up (the exact
    # premature-failure this hardening exists to prevent).
    rate_limited = stop_reason == Verdict.RATE_LIMITED.value
    # Terminal non-success (404 / auth / paywall) → a real wall; nothing else helps.
    terminal = stop_reason in _TERMINAL_NONSUCCESS_VALUES and not rate_limited
    if terminal:
        return routes, False

    if rate_limited:
        routes.append(
            "rate-limited (429) — transient: back off a few seconds then retry; a different TLS family or Playwright MCP often clears it. Do NOT hammer the grid."
        )
    # Budget cut → the curl grid itself was not finished (skip for 429: don't hammer).
    elif stop_reason == "budget" or not grid_exhausted:
        routes.append("generic-grid: NOT exhausted — re-run fetch() with max_attempts=None")

    # A gated page that survived the curl grid → the real browser is the next
    # escalation, and Playwright MCP must be driven from the AGENT session
    # (the engine can only spawn local Node Chrome, which Cloudflare-class
    # challenges often detect). So MCP is, by construction, an untried route here.
    must_mcp = True
    routes.append(
        "playwright_mcp (run from the agent session): browser_navigate → "
        "browser_network_requests → catch /api,/graphql,*.json internal endpoint → "
        "re-fetch that API URL with `python3 -m engine`; or browser_snapshot for rendered HTML"
    )
    routes.append(
        "user_hint retry: fetch(url, user_hint={'impersonate_first': 'safari_ios'|'chrome', 'referer_strategy': 'none'}) and/or device_class='mobile'"
    )
    return routes, must_mcp


def _give_up(
    trace,
    profile_used,
    last_resp,
    last_attempt,
    best_suspect,
    *,
    planned,
    executed,
    grid_exhausted,
    stop_reason,
) -> FetchResult:
    """Return the most honest failure result, preferring suspect content."""
    untried, must_mcp = _untried_routes(stop_reason, grid_exhausted)
    if best_suspect is not None:
        s_resp, s_att = best_suspect
        content = getattr(s_resp, "text", "") if s_resp is not None else ""
        return FetchResult(
            ok=False,
            content=content or "",
            final_url=str(getattr(s_resp, "url", s_att.url)) if s_resp is not None else s_att.url,
            verdict=s_att.verdict,
            profile_used=profile_used,
            trace=trace,
            summary=_format_summary(trace, profile_used, stop_reason),
            planned_attempts=planned,
            executed_attempts=executed,
            grid_exhausted=grid_exhausted,
            stop_reason=stop_reason,
            untried_routes=untried,
            must_invoke_playwright_mcp=must_mcp,
        )
    return FetchResult(
        ok=False,
        content=getattr(last_resp, "text", "") if last_resp is not None else "",
        final_url=str(getattr(last_resp, "url", url_of(last_attempt)))
        if last_resp is not None
        else url_of(last_attempt),
        verdict=last_attempt.verdict if last_attempt else Verdict.UNKNOWN.value,
        profile_used=profile_used,
        trace=trace,
        summary=_format_summary(trace, profile_used, stop_reason),
        planned_attempts=planned,
        executed_attempts=executed,
        grid_exhausted=grid_exhausted,
        stop_reason=stop_reason,
        untried_routes=untried,
        must_invoke_playwright_mcp=must_mcp,
    )


def url_of(attempt: Optional[Attempt]) -> str:
    return attempt.url if attempt else ""


def fetch_many(urls: list[str], **kwargs) -> list[FetchResult]:
    """Fetch many URLs, reusing the per-host SessionPool across calls.

    The first URL of a host may pay for warmup / browser bootstrap; later URLs
    of the SAME host reuse the winning session's cookies + connection, which is
    where R7-style bulk collection gets its throughput. Ordering by host keeps
    the warm session hot."""
    by_host: dict[str, list[int]] = {}
    for i, u in enumerate(urls):
        from .transport import _host_of

        by_host.setdefault(_host_of(u), []).append(i)
    results: list[Optional[FetchResult]] = [None] * len(urls)
    for _host, idxs in by_host.items():
        for i in idxs:
            results[i] = fetch(urls[i], **kwargs)
    return [r for r in results if r is not None]


def _build_result(
    resp,
    attempt: Attempt,
    trace: list[Attempt],
    profile_used: Optional[str],
    *,
    planned: int,
    executed: int,
    grid_exhausted: bool,
    stop_reason: str,
) -> FetchResult:
    return FetchResult(
        ok=True,
        content=getattr(resp, "text", "") or "",
        final_url=str(getattr(resp, "url", attempt.url)),
        verdict=attempt.verdict,
        profile_used=profile_used,
        trace=trace,
        summary=f"{attempt.executor} {attempt.impersonate} + {attempt.url_transform} + referer:{attempt.referer} → {attempt.verdict}",
        planned_attempts=planned,
        executed_attempts=executed,
        grid_exhausted=grid_exhausted,
        stop_reason=stop_reason,
    )


# WAF profiles known to typically gate HTML but leave internal JSON APIs
# (relatively) open. R7 hint surfaces an API-first route.
_R7_ELIGIBLE_PROFILES = frozenset(
    {
        "akamai_bot_manager",
        "cloudflare_turnstile",
        "datadome_probable",
        "perimeterx_human",
        "f5_big_ip",
        "aws_waf",
    }
)

R7_HINT = (
    "💡 R7 API-first 권장: WAF가 HTML 경로를 차단 중. "
    "Playwright MCP 사용 → browser_navigate → browser_network_requests "
    "→ `/api/`·`/graphql`·`\\.json` 필터로 내부 엔드포인트 탐지 → "
    "해당 URL을 `python3 -m engine <API_URL>`로 재호출. 대부분 API 레이어는 "
    "WAF 방어가 얕아 curl_cffi만으로 수집됨."
)


def _format_summary(trace: list[Attempt], profile: Optional[str], stop_reason: str = "") -> str:
    n = len(trace)
    verdicts = [a.verdict for a in trace]
    challenge_count = sum(1 for v in verdicts if v == Verdict.CHALLENGE.value)
    base = (
        f"failed after {n} attempts; profile={profile}; stop={stop_reason}; "
        f"verdicts={','.join(v for v in verdicts[:5])}" + ("..." if n > 5 else "")
    )
    if profile in _R7_ELIGIBLE_PROFILES and challenge_count >= 3:
        return base + "\n" + R7_HINT
    return base
