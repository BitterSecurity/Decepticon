# Sandbox Isolation

> Why the Kali sandbox runs root with hard capability/mem limits, and
> what the per-engagement container lifecycle looks like.

## TL;DR

The sandbox is the part of the stack that actually touches targets. It
runs root (because raw sockets require it) inside a container with the
minimum capabilities the offensive toolset needs, with `no-new-privileges`,
a 4 GiB memory cap, a 4096-pid cap, and `cap_drop: ALL` then explicit
`cap_add` for the ten capabilities listed in the current Compose service.

The current OSS deployment runs **one shared sandbox container per
host**. The launcher swaps the `/workspace` bind-mount per engagement,
but tmux state, `/tmp`, and `/var/log` are shared across engagements
that run on the same host. The right structural fix is per-engagement
containers spawned by the launcher; this document specifies the design.

## Today: shared sandbox, scoped workspace

```yaml
sandbox:
  cap_drop: [ALL]
  cap_add:
    - NET_RAW          # nmap SYN scans
    - NET_BIND_SERVICE # low ports
    - SYS_PTRACE       # frida / gdb (Reverser)
    - SETUID
    - SETGID
    - CHOWN
    - DAC_OVERRIDE
    - FOWNER
    - KILL
  security_opt:
    - no-new-privileges:true
  mem_limit: 4g
  pids_limit: 4096
  volumes:
    - ${DECEPTICON_ENGAGEMENT_WORKSPACE:-${DECEPTICON_HOME:-~/.decepticon}/workspace}:/workspace
```

What this gets right:

- **Capability surface is small.** A container compromise gives root
  with the ten explicitly added capabilities, not every host capability.
- **No-new-privileges** blocks setuid binaries from escalating *within*
  the container.
- **Memory cap** stops a fuzzer or runaway scan from OOM-killing the
  management plane.
- **Workspace bind-mount** is per-engagement when the launcher is in
  play.

What's still wrong:

- **One process namespace.** Every engagement that runs on this host
  sees every other engagement's still-running background jobs via
  `ps aux`, `bash_status()`, and tmux session enumeration.
- **One filesystem.** `/tmp`, `/var/log`, `/root` are shared. Engagement
  A's dumped hashes are readable by Engagement B's `cat /tmp/*`.
- **One tmux universe.** Background jobs from objective N+1 race against
  objective N+2's polling reads.
- **OPSEC posture transitions leak.** When A switches from `stealth` to
  `loud`, the tmux history that recorded that transition is visible to
  B's tmux history command.

## Proposed target: per-engagement sandbox containers

The following sketch is not an implemented launcher contract. In particular,
`DECEPTICON_PER_ENGAGEMENT_SANDBOX` and `DECEPTICON_SANDBOX_RUNTIME` are design
ideas, not supported configuration flags in current main. The process limit in
the historical sketch is not the current Compose value.

The launcher already chooses the engagement, sets
`DECEPTICON_ENGAGEMENT_WORKSPACE`, and runs `docker compose up`. The
minimum-invasive evolution is one extra layer between "engagement
chosen" and "compose up":

```
launcher.picker.Choose()
    │
    ▼
launcher.sandbox_lifecycle.Acquire(engagement_slug)
    │
    │ checks: container exists for slug?
    │  - if yes: ensure healthy, return SandboxHandle(url, token)
    │  - if no:  docker run, return SandboxHandle(url, token)
    │
    ▼
SANDBOX_URL=http://decepticon-sandbox-<slug>:9999
SANDBOX_TOKEN=<rotated per acquire>
    │
    ▼
launcher.compose.Up(...)
    │
    ▼
agent reaches its own sandbox via the URL+token
```

Per-engagement container properties:

- Container name: `decepticon-sandbox-<engagement-slug>`.
- Per-engagement Docker network: `sandbox-net-<engagement-slug>` so
  engagement A's container cannot reach engagement B's container even
  on the same host.
- Volume: only `<workspace_root>/<engagement-slug>:/workspace` (no
  fallback to the whole tree).
- Env: `SANDBOX_TOKEN=<urandom>` injected per acquire, so even if
  network isolation fails, the daemon refuses calls without the
  per-acquire token.
- Lifecycle: container stays up across the launcher's life and is torn
  down on engagement-close. Snapshot of `/workspace/.sessions/` and
  `.scratch/` is captured to `<workspace_root>/<engagement-slug>/.archive/`
  on teardown.

## Implementation sketch

A new Go package under `clients/launcher/internal/sandbox`:

