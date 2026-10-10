"""FastAPI application for the sandbox HTTP daemon.

Wraps a `DaemonSandbox` (the `SandboxBase` subclass that runs every
subprocess without the `docker exec <container>` prefix because the
daemon already lives *inside* the sandbox container) and exposes its
methods over HTTP. The wire surface mirrors the in-process surface 1:1:

  - `/execute`                  → BaseSandbox.execute
  - `/upload_files`             → BaseSandbox.upload_files
  - `/download_files`           → BaseSandbox.download_files
  - `/execute_tmux`             → SandboxBase.execute_tmux
  - `/start_background`         → SandboxBase.start_background
  - `/poll_completion`          → SandboxBase.poll_completion
  - `/kill_session`             → SandboxBase.kill_session
  - `/read_session_log_diff`    → SandboxBase.read_session_log_diff
  - `/reset_session_log_offset` → SandboxBase.reset_session_log_offset
  - `/session_log_path`         → SandboxBase.session_log_path

The agent's tmux session state lives inside this daemon process (via
the shared `TmuxSessionManager._initialized` + `DaemonSandbox._jobs`
class vars). `HTTPSandbox` on the agent side just forwards calls; the
PS1 marker / size watchdog / auto-background semantics all happen here
where they always did, just no longer behind a docker socket.

Layering: the daemon imports only from `decepticon.sandbox_kernel.*` so
the sandbox container image ships zero agent-side transport code
(`backends/` package). See `sandbox_kernel/__init__.py` for the full
layering rationale.

Auth model
----------
`SANDBOX_TOKEN` env: when set, every request must carry
``Authorization: Bearer <token>``. When unset (typical dev), the
daemon answers any caller. Cloud Run multi-container sibling traffic
on loopback isn't routable from outside the service, so the token is
defence-in-depth rather than the primary authn mechanism — but it's
the right thing to ship as the default.
"""

from __future__ import annotations

import base64
import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from decepticon.sandbox_kernel.daemon import DaemonSandbox

log = logging.getLogger("decepticon.sandbox_server")

# ── Wire models. Mirror the in-process types one-to-one so the daemon
# stays a thin transport layer over the canonical DockerSandbox API. ──


class ExecuteRequest(BaseModel):
    command: str
    timeout: int | None = None


class ExecuteResponseModel(BaseModel):
    output: str
    exit_code: int | None = None
    truncated: bool = False


class UploadFileEntry(BaseModel):
    path: str
    data_b64: str = Field(
        description="Base64-encoded file content. JSON wire format keeps "
        "the daemon trivial to debug with curl; the 33% overhead is a non-"
        "issue for plan files / scope artefacts (typically <50 KB).",
    )


class UploadFilesRequest(BaseModel):
    files: list[UploadFileEntry]


class FileResultModel(BaseModel):
    path: str
    error: str | None = None


class UploadFilesResponse(BaseModel):
    files: list[FileResultModel]


class DownloadFilesRequest(BaseModel):
    paths: list[str]


class DownloadResultModel(BaseModel):
    path: str
    data_b64: str | None = None
    error: str | None = None


class DownloadFilesResponse(BaseModel):
    files: list[DownloadResultModel]


# ── tmux / background surface — wraps DockerSandbox.execute_tmux,
# start_background, poll_completion, etc. These are the methods the
# bash tool consumes, so once they're up the LLM-driven bash chain
# transparently works through HTTPSandbox. ───────────────────────────


class ExecuteTmuxRequest(BaseModel):
    command: str = ""
    session: str = "main"
    timeout: int | None = None
    is_input: bool = False
    workspace_path: str | None = None


class ExecuteTmuxResponseModel(BaseModel):
    output: str


class StartBackgroundRequest(BaseModel):
    command: str
    session: str = "main"
    workspace_path: str | None = None


class SessionRequest(BaseModel):
    session: str = "main"
    workspace_path: str | None = None


class BackgroundJobModel(BaseModel):
    session: str
    key: str
    command: str
    initial_markers: int
    started_at: float
    workspace_path: str = "/workspace"
    status: str = "running"
    exit_code: int | None = None
    completed_at: float | None = None
    consumed: bool = False


class PollCompletionResponseModel(BaseModel):
    job: BackgroundJobModel | None = None


class SessionLogDiffResponseModel(BaseModel):
    diff: str


class SessionLogPathResponseModel(BaseModel):
    path: str


class ProvisionEgressRequest(BaseModel):
    policy: dict = Field(
        description="Serialized EgressPolicy "
        "(decepticon.sandbox_kernel.egress.EgressPolicy.to_dict()): the "
        "compiled in-scope / out-of-scope / forbidden destinations.",
    )


class ProvisionEgressResponseModel(BaseModel):
    applied: bool
    enforce: bool
    detail: str
    management_cidrs: list[str]
    resolved_host_ips: list[str]
    dns_allowlist: list[str]


# ── Module-level singletons. These live for the daemon's lifetime; we
# don't recreate the backend per request because reusing the configured
# workspace + tmux session managers is the whole point. ──────────────


_backend: DaemonSandbox | None = None
_required_token: str | None = None


