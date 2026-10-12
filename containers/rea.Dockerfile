FROM node:24.13.0-bookworm-slim@sha256:4660b1ca8b28d6d1906fd644abe34b2ed81d15434d26d845ef0aced307cf4b6f AS node_runtime
FROM eclipse-temurin:25-jdk-jammy@sha256:318f90a80337fb6f26b025dbec0a95fa6f48abf60ce129babf555618356bdbf8 AS jdk

FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55 AS runtime
COPY --from=node_runtime /usr/local /usr/local
COPY --from=jdk /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/java/openjdk/bin:$PATH \
    GHIDRA_INSTALL_DIR=/opt/ghidra \
    PYTHONPATH=/opt \
    REA_ANALYSIS_PROVIDER=ghidra

ARG GHIDRA_URL=https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip
ARG GHIDRA_SHA256=ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl procps python3 python3-pip python3-venv unzip \
    && curl -fsSL "$GHIDRA_URL" -o /tmp/ghidra.zip \
    && echo "$GHIDRA_SHA256  /tmp/ghidra.zip" | sha256sum --check --strict \
    && unzip -q /tmp/ghidra.zip -d /opt \
    && mv /opt/ghidra_12.1.4_PUBLIC /opt/ghidra \
    && python3 -m venv /opt/rea-service-venv \
    && /opt/rea-service-venv/bin/pip install --no-cache-dir 'fastmcp==4.0.11' \
    && mkdir -p /opt/rea /workspace \
    && cd /opt/rea \
    && npm pack rea-agents@6.3.0 --silent \
    && node -e 'const fs=require("node:fs");const crypto=require("node:crypto");const digest=crypto.createHash("sha512").update(fs.readFileSync("rea-agents-6.3.0.tgz")).digest("base64");if(digest!=="1l3L22PYqlzW70I2Z8fVjWufdBp8lN5dwXSiBERLKPP7pybiFSHeCD8LnXfAoilYkmSQe1NfRgOrbIr9ujkOfQ==")process.exit(1)' \
    && npm install --prefix /opt/rea --ignore-scripts --omit=dev /opt/rea/rea-agents-6.3.0.tgz \
    && rm /opt/rea/rea-agents-6.3.0.tgz /tmp/ghidra.zip \
    && test "$(node -p 'require("/opt/rea/node_modules/rea-agents/package.json").version')" = 6.3.0 \
    && test "$(grep '^application.version=' /opt/ghidra/Ghidra/application.properties)" = 'application.version=12.1.4' \
    && rm -rf /var/lib/apt/lists/*

COPY containers/rea_service.py /opt/rea_service.py
RUN chown ubuntu:ubuntu /workspace
USER ubuntu
WORKDIR /workspace
EXPOSE 8082
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8082/health", timeout=3)' || exit 1
CMD ["/opt/rea-service-venv/bin/uvicorn", "rea_service:app", "--host", "0.0.0.0", "--port", "8082"]
