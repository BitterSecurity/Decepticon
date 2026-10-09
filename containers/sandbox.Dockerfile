# syntax=docker/dockerfile:1
# Pin digest for reproducible builds and stable GHA cache layers.
# To update: docker pull kalilinux/kali-rolling:latest && docker inspect --format='{{index .RepoDigests 0}}' kalilinux/kali-rolling:latest
FROM kalilinux/kali-rolling@sha256:ab7f9873e9d976d62f59e172350604dd980339f567bfb2eaa5c2bdfaa2dc42b7

# Consolidated package install — one RUN layer to maximize cache hits
# and minimize image size. Kali apt sandbox disabled so it doesn't fail
# trying to drop privileges to the _apt user.
#
# BuildKit cache mounts on /var/cache/apt and /var/lib/apt/lists keep
# .deb downloads + the apt index cached across builds (local rebuilds,
# GHA cache-from=type=gha). The Debian-style /etc/apt/apt.conf.d/docker-clean
# auto-purge is disabled inline so cached .debs survive between RUN steps,
# and the trailing apt-get clean is gone — the cache mount paths aren't
# part of the image layer, so leaving them populated costs zero image MB.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked,id=sandbox-apt-cache \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked,id=sandbox-apt-lists \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo "APT::Sandbox::User \"root\";" > /etc/apt/apt.conf.d/10sandbox && \
    sed -i 's|https://|http://|g' /etc/apt/sources.list* 2>/dev/null; \
    find /etc/apt/sources.list.d/ -name '*.sources' -exec sed -i 's|https://|http://|g' {} + 2>/dev/null; \
    apt-get update && \
    apt-get install -y --no-install-recommends --no-install-suggests \
        ca-certificates && \
    update-ca-certificates && \
    sed -i 's|http://|https://|g' /etc/apt/sources.list* 2>/dev/null; \
    find /etc/apt/sources.list.d/ -name '*.sources' -exec sed -i 's|http://|https://|g' {} + 2>/dev/null; \
    apt-get update && \
    apt-get install -y --no-install-recommends --no-install-suggests \
        # ── Core runtime ──
        curl \
        wget \
        python3 \
        python3-pip \
        tmux \
        # ── Recon ──
        nmap \
        dnsutils \
        whois \
        netcat-openbsd \
        iputils-ping \
        subfinder \
        httpx-toolkit \
        katana \
        nuclei \
        naabu \
        whatweb \
        masscan \
        # ── Exploit & post-exploitation ──
        hydra \
        sqlmap \
        nikto \
        smbclient \
        exploitdb \
        dirb \
        gobuster \
        # SSH client + sshpass for lateral movement / multi-host scenarios
        # (e.g., MHBench OpenStack topologies — attacker pivots through a
        # jump host via ProxyJump to reach internal ring hosts).
        openssh-client \
        sshpass \
        # ── JavaScript runtime (JSFuck payload encoding/validation) ──
        nodejs \
        npm \
        # ── C2 client (connects to the separate c2-sliver server container) ──
        sliver \
        # ── AD attack chain — Responder → ntlmrelayx → secretsdump ──
        # responder + python3-impacket are both in kali-rolling apt; they
        # chain together for the canonical internal-network AD attack
        # documented in docs/red-team/tools-techniques.md.
        responder \
        python3-impacket \
        # ── Mobile triage host-side (Mobile agent) ──
        # adb + apktool are in kali-rolling apt and let the agent do quick
        # APK / device triage from the bash tool without leaving the sandbox.
        adb \
        apktool \
        # ── YARA (DFIR validation; complements yara-x on the host) ──
        yara

# ── Tools NOT yet wired into apt-install ──
# Deliberately deferred to follow-up PRs because they need either a
# GitHub-release pull (no apt package) or a pip install:
#   chisel       (GitHub release: jpillora/chisel)
#   ligolo-ng    (GitHub release: nicocha30/ligolo-ng)
#   afl++ / aflplusplus / honggfuzz (apt names + dep weight uncertain)
#   plaso        (pip: ``plaso``; pulls in pyparsing + heavy deps)
#   volatility3  (pip: ``volatility3``; same as operator's host install)
# Operators who need these today can ``pip install volatility3 plaso``
# inside the sandbox via the agent's bash tool. A follow-up PR will add
# them to the image once the right install paths are verified.

# Configure tmux: 20K line scrollback buffer. The Python-side output
# truncation (MAX_OUTPUT_CHARS = 30_000 chars) means the agent reads at
# most ~500 lines, but the tmux PS1-marker detection
# (TmuxSessionManager._wait_for_completion) counts ALL markers in the
# scrollback via capture-pane. Commands that produce >N lines cause old
# markers to scroll off, which breaks the count check. 20K handles
# realistic red-team tool output (nmap /24 ≈ 5–25K lines) while cutting
# per-session RSS by ~60% vs the previous 50K.
RUN echo "set-option -g history-limit 20000" > /root/.tmux.conf

