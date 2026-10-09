# syntax=docker/dockerfile:1
# Havoc C2 Framework — dynamic-spawn workload
# Started via ops_start("c2-havoc") when the agent needs Havoc.
FROM golang:1.22-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    mingw-w64 \
    nasm \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth=1 https://github.com/HavocFramework/Havoc.git /opt/havoc-src

WORKDIR /opt/havoc-src/teamserver
RUN go build -o /opt/havoc-teamserver .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for the runtime stage (Trivy DS-0002).
# The teamserver binary doesn't need root — it binds to high ports
# (40056, 443 via capabilities) and writes to /opt/havoc/data.
RUN groupadd -r havoc && useradd -r -g havoc -d /opt/havoc havoc

COPY --from=builder /opt/havoc-teamserver /opt/havoc/teamserver
COPY containers/c2-havoc-entrypoint.sh /entrypoint.sh
# Strip any CR so the image builds correctly even from a Windows host
# whose checkout introduced CRLF line endings.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

WORKDIR /opt/havoc
ENTRYPOINT ["/entrypoint.sh"]
EXPOSE 40056 443

# Runtime user — security boundary is the container + sandbox-net isolation.
USER havoc

HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=30s \
    CMD curl -fsk https://localhost:40056/ || exit 1