```go
package sandbox

type Handle struct {
    URL     string
    Token   string
    Slug    string
}

type Lifecycle struct {
    workspaceRoot string
    imageRef      string
    tokens        map[string]string
}

func (l *Lifecycle) Acquire(slug string) (*Handle, error) {
    name := "decepticon-sandbox-" + slug
    netName := "sandbox-net-" + slug

    // 1. Ensure per-engagement network exists.
    if _, err := exec.Command("docker", "network", "inspect", netName).Output(); err != nil {
        exec.Command("docker", "network", "create", "--driver=bridge", netName).Run()
    }

    // 2. Container exists?
    if running, _ := l.containerRunning(name); !running {
        token := generateToken(32)
        l.tokens[slug] = token

        exec.Command("docker", "run", "-d",
            "--name", name,
            "--network", netName,
            "--cap-drop=ALL",
            "--cap-add=NET_RAW",
            "--cap-add=NET_BIND_SERVICE", "--cap-add=SYS_PTRACE",
            "--cap-add=SETUID", "--cap-add=SETGID",
            "--cap-add=CHOWN", "--cap-add=DAC_OVERRIDE",
            "--cap-add=FOWNER", "--cap-add=KILL",
            "--security-opt=no-new-privileges:true",
            "--memory=4g",
            "--pids-limit=1024",
            "-e", "SANDBOX_DAEMON=1",
            "-e", "SANDBOX_TOKEN="+token,
            "-v", filepath.Join(l.workspaceRoot, slug)+":/workspace",
            l.imageRef,
        ).Run()
    }

    return &Handle{
        URL:   "http://" + name + ":9999",
        Token: l.tokens[slug],
        Slug:  slug,
    }, nil
}

func (l *Lifecycle) Release(slug string, archive bool) error {
    name := "decepticon-sandbox-" + slug
    if archive {
        // tar /workspace/.sessions and .scratch to .archive/<ts>.tar.zst
        l.archive(slug)
    }
    exec.Command("docker", "rm", "-f", name).Run()
    exec.Command("docker", "network", "rm", "sandbox-net-"+slug).Run()
    delete(l.tokens, slug)
    return nil
}
```

Wiring: `clients/launcher/cmd/start.go` consults `Lifecycle.Acquire(slug)`
before issuing `docker compose up`, sets `SANDBOX_URL` and
`SANDBOX_TOKEN` for the agent containers, and calls
`Lifecycle.Release(slug, archive=true)` from the engagement-close hook.

The existing `sandbox` service in `docker-compose.yml` stays as the
**dev / single-engagement** entry point. The per-engagement spawner is
opt-in via `DECEPTICON_PER_ENGAGEMENT_SANDBOX=1` so existing dogfood
runs are unchanged.

## Why not Firecracker / gVisor?

Firecracker microVMs would replace Docker containers with hardware-
virtualized VMs (separate kernel per VM, ~125ms boot). That's the right
architecture for multi-tenant deployments where a sandbox-kernel-exploit-to-host
escape is in the threat model. For OSS / single-operator deployments
this document does not establish that the Docker boundary is sufficient for
every threat model. Firecracker requires
KVM (no macOS, no Windows desktop). Multi-tenant deployments need separately qualified isolation.

gVisor is the middle ground: same Docker UX, intercepts every syscall
through a user-space kernel. ~20-30% perf cost. Available as a Docker
runtime (`runsc`). Worth offering as an opt-in
`DECEPTICON_SANDBOX_RUNTIME=runsc` toggle in a follow-up.

## Verifying the hardening

```bash
# Verify cap_drop is in effect
docker exec decepticon-sandbox cat /proc/1/status | grep CapEff
# Expect a tiny capability set, not 0x000001ffffffffff (= all caps)

# Verify no-new-privileges
docker exec decepticon-sandbox cat /proc/1/status | grep NoNewPrivs
# Expect: NoNewPrivs:	1

# Verify mem_limit
docker inspect decepticon-sandbox --format '{{.HostConfig.Memory}}'
# Expect: 4294967296 (4 GiB)

# Verify pids_limit
docker inspect decepticon-sandbox --format '{{.HostConfig.PidsLimit}}'
# Expect: 4096
```

## Future hardening

1. **Per-engagement spawn (above).** This is the next major change.
2. **`DECEPTICON_SANDBOX_RUNTIME=runsc`** opt-in for gVisor.
3. **Firecracker microVM mode** for the multi-tenant pool plane.
4. **Per-engagement Sliver C2** so engagement A's beacons don't share a
   team server with engagement B's. The `c2-sliver` service in compose
   is currently shared; same per-engagement container approach applies.

## References

- [Docker capability model](https://docs.docker.com/engine/security/#linux-kernel-capabilities)
- [`no-new-privileges` flag](https://docs.docker.com/engine/reference/run/#security-configuration)
- [Firecracker](https://firecracker-microvm.github.io/)
- [gVisor](https://gvisor.dev/)
- [`docs/security/neo4j-hardening.md`](./neo4j-hardening.md) - the
  paired control for the management-plane bridge.
- [`docs/security/decepticon-threat-model.md`](./decepticon-threat-model.md) -
  STRIDE walk that ranks per-engagement isolation as the #2 follow-up.