# Optional HTTP sandbox daemon — see decepticon/sandbox_server/.
#
# The daemon is OFF by default. The existing dev / local-docker / GCE
# Spot deployments use this image as before (host docker daemon
# `docker exec`s into the container; entrypoint just tails forever).
# When `SANDBOX_DAEMON=1` is set at runtime — Cloud Run multi-container
# deploys do this — the entrypoint replaces the tail loop with the
# FastAPI server, and the agent container talks to it over HTTP
# instead of `docker exec`.
#
# Only `fastapi` + `uvicorn` + `deepagents` are pulled in here; the
# heavier decepticon agent / LLM / langgraph SDKs are deliberately
# left out so the sandbox image doesn't bloat for the >95% of users
# who never enable the daemon.
RUN pip3 install --break-system-packages --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn>=0.30.0" \
    "deepagents>=0.5.0"

# ── Open-web acquisition engine (insane-search port, ADR-0010) ───────
# decepticon/sandbox_web runs HERE, inside the sandbox, so all open-web
# egress stays in sandbox-net behind the nftables allowlist. Python deps:
#   * curl_cffi>=0.15 — TLS-impersonation fetch tier. 0.15 is REQUIRED by the
#                    engine: it ships the current Chrome (146+) JA3/JA4
#                    fingerprint, HTTP/3, and SSRF-safe redirect defaults; older
#                    pins are fingerprinted as stale and blocked.
#   * beautifulsoup4 — success-selector proof + DDG result parsing
#   * pyyaml       — WAF profile loading
#   * yt-dlp       — Phase 0 media route (YouTube/Vimeo/… --dump-json)
#   * pydantic*    — decepticon-core dep so the per-hop RoE scope_check
#                    (evaluate_target on <workspace>/plan/roe.json) is live.
RUN pip3 install --break-system-packages --no-cache-dir \
    "curl_cffi>=0.15.0" \
    "beautifulsoup4>=4.12.0" \
    "pyyaml>=6.0.0" \
    "yt-dlp>=2025.1.1" \
    "pydantic>=2.0.0" \
    "pydantic-settings>=2.0.0" \
    "typing-extensions>=4.0.0" \
    "playwright>=1.48.0"

# Playwright browser tier — the engine's last escalation rung for JS/WAF
# challenges (Cloudflare Turnstile, Akamai, DataDome) that the curl_cffi grid
# can't clear. The node templates in `decepticon/sandbox_web/templates/` launch
# Chromium with the puppeteer-extra stealth stack; their npm deps are installed
# LOCALLY in that dir AFTER the package is COPYed in (see below) — a global
# install does not resolve the stealth plugin's nested evasion deps. node/npm
# are already installed above.
#
# Browser binary: use Kali's system Chromium. `playwright install --with-deps`
# is NOT used — its hardcoded Debian font/xvfb apt list (fonts-liberation,
# xvfb, …) does not exist in the Kali repos and fails the build. apt installs
# Chromium WITH its runtime libs resolved by the distro; the templates launch it
# via executablePath (INSANE_CHROMIUM_PATH). Headless by default (no X server);
# INSANE_HEADLESS=0 + xvfb is the headful path for headless-detecting WAFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*
ENV INSANE_CHROMIUM_PATH=/usr/bin/chromium

# ── Reverse Engineering: Ghidra 12.1 + radare2 + binwalk (opt-in) ──
#
# Gated by a build ARG so the default sandbox image stays lean
# (~500 MB lighter without JDK 21 + Ghidra). Enable with:
#   docker build --build-arg INSTALL_REVERSING=true ...
# or, via docker-compose, set INSTALL_REVERSING=true in .env when running
# with COMPOSE_PROFILES=reversing. The ghidra-mcp sidecar service in
# docker-compose.yml builds this image with the ARG set to true.
#
# When disabled (default), ghidra_available() returns False and the
# tools/reversing/tools.py @tool wrappers surface a clean "Ghidra not
# installed — enable the reversing profile" error to the agent rather
# than crashing. The MCP path (http://ghidra-mcp:8089) is still wired
# up — agents can use the sidecar without the host sandbox having
# Ghidra installed locally.
#
# Pinned to the 20260513 build of Ghidra 12.1 so the image is
# reproducible. To upgrade: bump the URL + verify the SHA-256 against
# the GitHub release page, then bump GHIDRA_SHA256 below.
ARG INSTALL_REVERSING=false
ARG GHIDRA_VERSION=12.1
ARG GHIDRA_BUILD_DATE=20260513
ARG GHIDRA_SHA256=""
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked,id=sandbox-apt-cache \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked,id=sandbox-apt-lists \
    if [ "$INSTALL_REVERSING" = "true" ]; then \
        apt-get update && \
        apt-get install -y --no-install-recommends --no-install-suggests \
            openjdk-21-jdk-headless \
            radare2 \
            binwalk \
            unzip && \
        curl -fsSL -o /tmp/ghidra.zip \
            "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_BUILD_DATE}.zip" && \
        if [ -n "$GHIDRA_SHA256" ]; then \
            echo "$GHIDRA_SHA256  /tmp/ghidra.zip" | sha256sum -c - ; \
        fi && \
        unzip -q /tmp/ghidra.zip -d /opt && \
        mv "/opt/ghidra_${GHIDRA_VERSION}_PUBLIC" /opt/ghidra && \
        rm /tmp/ghidra.zip ; \
    else \
        echo "INSTALL_REVERSING=false — skipping JDK 21 + Ghidra + radare2 + binwalk" ; \
    fi

