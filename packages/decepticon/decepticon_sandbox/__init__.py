"""Decepticon sandbox runtime.

Code that runs INSIDE the engagement sandbox, kept out of the management-side
``decepticon`` package so importing it never triggers the framework boot:

* ``kernel`` — tmux sessions, background jobs, wire protocol
* ``server`` — the sandbox daemon and the managed OpenShell session bridge
* ``web`` — the open-web acquisition engine behind ``web_fetch``/``web_search``
* ``browser`` — the agent browser runner and provider adapters

The sandbox image installs this package (with ``decepticon_core``) under
``/usr/local/lib/decepticon-sandbox`` and registers that root with a ``.pth``
file, so ``python3 -I -m decepticon_sandbox.<module>`` works without any
environment variables.
"""
