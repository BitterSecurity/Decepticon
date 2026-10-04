# syntax=docker/dockerfile:1
# Skillogy server image (Phase 1a, Amendment v0.2.2).
#
# The skill catalog is stored in Neo4j and queried over REST. This image
# carries no langchain/langgraph/sandbox dependencies and no agent
# runtime — just FastAPI + uvicorn + the Neo4j driver + httpx (the
# ADR-0011 embedding client that talks to the litellm proxy) + the
# skillogy server module + the CI-built ``skills.cypher`` dump that
# gets reconciled with persistent Neo4j on boot.

FROM python:3.13-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Server code. The decepticon namespace package is materialized at the
# top level so ``python -m decepticon.skillogy`` works without the rest
# of the decepticon framework being present.
COPY packages/decepticon/decepticon/skillogy ./decepticon/skillogy
COPY packages/decepticon/decepticon/skill_audit ./decepticon/skill_audit

# CI-built graph dump. A digest marker prevents unchanged releases from
# replaying thousands of MERGEs while persistent installs receive updates.
COPY packages/decepticon/decepticon/skills/.graph/skills.cypher /app/skills.cypher

RUN touch ./decepticon/__init__.py

RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.30.0" \
    "pydantic>=2.0.0" \
    "neo4j>=5.24" \
    "httpx>=0.27.0"

RUN groupadd -r skillogy && useradd -r -g skillogy -d /app -s /sbin/nologin skillogy \
    && chown -R skillogy:skillogy /app

USER skillogy

# Bind / port
ENV SKILLOGY_REST_PORT=9100

# Neo4j connection — overridable per environment. The decepticon-net
# compose hostname is ``neo4j``; standalone tests may point this at
# ``bolt://localhost:7687``.
ENV SKILLOGY_NEO4J_URI=bolt://neo4j:7687
ENV SKILLOGY_NEO4J_USER=neo4j

# Baked cypher dump used by the release reconciler.
ENV SKILLOGY_CYPHER_PATH=/app/skills.cypher

EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9100/v1/health')" || exit 1

CMD ["python", "-m", "decepticon.skillogy"]
