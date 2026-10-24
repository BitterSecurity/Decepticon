# ----------------------
# build-base — Node 22 LTS + native build toolchain.
# Used only by stages that compile native addons (node-pty, sharp).
# ----------------------
FROM node:22-slim AS build-base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl \
    ca-certificates \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# ----------------------
# deps — install full workspace dependencies (incl. devDependencies for build).
# Layer busted only when package.json or package-lock.json change.
# ----------------------
FROM build-base AS deps

WORKDIR /app

COPY package.json package-lock.json ./
COPY clients/cli/package.json clients/cli/
COPY clients/web/package.json clients/web/
COPY clients/shared/streaming/package.json clients/shared/streaming/

RUN npm ci --no-audit --no-fund

# ----------------------
# build — prisma generate + next build.
#
# Layer strategy (ordered by change frequency, rarest first):
#   1. node_modules (busted only by package.json changes — deps stage)
#   2. shared libs   (changes rarely)
#   3. cli source    (changes occasionally)
#   4. web source    (changes most often — last COPY before build)
# ----------------------
FROM build-base AS build

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY package.json package-lock.json ./

# Rarely-changing layers first
COPY clients/shared ./clients/shared
COPY clients/cli ./clients/cli

# Web source last — most frequent changes only bust from here
COPY clients/web ./clients/web

# Stamp the package version from the git tag at build time.
ARG VERSION=0.0.0
RUN sed -i 's/"version": "[^"]*"/"version": "'"$VERSION"'"/' clients/web/package.json && \
    sed -i 's/"version": "[^"]*"/"version": "'"$VERSION"'"/' clients/cli/package.json

WORKDIR /app/clients/web

# Build the shared streaming workspace first — its package.json main
# points at dist/index.js, so the Next build's import resolution will
# fail with "Module not found: @decepticon/streaming" otherwise.
WORKDIR /app
RUN npm run build --workspace=@decepticon/streaming
WORKDIR /app/clients/web

RUN npx prisma generate
RUN npm run build

# ----------------------
# runtime — minimal node:22-slim, NO build toolchain.
#
# Layer strategy: node_modules is by far the largest COPY (~1.7GB).
# It's placed early so source-only rebuilds reuse it from cache.
# ----------------------
FROM node:22-slim AS runner

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
ENV TERMINAL_PORT=3003

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Heaviest layer first — cached unless deps change
COPY --from=build --chown=nextjs:nodejs /app/node_modules ./node_modules

# Prisma schema + migrations (rarely changes)
COPY --from=build --chown=nextjs:nodejs /app/clients/web/prisma ./clients/web/prisma
COPY --from=build --chown=nextjs:nodejs /app/clients/web/prisma.config.ts ./clients/web/prisma.config.ts

# Shared streaming library
COPY --from=build --chown=nextjs:nodejs /app/clients/shared ./clients/shared

# CLI source (spawned by terminal server via PTY)
COPY --from=build --chown=nextjs:nodejs /app/clients/cli/src ./clients/cli/src
COPY --from=build --chown=nextjs:nodejs /app/clients/cli/package.json ./clients/cli/package.json

# Public assets (changes rarely)
COPY --from=build --chown=nextjs:nodejs /app/clients/web/public ./clients/web/public

# Terminal WebSocket server (changes occasionally)
COPY --from=build --chown=nextjs:nodejs /app/clients/web/server ./clients/web/server

# Standalone Next.js server + static — changes every build but layer is small (~80MB)
COPY --from=build --chown=nextjs:nodejs /app/clients/web/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/clients/web/.next/static ./clients/web/.next/static

WORKDIR /app/clients/web

COPY --chmod=755 containers/web-entrypoint.sh /web-entrypoint.sh
# Strip any CR so the image builds correctly even from a Windows host
# whose checkout introduced CRLF line endings.
RUN sed -i 's/\r$//' /web-entrypoint.sh

# System users have /nonexistent as their home on Debian. Store web CLI drafts
# and observation cursors in a private, non-root-writable volume instead.
ENV DECEPTICON_WEB_STATE_DIR=/var/lib/decepticon-web
RUN install -d -m 0700 -o nextjs -g nodejs /var/lib/decepticon-web

USER nextjs

# Check the actual runtime identity, not a root build-stage permission check.
RUN node -e 'const fs=require("fs"),path=require("path");if(process.getuid()===0)throw Error("Web runtime must be non-root");const dir=fs.mkdtempSync(path.join(process.env.DECEPTICON_WEB_STATE_DIR,"permission-check-"));fs.writeFileSync(path.join(dir,"state.json"),"{}",{mode:0o600});fs.rmSync(dir,{recursive:true});'

EXPOSE 3000 3003

CMD ["/web-entrypoint.sh"]
