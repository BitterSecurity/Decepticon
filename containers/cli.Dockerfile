# ── Stage 1: Build ────────────────────────────────────────────────
FROM node:24-slim AS builder
WORKDIR /app

# Copy workspace root (lock file) + cli package.json for dependency install
COPY package.json package-lock.json ./
COPY clients/cli/package.json clients/cli/
COPY clients/shared/streaming/package.json clients/shared/streaming/

RUN npm ci --workspace=@decepticon/cli

# Copy CLI and shared source and build. Build the shared workspace first
# so the CLI's tsc compile + runtime both resolve `@decepticon/streaming`
# to its emitted dist/ (its package.json main points at dist/index.js).
COPY clients/shared/ clients/shared/
COPY clients/cli/ clients/cli/

# Stamp the package version from the git tag at build time. Source-tree
# package.json carries a "0.0.0" sentinel; release.yml passes the real
# version via --build-arg so the shipped package metadata matches the tag.
# Stamp after the source COPY so it cannot restore the sentinel; keep npm ci
# independent of VERSION so dependency caching also survives releases.
ARG VERSION=0.0.0
RUN sed -i 's/"version": "[^"]*"/"version": "'"$VERSION"'"/' clients/cli/package.json

RUN npm run build --workspace=@decepticon/streaming
RUN npm run build --workspace=@decepticon/cli

# ── Stage 2: Runtime ──────────────────────────────────────────────
FROM node:24-slim
WORKDIR /app

# The `/blue` slash command shells out to `docker compose` against the host
# docker daemon (the docker.sock is bind-mounted in docker-compose.yml) to
# start and stop the local Blue Cell sensor. That requires
# the docker CLI + compose plugin inside this image. Pulled from Docker's
# official Debian repo so we get docker-ce-cli + docker-compose-plugin
# without dragging in the dockerd daemon (which docker.io would).
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin docker-compose-plugin && \
    apt-get purge -y --auto-remove gnupg && \
    rm -rf /var/lib/apt/lists/*

# Copy compiled output + runtime dependencies. We run plain `node` on the
# tsc-emitted dist/ — no tsx runtime loader. tsx 4.20+ registers a JSON
# transform on the module loader that rewrites `*.json` into ESM JS, which
# breaks any CJS dependency doing `require('./*.json')` (notably
# `cli-boxes` via `boxen` via `ink`). Compiling ahead of time and dropping
# tsx at runtime removes that hazard entirely.
COPY --from=builder /app/clients/cli/package.json ./
COPY --from=builder /app/node_modules ./node_modules
# Shared workspace package is symlinked from node_modules; copy its
# package.json + dist so the symlink resolves to a real ESM build.
COPY --from=builder /app/clients/shared/streaming/package.json ./clients/shared/streaming/package.json
COPY --from=builder /app/clients/shared/streaming/dist ./clients/shared/streaming/dist
COPY --from=builder /app/clients/cli/dist ./dist
# The blue sensor stack moved to BitterSecurity/AutoBot (migration stage 2c).
# The /blue CLI surfaces expect the sensor compose files at /app/blue-sensor;
# deployments source them from the AutoBot repository checkout (or a future
# autobot distribution) — see examples/local-sensor there. Keep the mount
# point declarative so the compose files can be installed without rebuilding.
COPY examples/README.md /app/blue-sensor/README.md

ENV DECEPTICON_API_URL=http://langgraph:2024
ENV NODE_ENV=production

# No HEALTHCHECK — CLI is an interactive TTY app with no HTTP surface.

# Run as root by operator policy. The CLI shells out to bundled offensive
# tools (apt/curl/wget/git fetches at runtime), so a non-root USER would
# break the agent's ability to install missing utilities on the fly.
# Semgrep ``missing-user-entrypoint`` is explicitly dispositioned here.
USER root

ENTRYPOINT ["node", "dist/index.js"]