def _get_backend() -> DaemonSandbox:
    global _backend
    if _backend is None:
        workspace_path = os.environ.get("SANDBOX_ROOT_DIR", "/workspace")
        timeout = int(os.environ.get("SANDBOX_DEFAULT_TIMEOUT", "120"))
        # `container_name` is cosmetic for DaemonSandbox — it
        # never invokes `docker exec` — but the value still shows up
        # in tmux session names and `.id`, so make it informative.
        sandbox_name = os.environ.get("SANDBOX_CONTAINER_NAME", "local")
        backend_class = DaemonSandbox
        if os.environ.get("SANDBOX_BOUNDED_FILE_TRANSFERS") == "1":
            from decepticon.sandbox_kernel.scoped_file_transfers import OwnedFileTransferSandbox
            backend_class = OwnedFileTransferSandbox
        _backend = backend_class(
            container_name=sandbox_name,
            default_timeout=timeout,
            workspace_path=workspace_path,
        )
    return _backend


def _verify_token(authorization: str | None) -> None:
    if _required_token is None:
        return
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    if not hmac.compare_digest(authorization[len("Bearer ") :], _required_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
        )


# FastAPI dependency injection wrapper for the auth header. Single
# seam for swapping in OIDC / mTLS later without touching every route.
def auth(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    _verify_token(authorization)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _required_token
    _required_token = os.environ.get("SANDBOX_TOKEN") or None

    # Zombie reaping is delegated to the container init process (tini),
    # enabled via ``init: true`` on the sandbox compose service. tini
    # runs as PID 1 and reaps the tmux server / bash grandchildren that
    # get reparented to it when their tmux parent exits.
    #
    # The daemon deliberately does NOT install a process-wide
    # ``waitpid(-1)`` SIGCHLD handler. Such a handler races with the
    # ``subprocess.run`` calls the daemon uses to run commands: it steals
    # their children, so ``Popen.wait`` hits ECHILD and CPython reports a
    # bogus exit code of 0, clobbering real non-zero exits. Letting
    # ``subprocess.run`` reap its own children — and tini reap the
    # reparented orphans — keeps exit codes correct.

    # Warm the backend so the first agent request doesn't pay the
    # init cost on its critical path.
    backend = _get_backend()
    # Reap tmux sockets/sessions left by a previously-killed daemon
    # process. A SIGKILL leaves them orphaned (their tmux servers
    # survive but no live manager tracks them); without this reap they
    # accumulate forever and can collide with new session names on the
    # next engagement attempt. Scoped to decepticon-named sockets
    # only — see SandboxBase.reap_orphaned_tmux_sessions docstring.
    try:
        reaped = backend.reap_orphaned_tmux_sessions()
        if reaped:
            log.info("startup: reaped %d orphan tmux session(s)", reaped)
    except Exception:
        log.exception("startup: reap_orphaned_tmux_sessions raised")
    yield
    # Shutdown: kill every tmux session we ever handed out so the tmux
    # servers don't outlive the daemon and leave bash zombies behind.
    try:
        backend = _get_backend()
        killed = backend.kill_all_sessions()
        if killed:
            log.info("shutdown: killed %d tmux session(s)", killed)
    except Exception:
        log.exception("shutdown: kill_all_sessions raised")


app = FastAPI(
    title="Decepticon sandbox daemon",
    summary="HTTP transport for the DaemonSandbox surface. Pairs with HTTPSandbox.",
    lifespan=lifespan,
)


# ── Liveness ──────────────────────────────────────────────────────────


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Cheap liveness probe. Cloud Run / Kubernetes startup probes
    should target this — it doesn't touch the backend so it stays
    green even during a long-running command."""
    return {"status": "ok"}


# ── BaseSandbox surface ───────────────────────────────────────────────


@app.post("/execute", response_model=ExecuteResponseModel)
def execute(
    req: ExecuteRequest,
    _: Annotated[None, Depends(auth)],
) -> ExecuteResponseModel:
    backend = _get_backend()
    result = backend.execute(req.command, timeout=req.timeout)
    return ExecuteResponseModel(
        output=result.output,
        exit_code=result.exit_code,
        truncated=result.truncated,
    )


@app.post("/upload_files", response_model=UploadFilesResponse)
def upload_files(
    req: UploadFilesRequest,
    _: Annotated[None, Depends(auth)],
) -> UploadFilesResponse:
    backend = _get_backend()
    decoded = [(f.path, base64.b64decode(f.data_b64)) for f in req.files]
    results = backend.upload_files(decoded)
    return UploadFilesResponse(
        files=[FileResultModel(path=r.path, error=r.error) for r in results],
    )


@app.post("/download_files", response_model=DownloadFilesResponse)
def download_files(
    req: DownloadFilesRequest,
    _: Annotated[None, Depends(auth)],
) -> DownloadFilesResponse:
    backend = _get_backend()
    results = backend.download_files(req.paths)
    return DownloadFilesResponse(
        files=[
            DownloadResultModel(
                path=r.path,
                data_b64=(
                    base64.b64encode(r.content).decode("ascii") if r.content is not None else None
                ),
                error=r.error,
            )
            for r in results
        ],
    )


# ── tmux / background surface ─────────────────────────────────────────


@app.post("/execute_tmux", response_model=ExecuteTmuxResponseModel)
def execute_tmux(
    req: ExecuteTmuxRequest,
    _: Annotated[None, Depends(auth)],
) -> ExecuteTmuxResponseModel:
    backend = _get_backend()
    output = backend.execute_tmux(
        command=req.command,
        session=req.session,
        timeout=req.timeout,
        is_input=req.is_input,
        workspace_path=req.workspace_path,
    )
    return ExecuteTmuxResponseModel(output=output)


@app.post("/start_background", response_model=dict)
def start_background(
    req: StartBackgroundRequest,
    _: Annotated[None, Depends(auth)],
) -> dict:
    backend = _get_backend()
    backend.start_background(
        command=req.command,
        session=req.session,
        workspace_path=req.workspace_path,
    )
    return {"status": "ok"}


@app.post("/poll_completion", response_model=PollCompletionResponseModel)
def poll_completion(
    req: SessionRequest,
    _: Annotated[None, Depends(auth)],
) -> PollCompletionResponseModel:
    backend = _get_backend()
    job = backend.poll_completion(
        session=req.session,
        workspace_path=req.workspace_path,
    )
    if job is None:
        return PollCompletionResponseModel(job=None)
    # Field-by-field copy rather than asdict so a future BackgroundJob
    # extension doesn't silently leak fields the wire shape isn't
    # ready for.
    return PollCompletionResponseModel(
        job=BackgroundJobModel(
            session=job.session,
            key=job.key,
            command=job.command,
            initial_markers=job.initial_markers,
            started_at=job.started_at,
            workspace_path=job.workspace_path,
            status=job.status,
            exit_code=job.exit_code,
            completed_at=job.completed_at,
            consumed=job.consumed,
        ),
    )


@app.post("/kill_session", response_model=dict)
def kill_session(
    req: SessionRequest,
    _: Annotated[None, Depends(auth)],
) -> dict:
    backend = _get_backend()
    backend.kill_session(
        session=req.session,
        workspace_path=req.workspace_path,
    )
    return {"status": "ok"}


@app.post("/read_session_log_diff", response_model=SessionLogDiffResponseModel)
def read_session_log_diff(
    req: SessionRequest,
    _: Annotated[None, Depends(auth)],
) -> SessionLogDiffResponseModel:
    backend = _get_backend()
    diff = backend.read_session_log_diff(
        session=req.session,
        workspace_path=req.workspace_path,
    )
    return SessionLogDiffResponseModel(diff=diff)


@app.post("/reset_session_log_offset", response_model=dict)
def reset_session_log_offset(
    req: SessionRequest,
    _: Annotated[None, Depends(auth)],
) -> dict:
    backend = _get_backend()
    backend.reset_session_log_offset(
        session=req.session,
        workspace_path=req.workspace_path,
    )
    return {"status": "ok"}


def _egress_enforcement_enabled() -> bool:
    """Whether the sandbox applies egress rules — default ON, opt-out.

    Decided sandbox-side (its own env, not trusted from the wire). The
    egress firewall loads by default for any enforcing policy; an operator
    whose environment it doesn't fit sets ``DECEPTICON_EGRESS_DISABLE=1``
    on the sandbox container to fall back to the parser layer alone. The
    management plane is discovered locally, so default-on can't sever the
    agent↔management (Neo4j / daemon) link.
    """
    return os.environ.get("DECEPTICON_EGRESS_DISABLE", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }


@app.post("/provision_egress", response_model=ProvisionEgressResponseModel)
def provision_egress(
    req: ProvisionEgressRequest,
    _: Annotated[None, Depends(auth)],
) -> ProvisionEgressResponseModel:
    """Apply the Layer-2 RoE egress guardrail (nftables connect-allowlist).

    Reconstructs the agent-compiled ``EgressPolicy``, folds in the
    locally-discovered management plane + resolver + resolved host IPs,
    and loads the ruleset via ``nft``. Default ON for an enforcing
    policy; an operator can opt out with ``DECEPTICON_EGRESS_DISABLE``,
    in which case it returns the rendered ruleset for inspection without
    touching the network.
    """
    from decepticon.sandbox_kernel.egress import EgressPolicy, apply_egress

    policy = EgressPolicy.from_dict(req.policy)
    result = apply_egress(policy, enabled=_egress_enforcement_enabled())
    return ProvisionEgressResponseModel(
        applied=result.applied,
        enforce=result.enforce,
        detail=result.detail,
        management_cidrs=list(result.management_cidrs),
        resolved_host_ips=list(result.resolved_host_ips),
        dns_allowlist=list(result.dns_allowlist),
    )


@app.post("/session_log_path", response_model=SessionLogPathResponseModel)
def session_log_path(
    req: SessionRequest,
    _: Annotated[None, Depends(auth)],
) -> SessionLogPathResponseModel:
    backend = _get_backend()
    path = backend.session_log_path(
        session=req.session,
        workspace_path=req.workspace_path,
    )
    return SessionLogPathResponseModel(path=path)
