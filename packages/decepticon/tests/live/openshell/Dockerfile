# Reference test workload; production E2B qualification remains separate.
ARG BASE_IMAGE=ghcr.io/bittersecurity/decepticon-sandbox@sha256:cbf20d86e545adc5346462baa31b9acbcf88ab266e91f4ebcd01eafc034b6895
FROM ${BASE_IMAGE}
USER root
COPY packages/decepticon/decepticon_sandbox /usr/local/lib/decepticon-sandbox/decepticon_sandbox
RUN python3 -c "import pathlib, sysconfig; pathlib.Path(sysconfig.get_paths()['purelib'], 'decepticon-sandbox.pth').write_text('/usr/local/lib/decepticon-sandbox\\n')"
RUN groupadd --gid 1500 dcptn \
    && useradd --uid 1500 --gid 1500 --create-home --shell /bin/bash dcptn \
    && mkdir -p /workspace \
    && chown dcptn:dcptn /workspace
ENV SANDBOX_DAEMON_HOST=127.0.0.1
USER 1500:1500
WORKDIR /workspace
ENTRYPOINT ["python3", "-m", "decepticon_sandbox.server"]
