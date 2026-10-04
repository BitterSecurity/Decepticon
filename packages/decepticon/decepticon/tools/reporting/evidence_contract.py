"""Validate local finding/report evidence references without executing artifacts.

This checks authored metadata and local file availability, not whether an
artifact proves its claimed finding. Paths are engagement-relative POSIX paths;
all symlinks below the caller-selected engagement root are rejected.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

_EVIDENCE_ROOTS = ("findings/evidence/", "recon/evidence/", "exploit/evidence/", "evidence/")
_MAX_DOCUMENT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class EvidenceReference:
    path: str
    label: str
    role: str = "artifact"
    supports: str | None = None


@dataclass(frozen=True)
class EvidenceContract:
    references: tuple[EvidenceReference, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors


def canonical_relative_path(value: object) -> str:
    """Require one unencoded, canonical relative spelling; never normalize input."""
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("path must be a nonempty string of at most 1024 characters")
    if re.search(r"[\\\s?#%\x00-\x1f\x7f:]", value):
        raise ValueError("path contains whitespace, encoding, or reserved characters")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("path must be canonical and engagement-relative")
    return value


def evidence_path(value: object) -> str:
    path = canonical_relative_path(value)
    if not path.startswith(_EVIDENCE_ROOTS):
        raise ValueError("path must be inside an engagement evidence directory")
    return path


def _open_file(root: Path, relative: str) -> int:
    """Walk with directory descriptors so symlink swaps cannot redirect traversal."""
    parts = canonical_relative_path(relative).split("/")
    directory = os.open(root.resolve(strict=True), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in parts[:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("reference must identify a regular file")
        return descriptor
    finally:
        os.close(directory)


def _text(value: object, field: str, default: str | None = None) -> str | None:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError(f"{field} must be a nonempty string of at most 2000 characters")
    return value.strip()


def validate_evidence_metadata(root: Path, metadata: object) -> EvidenceContract:
    """Check legacy pointers, manifests and step references against local files.

    Missing optional fields are compatible with legacy reports. Invalid fields
    are reported rather than silently dropped. References are deduplicated by
    path; explicit manifest metadata takes precedence over a legacy pointer.
    """
    if not isinstance(metadata, dict):
        return EvidenceContract(errors=("frontmatter must be a mapping",))
    errors: list[str] = []
    references: dict[str, EvidenceReference] = {}

    def add(value: object, field: str, item: dict[str, Any] | None = None) -> None:
        try:
            path = evidence_path(value)
            entry = item or {}
            reference = EvidenceReference(
                path=path,
                label=_text(entry.get("label"), "label", path.rsplit("/", 1)[-1]) or path,
                role=_text(entry.get("role"), "role", "artifact") or "artifact",
                supports=_text(entry.get("supports"), "supports"),
            )
            if path in references and item is not None:
                raise ValueError("duplicate evidence_manifest path")
            references.setdefault(path, reference)
        except ValueError as exc:
            errors.append(f"{field}: {exc}")

    manifest = metadata.get("evidence_manifest", [])
    if not isinstance(manifest, list) or len(manifest) > 100:
        errors.append("evidence_manifest must be a list of at most 100 entries")
    else:
        for index, item in enumerate(manifest):
            field = f"evidence_manifest[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{field}: entry must be a mapping")
                continue
            add(item.get("path"), field, item)
    if "evidence_pointer" in metadata:
        add(metadata["evidence_pointer"], "evidence_pointer")

    steps = metadata.get("attack_steps", [])
    if not isinstance(steps, list) or len(steps) > 30:
        errors.append("attack_steps must be a list of at most 30 entries")
    else:
        for index, step in enumerate(steps):
            field = f"attack_steps[{index}]"
            if isinstance(step, str) and step.strip() and len(step) <= 2000:
                continue
            if not isinstance(step, dict):
                errors.append(f"{field}: expected text or a mapping")
                continue
            try:
                if _text(step.get("label"), "label") is None:
                    raise ValueError("label is required")
                if step.get("status") not in ("observed", "untested"):
                    raise ValueError("status must be observed or untested")
            except ValueError as exc:
                errors.append(f"{field}: {exc}")
            refs = step.get("evidence_refs", [])
            if not isinstance(refs, list) or len(refs) > 100:
                errors.append(f"{field}: evidence_refs must have at most 100 entries")
                continue
            for ref_index, ref in enumerate(refs):
                add(ref, f"{field}.evidence_refs[{ref_index}]")

    for path in references:
        try:
            os.close(_open_file(root, path))
        except (OSError, ValueError, RuntimeError):
            errors.append(f"{path}: missing, inaccessible, nonregular, or symlinked evidence file")
    return EvidenceContract(tuple(references.values()), tuple(errors))


class _FrontmatterLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate metadata keys instead of accepting the last one."""


def _mapping(loader: _FrontmatterLoader, node: yaml.MappingNode) -> dict:
    result: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("frontmatter keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


_FrontmatterLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def validate_finding_document(root: Path, document: str) -> EvidenceContract:
    """Validate a findings/*.md or report/*.md document and its declared references."""
    try:
        path = canonical_relative_path(document)
        if not path.startswith(("findings/", "report/")) or not path.endswith(".md"):
            raise ValueError("document must be Markdown under findings/ or report/")
        with os.fdopen(_open_file(root, path), "rb") as stream:
            data = stream.read(_MAX_DOCUMENT_BYTES + 1)
        if len(data) > _MAX_DOCUMENT_BYTES:
            raise ValueError("document exceeds 1 MiB")
        lines = data.decode("utf-8-sig").splitlines()
        if not lines or lines[0] != "---":
            return EvidenceContract()  # Legacy Markdown has no declared metadata.
        try:
            end = lines.index("---", 1)
        except ValueError as exc:
            raise ValueError("unterminated YAML frontmatter") from exc
        metadata = yaml.load("\n".join(lines[1:end]), Loader=_FrontmatterLoader)
        return validate_evidence_metadata(root, {} if metadata is None else metadata)
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        return EvidenceContract(errors=(f"document: {exc}",))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Local engagement workspace")
    parser.add_argument("document", help="Engagement-relative findings/ or report/ Markdown path")
    args = parser.parse_args()
    result = validate_finding_document(args.root, args.document)
    print(json.dumps({"valid": result.valid, **asdict(result)}, ensure_ascii=False, indent=2))
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
