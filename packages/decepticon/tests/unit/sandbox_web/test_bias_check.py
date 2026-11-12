"""Tests for the No-Site-Name bias checker — and the gate itself.

``test_engine_tree_is_clean`` is the live CI gate: it asserts the shipped
``decepticon_sandbox/web`` engine has no hardcoded target hosts.
"""

from __future__ import annotations

from pathlib import Path

from decepticon_sandbox.web import bias_check


def test_engine_tree_is_clean() -> None:
    """The real engine package must pass the No-Site-Name rule."""
    assert bias_check.main([]) == 0


def test_hardcoded_host_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "leaky.py").write_text('TARGET = "https://acme-corp-internal.com/login"\n')
    assert bias_check.main(["--root", str(tmp_path)]) == 1


def test_allowlisted_host_is_clean(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text(
        'REFERER = "https://www.google.com/"\nEX = "http://example.com/x"\n'
    )
    assert bias_check.main(["--root", str(tmp_path)]) == 0


def test_note_bias_ok_exemption(tmp_path: Path) -> None:
    (tmp_path / "doc.py").write_text(
        'URL = "https://acme-corp-internal.com/x"  # NOTE-BIAS-OK illustrative\n'
    )
    assert bias_check.main(["--root", str(tmp_path)]) == 0


def test_waf_product_names_are_not_flagged(tmp_path: Path) -> None:
    # WAF vendor product strings (no TLD) must not trip the host regex.
    (tmp_path / "prof.yaml").write_text(
        'akamai:\n  body: ["Powered and protected by Akamai"]\n  server: ["AkamaiGHost"]\n'
    )
    assert bias_check.main(["--root", str(tmp_path)]) == 0
