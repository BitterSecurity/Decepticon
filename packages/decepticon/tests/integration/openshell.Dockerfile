# Reference test workload; production E2B qualification remains separate.
ARG BASE_IMAGE=ghcr.io/bittersecurity/decepticon-sandbox@sha256:cbf20d86e545adc5346462baa31b9acbcf88ab266e91f4ebcd01eafc034b6895
FROM ${BASE_IMAGE}
USER root
COPY packages/decepticon/decepticon/sandbox_kernel /opt/decepticon/sandbox_kernel
COPY packages/decepticon/decepticon/sandbox_server /opt/decepticon/sandbox_server
RUN groupadd --gid 1500 dcptn \
    && useradd --uid 1500 --gid 1500 --create-home --shell /bin/bash dcptn \
    && mkdir -p /workspace \
    && chown dcptn:dcptn /workspace
ENV DECEPTICON_SKIP_BOOT=1 SANDBOX_DAEMON_HOST=127.0.0.1
USER 1500:1500
WORKDIR /workspace
ENTRYPOINT ["python3", "-m", "decepticon.sandbox_server"]
