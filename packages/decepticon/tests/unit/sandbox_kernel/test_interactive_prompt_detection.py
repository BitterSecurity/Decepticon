"""Tests for ``_looks_like_interactive_prompt``.

Stall detection in :mod:`decepticon_sandbox.kernel.tmux` used to label any
3 s no-output window as ``[interactive]``. That confused a silently hung
process (network deadlock, infinite wait) with a real prompt waiting for
input. ``_looks_like_interactive_prompt`` inspects the last non-blank
line of the screen and returns ``True`` only when that tail looks like a
shell or offensive-tool prompt — so the stall path can fall through to a
distinct "may be hung" message for prompt-less stalls.
"""

from __future__ import annotations

import pytest
from decepticon_sandbox.kernel.tmux import _looks_like_interactive_prompt


@pytest.mark.parametrize(
    "screen",
    [
        # Shell-style prompts (trailing whitespace is normal in tmux capture)
        "doing stuff\n$ ",
        "root@box:/# ",
        "user@host:~$",
        "Continue? ",
        "Password: ",
        "Press any key > ",
        # Offensive-tool / REPL prompts
        "[*] Started reverse handler\nmsf6 > ",
        "meterpreter > ",
        "sliver > ",
        "sliver (BLUE_PHANTOM) > ",
        "(Pdb) ",
        ">>> ",
        "mysql> ",
        "ftp> ",
    ],
)
def test_prompt_tails_detected(screen: str) -> None:
    assert _looks_like_interactive_prompt(screen) is True


@pytest.mark.parametrize(
    "screen",
    [
        # Plain log / spinner tails — NOT prompts
        "downloading...\n  42% [=====>     ]",
        "fetching metadata\nresolving deps",
        "INFO  connecting to 10.0.0.5",
        "Scanning 1000 ports",
        "",  # nothing on screen at all
        "   \n   ",  # only blank lines
    ],
)
def test_non_prompt_tails_rejected(screen: str) -> None:
    assert _looks_like_interactive_prompt(screen) is False
