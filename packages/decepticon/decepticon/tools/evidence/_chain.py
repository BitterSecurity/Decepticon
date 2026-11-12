"""Hash-chain primitives for the evidence chain-of-custody manifest.

Each record's ``hash`` is SHA-256 over its canonical JSON plus the previous
record's hash (64 zero hex digits for the first record), so changing record N
invalidates every later record. ``hmac`` binds ``hash`` to the operator-held
``DECEPTICON_AUDIT_HMAC_KEY`` when it is set and is ``""`` otherwise.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

_GENESIS_PREV_HASH = "0" * 64


def _canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _record_hash(record: dict[str, Any], prev_hash: str) -> str:
    payload = dict(record)
    payload["prev_hash"] = prev_hash
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _record_hmac(this_hash: str, key: bytes | None) -> str:
    if not key:
        return ""
    return hmac.new(key, this_hash.encode("ascii"), hashlib.sha256).hexdigest()
