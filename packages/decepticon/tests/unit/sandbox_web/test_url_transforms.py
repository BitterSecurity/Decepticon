"""Unit tests for the domain-agnostic URL transforms."""

from __future__ import annotations

import pytest
from decepticon_sandbox.web.url_transforms import (
    apply_transform,
    iter_transformed,
)


def test_original_is_identity() -> None:
    assert apply_transform("original", "https://www.example.com/a") == "https://www.example.com/a"


def test_mobile_subdomain_rewrites_www_to_m() -> None:
    assert apply_transform("mobile_subdomain", "https://www.example.com/a?b=1") == (
        "https://m.example.com/a?b=1"
    )


def test_mobile_subdomain_preserves_port() -> None:
    assert apply_transform("mobile_subdomain", "https://www.example.com:8443/a") == (
        "https://m.example.com:8443/a"
    )


def test_mobile_subdomain_skips_non_www() -> None:
    assert apply_transform("mobile_subdomain", "https://example.com/a") is None
    assert apply_transform("mobile_subdomain", "https://m.example.com/a") is None


def test_am_prefix_on_apex_only() -> None:
    assert apply_transform("am_prefix", "https://example.com/a") == "https://m.example.com/a"
    # www → handled by mobile_subdomain, not am_prefix
    assert apply_transform("am_prefix", "https://www.example.com/a") is None
    # multi-label subdomain → skipped
    assert apply_transform("am_prefix", "https://a.b.example.com/a") is None
    # already mobile → skipped
    assert apply_transform("am_prefix", "https://m.example.com/a") is None


def test_drop_www() -> None:
    assert apply_transform("drop_www", "https://www.example.com/a") == "https://example.com/a"
    assert apply_transform("drop_www", "https://example.com/a") is None


def test_unknown_transform_raises() -> None:
    with pytest.raises(ValueError, match="Unknown transform"):
        apply_transform("warp_drive", "https://example.com")


def test_iter_transformed_dedupes_and_skips() -> None:
    # apex URL: original applies; drop_www/mobile_subdomain skip; am_prefix applies.
    out = iter_transformed(
        "https://example.com/x",
        ["original", "drop_www", "mobile_subdomain", "am_prefix"],
    )
    names = [n for n, _ in out]
    urls = [u for _, u in out]
    assert names == ["original", "am_prefix"]
    assert urls == ["https://example.com/x", "https://m.example.com/x"]
    assert len(set(urls)) == len(urls)  # no dupes


def test_iter_transformed_www_host() -> None:
    out = iter_transformed(
        "https://www.example.com/x",
        ["original", "mobile_subdomain", "drop_www"],
    )
    assert out == [
        ("original", "https://www.example.com/x"),
        ("mobile_subdomain", "https://m.example.com/x"),
        ("drop_www", "https://example.com/x"),
    ]
