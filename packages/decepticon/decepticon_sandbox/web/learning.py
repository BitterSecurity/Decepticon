"""U5: lightweight per-host self-learning store (`observations/learned.json`).

Records which fetch route (impersonate × referer × url-transform × phase) last
SUCCEEDED for a host, so the next visit promotes it to the probe / front of the
grid instead of rediscovering it from scratch. The store is bounded and
self-pruning so it can never grow without limit:

  * eviction on failure — a learned route that fails on a REAL block
    (`exhausted` / `challenge` / `blocked`) earns a strike; after
    ``EVICT_AFTER_FAILS`` consecutive real failures the entry is deleted.
    Transient outcomes (429 rate-limit, network/unknown error, budget cut) and
    URL-level outcomes (404/401) never strike — they are not the route's fault.
  * TTL — an entry unused for ``TTL_DAYS`` is pruned the next time the store is
    loaded (default 30 days).
  * cap — at most ``MAX_ENTRIES`` (default 500); on overflow the
    least-recently-used entries are dropped.

This is a DATA file, never code, so the No-Site-Name Rule (R3) holds: per-site
knowledge lives in JSON that both the engine and the agent can read, while the
fetch chain itself stays site-agnostic.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

TTL_DAYS = int(os.environ.get("INSANE_LEARN_TTL_DAYS", "30"))
MAX_ENTRIES = int(os.environ.get("INSANE_LEARN_MAX", "500"))
EVICT_AFTER_FAILS = 2

# stop_reason values that mean the bypass ROUTE genuinely failed (→ strike).
# Everything else (rate_limited / unknown / budget / auth_required / not_found /
# success / "") is transient or URL-level and never strikes the route.
PENALIZE_REASONS = frozenset({"exhausted", "challenge", "blocked"})


def enabled() -> bool:
    return os.environ.get("INSANE_LEARN", "1") not in ("0", "false", "no")


def default_path() -> str:
    p = os.environ.get("INSANE_LEARNED_PATH")
    if p:
        return p
    return os.path.join(os.path.expanduser("~"), ".insane_search", "learned.json")


def is_real_failure(stop_reason: str) -> bool:
    """True when `stop_reason` means the route itself was blocked (→ strike)."""
    return (stop_reason or "") in PENALIZE_REASONS


def key_for(url: str, device_class: str) -> str:
    host = (urlsplit(url).netloc or "").lower()
    dev = "mobile" if device_class == "mobile" else "desktop"
    return f"{host}::{dev}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _prune(data: dict, now: Optional[datetime] = None) -> dict:
    """Drop TTL-expired entries, then enforce the LRU cap. Pure (in-memory)."""
    now = now or _now()
    cutoff = now - timedelta(days=TTL_DAYS)
    kept = {}
    for k, v in data.items():
        lu = _parse(v.get("last_used", "")) if isinstance(v, dict) else None
        if lu is None or lu >= cutoff:
            kept[k] = v
    if len(kept) > MAX_ENTRIES:
        # keep the MAX_ENTRIES most-recently-used
        ordered = sorted(
            kept.items(),
            key=lambda kv: _parse(kv[1].get("last_used", "")) or now,
            reverse=True,
        )
        kept = dict(ordered[:MAX_ENTRIES])
    return kept


def load(path: Optional[str] = None) -> dict:
    """Load the store, pruning TTL-expired + over-cap entries in memory.

    Pruning is not persisted here (write-on-read is wasteful); the next
    `record_*` save writes the pruned set back, so the file converges."""
    path = path or default_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return _prune(data)


def save(data: dict, path: Optional[str] = None) -> None:
    path = path or default_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass  # learning is best-effort; never break a fetch on a write error


def lookup(
    url: str, device_class: str, path: Optional[str] = None, data: Optional[dict] = None
) -> Optional[dict]:
    """Return the learned route dict for this host, or None."""
    data = load(path) if data is None else data
    entry = data.get(key_for(url, device_class))
    if isinstance(entry, dict):
        route = entry.get("route")
        if isinstance(route, dict):
            return route
    return None


def record_success(url: str, device_class: str, route: dict, path: Optional[str] = None) -> None:
    """Upsert the winning route for this host (resets the failure strike)."""
    path = path or default_path()
    data = load(path)
    k = key_for(url, device_class)
    now = _now().isoformat()
    raw = data.get(k)
    entry = raw if isinstance(raw, dict) else {}
    same = entry.get("route") == route
    data[k] = {
        "route": route,
        "wins": int(entry.get("wins", 0)) + 1 if same else 1,
        "consecutive_fails": 0,
        "last_used": now,
        "last_success": now,
    }
    save(_prune(data), path)


def record_failure(url: str, device_class: str, penalize: bool, path: Optional[str] = None) -> None:
    """Record that the learned route did not win this run.

    `penalize=True` (a real block) strikes the entry and deletes it after
    EVICT_AFTER_FAILS consecutive strikes. `penalize=False` (transient / URL
    issue) just refreshes `last_used` so an actively-retried host is not
    TTL-pruned. No-op when nothing was learned for this host."""
    path = path or default_path()
    data = load(path)
    k = key_for(url, device_class)
    entry = data.get(k)
    if not isinstance(entry, dict):
        return
    if penalize:
        entry["consecutive_fails"] = int(entry.get("consecutive_fails", 0)) + 1
        entry["last_used"] = _now().isoformat()
        if entry["consecutive_fails"] >= EVICT_AFTER_FAILS:
            del data[k]
    else:
        entry["last_used"] = _now().isoformat()
    save(_prune(data), path)