ENV GHIDRA_INSTALL_DIR=/opt/ghidra \
    GHIDRA_MCP_URL=http://ghidra-mcp:8089 \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

# Ship only the modules the daemon actually imports:
#   - decepticon/__init__.py     — package marker (light-weight, just reads __version__)
#   - decepticon/sandbox_kernel/ — shared sandbox primitives: TmuxSessionManager,
#                                  BackgroundJobTracker, SandboxBase, and DaemonSandbox.
#                                  The daemon instantiates `DaemonSandbox` (exec_prefix=[],
#                                  pathlib upload/download) — no `docker exec`, no
#                                  agent-side transport, so the sandbox image stays
#                                  free of the `backends/` package on purpose.
#   - decepticon/sandbox_server/ — the FastAPI app + uvicorn entry point.
# `backends/` (DockerSandbox + HTTPSandbox + factory) is deliberately
# absent — that's agent-side code, lives in the langgraph image. Other
# subtrees (agents / llm / middleware / tools / core) are left out too
# so the sandbox image doesn't bloat for the >95% of users who never
# enable the daemon and so the dependency surface stays minimal.
# Phase 0 of the core/framework/sdk split relocated the framework
# source tree to packages/decepticon/decepticon/. The sandbox
# image stays an exec-only daemon — it only needs ``sandbox_kernel``
# + ``sandbox_server`` + the bare ``__init__.py`` so they can be
# imported as a Python package under PYTHONPATH=/opt.
COPY packages/decepticon/decepticon/__init__.py /opt/decepticon/__init__.py
COPY packages/decepticon/decepticon/sandbox_kernel /opt/decepticon/sandbox_kernel
COPY packages/decepticon/decepticon/sandbox_server /opt/decepticon/sandbox_server
# Open-web engine (ADR-0010) + the pure contract layer it uses for the
# per-hop RoE scope_check. decepticon-core imports only pydantic + stdlib
# (never langchain/langgraph), so it is safe to ship to the lean sandbox.
COPY packages/decepticon/decepticon/sandbox_web /opt/decepticon/sandbox_web
COPY packages/decepticon-core/decepticon_core /opt/decepticon_core
ENV PYTHONPATH=/opt

# Install the Playwright templates' node deps LOCALLY (in their own dir) so the
# stealth plugin's full nested evasion tree (incl. user-preferences) resolves —
# a global install + NODE_PATH does not. The executor shells `node` here, so node
# resolves require() from templates/node_modules. Done after the COPY because the
# templates' package.json ships inside sandbox_web.
RUN cd /opt/decepticon/sandbox_web/templates \
    && npm install --no-fund --no-audit --omit=optional \
    && npm cache clean --force

# Skip the framework boot path on this image — the sandbox container
# ships only sandbox_kernel + sandbox_server, NOT decepticon-core or
# the rest of the framework. ``decepticon/__init__.py`` checks this
# env var and short-circuits the RoleRegistry + PluginRegistry setup
# so the sandbox process can ``python -m decepticon.sandbox_server``
# without importing decepticon-core (which isn't installed here).
ENV DECEPTICON_SKIP_BOOT=1

# Working directory for the agent's virtual filesystem.
# Runs as root — security boundary is the container, not the user.
# Root access is required for raw sockets (nmap SYN scans), packet capture,
# and unrestricted filesystem access during red team operations.
WORKDIR /workspace

# Skills are NO LONGER baked into the sandbox image. They live in the
# langgraph container (see ``containers/langgraph.Dockerfile``), where
# they are read in-process by ``FilesystemBackend`` via the
# ``CompositeBackend`` route declared in
# ``decepticon/backends/__init__.py:make_agent_backend``. Skills are
# read-only knowledge — they don't need the sandbox's isolated
# execution environment, and avoiding the HTTP round-trip per skill
# read saves agent-init latency.

# Entrypoint: chmod 777 /workspace so host user can access files without sudo.
# Security boundary is the container, not file permissions.
COPY containers/sandbox-entrypoint.sh /entrypoint.sh
# Strip any CR so the image builds correctly even from a Windows host
# whose checkout introduced CRLF line endings.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# Healthcheck: verify the sandbox is alive and tmux is usable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD tmux -V >/dev/null 2>&1 || exit 1

# Keep the container alive so the backend can 'docker exec' into it
CMD ["tail", "-f", "/dev/null"]
