"""LangChain ``@tool`` wrappers for the evidence package."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from decepticon.tools.evidence._chain import (
    _GENESIS_PREV_HASH,
    _record_hash,
    _record_hmac,
)
from decepticon.tools.evidence.asciicast import (
    AsciicastExportError,
    export_asciicast,
    list_recordings,
)

_MANIFEST_NAME = "chain-of-custody.jsonl"
_SEAL_FIELDS = ("path", "sha256", "size", "sealed_at")


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_ENGAGEMENT_WORKSPACE") or "/workspace")


def _evidence_dir() -> Path:
    return _workspace() / "evidence" / "recordings"


def _evidence_root(workspace_path: str) -> Path:
    base = Path(workspace_path) if workspace_path else _workspace()
    return base / "evidence"


def _hmac_key() -> bytes | None:
    env_key = os.environ.get("DECEPTICON_AUDIT_HMAC_KEY")
    return env_key.encode("utf-8") if env_key else None


def _hmac_equal(expected: str, stored: Any) -> bool:
    return hmac.compare_digest(expected, str(stored or ""))


def _hash_file(file_path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with file_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _evidence_files(evidence_root: Path, manifest_path: Path) -> list[Path]:
    found: list[Path] = []
    for candidate in sorted(evidence_root.rglob("*")):
        if candidate.is_file() and candidate != manifest_path:
            found.append(candidate)
    return found


@tool
def export_session_asciicast(
    session_name: str,
    pipe_pane_log_path: str = "",
    title: str = "",
) -> str:
    """Convert a tmux session's pipe-pane log into an asciicast v2 (.cast) file.

    Produces ``<workspace>/evidence/recordings/<session_name>.cast`` plus a
    ``.cast.manifest.json`` sidecar. The asciicast file can be played back
    in any browser with asciinema-player and bundled into the engagement
    out-brief for client visibility into agent actions.

    Args:
        session_name: tmux session identifier used in the output filename.
        pipe_pane_log_path: explicit path to the pipe-pane log. When empty,
            falls back to ``<workspace>/.tmux-logs/<session_name>.log`` (the
            default location used by the sandbox FastAPI daemon).
        title: optional asciicast title; defaults to ``"Decepticon session <name>"``.
    """
    log_path = (
        Path(pipe_pane_log_path)
        if pipe_pane_log_path
        else _workspace() / ".tmux-logs" / f"{session_name}.log"
    )
    out_dir = _evidence_dir()
    out_path = out_dir / f"{session_name}.cast"
    try:
        manifest = export_asciicast(
            log_path=log_path,
            output_path=out_path,
            session_name=session_name,
            title=title,
        )
    except AsciicastExportError as exc:
        return _json({"error": str(exc)})
    return _json({"status": "exported", **manifest})


@tool
def list_session_recordings() -> str:
    """List all asciicast recordings captured for the current engagement.

    Reads ``<workspace>/evidence/recordings/*.cast.manifest.json`` and
    returns the parsed manifests. Use this before generating the
    engagement out-brief to know which recordings are available for embedding.
    """
    manifests = list_recordings(_evidence_dir())
    return _json({"count": len(manifests), "recordings": manifests})


@tool(
    description=(
        "Seal every file under <workspace>/evidence/ into an append-only "
        "chain-of-custody manifest. Computes SHA-256 + byte size for each "
        "artifact and appends one HMAC-chained record per file to "
        "evidence/chain-of-custody.jsonl so later tampering is detectable. "
        "Pass workspace_path to override the engagement workspace root; "
        "leave it empty to use the active engagement. Run before out-brief "
        "to lock evidence integrity."
    )
)
def seal_evidence(workspace_path: str = "") -> str:
    evidence_root = _evidence_root(workspace_path)
    if not evidence_root.is_dir():
        return _json({"error": f"evidence directory not found: {evidence_root}"})
    manifest_path = evidence_root / _MANIFEST_NAME
    files = _evidence_files(evidence_root, manifest_path)
    key = _hmac_key()
    sealed_at = datetime.now(timezone.utc).isoformat()
    prev_hash = _GENESIS_PREV_HASH
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    prev_hash = str(json.loads(line).get("hash", prev_hash))
                except json.JSONDecodeError:
                    continue
    records: list[dict[str, Any]] = []
    with manifest_path.open("a", encoding="utf-8") as fh:
        for file_path in files:
            sha256, size = _hash_file(file_path)
            rel = file_path.relative_to(evidence_root).as_posix()
            record: dict[str, Any] = {
                "path": rel,
                "sha256": sha256,
                "size": size,
                "sealed_at": sealed_at,
            }
            this_hash = _record_hash(record, prev_hash)
            record["prev_hash"] = prev_hash
            record["hash"] = this_hash
            record["hmac"] = _record_hmac(this_hash, key)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            records.append(record)
            prev_hash = this_hash
    return _json(
        {
            "status": "sealed",
            "manifest": str(manifest_path),
            "sealed": len(records),
            "files": [r["path"] for r in records],
        }
    )


@tool(
    description=(
        "Verify the evidence chain-of-custody manifest. Re-hashes every file "
        "recorded in evidence/chain-of-custody.jsonl, recomputes the HMAC "
        "chain, and reports drift (changed SHA-256/size), missing files, and "
        "any broken chain links. Pass workspace_path to override the "
        "engagement workspace root. Returns ok=true only when every record "
        "still matches the file on disk."
    )
)
def verify_evidence(workspace_path: str = "") -> str:
    evidence_root = _evidence_root(workspace_path)
    manifest_path = evidence_root / _MANIFEST_NAME
    if not manifest_path.exists():
        return _json({"error": f"manifest not found: {manifest_path}"})
    key = _hmac_key()
    prev_hash = _GENESIS_PREV_HASH
    checked = 0
    drift: list[dict[str, Any]] = []
    missing: list[str] = []
    chain_errors: list[dict[str, Any]] = []
    with manifest_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                chain_errors.append({"reason": "undecodable record", "line": checked + 1})
                continue
            checked += 1
            core = {k: rec.get(k) for k in _SEAL_FIELDS}
            recomputed = _record_hash(core, prev_hash)
            if rec.get("prev_hash") != prev_hash or rec.get("hash") != recomputed:
                chain_errors.append({"path": rec.get("path"), "reason": "broken chain link"})
            elif key is not None and not _hmac_equal(
                _record_hmac(recomputed, key), rec.get("hmac")
            ):
                chain_errors.append({"path": rec.get("path"), "reason": "hmac mismatch"})
            prev_hash = str(rec.get("hash", prev_hash))
            file_path = evidence_root / str(rec.get("path", ""))
            if not file_path.is_file():
                missing.append(str(rec.get("path")))
                continue
            sha256, size = _hash_file(file_path)
            if sha256 != rec.get("sha256") or size != rec.get("size"):
                drift.append(
                    {
                        "path": rec.get("path"),
                        "expected_sha256": rec.get("sha256"),
                        "actual_sha256": sha256,
                        "expected_size": rec.get("size"),
                        "actual_size": size,
                    }
                )
    ok = not drift and not missing and not chain_errors
    return _json(
        {
            "ok": ok,
            "records_checked": checked,
            "drift": drift,
            "missing": missing,
            "chain_errors": chain_errors,
        }
    )


EVIDENCE_TOOLS = [
    export_session_asciicast,
    list_session_recordings,
    seal_evidence,
    verify_evidence,
]
