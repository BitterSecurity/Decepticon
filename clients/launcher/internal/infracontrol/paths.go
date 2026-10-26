// Package opscontrol implements the ADR-0006 agent-driven container
// lifecycle daemon and its Backend Protocol.
//
// The daemon is the only process that holds the docker socket; the
// agent calls into it over a Unix-domain socket bind-mounted into
// langgraph (and only langgraph). See docs/adr/0006-agent-driven-container-lifecycle.md.
package infracontrol

import (
	"os"
	"path/filepath"
	"strings"

	"github.com/BitterSecurity/Decepticon/clients/launcher/internal/config"
)

// StackName returns the value of DECEPTICON_STACK_NAME, sanitized to
// match the same `[a-z0-9-]{1,32}` shape we accept in compose object
// names. Empty when the user has not opted into a named stack.
//
// Stack-aware paths let two stacks (e.g., the default install and an
// engagement-isolation dogfood install) coexist on the same host
// without colliding socket/PID files or systemd unit names.
func StackName() string {
	name := strings.TrimSpace(os.Getenv("DECEPTICON_STACK_NAME"))
	if name == "" {
		return ""
	}
	// Conservative: only [a-z0-9-]{1,32}. The compose-side container
	// naming already accepts this, and stricter here means a malformed
	// stack name never lands in a unit file or socket path.
	if len(name) > 32 {
		name = name[:32]
	}
	clean := strings.Builder{}
	for _, r := range name {
		switch {
		case r >= 'a' && r <= 'z':
		case r >= '0' && r <= '9':
		case r == '-':
		default:
			r = '-'
		}
		clean.WriteRune(r)
	}
	return clean.String()
}

// stackSuffix returns ".stack2"-style suffix when DECEPTICON_STACK_NAME
// is set, empty otherwise. Used as a filename infix.
func stackSuffix() string {
	if s := StackName(); s != "" {
		return "." + s
	}
	return ""
}

func runDir() string {
	if path := strings.TrimSpace(os.Getenv("DECEPTICON_OPSCONTROL_RUN_DIR")); filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(config.DecepticonHome(), "run")
}

// RunDirOverride returns the explicit absolute run directory, if any.
func RunDirOverride() string {
	if path := strings.TrimSpace(os.Getenv("DECEPTICON_OPSCONTROL_RUN_DIR")); filepath.IsAbs(path) {
		return path
	}
	return ""
}

// HostSocketPath returns the host-side path of the opscontrol UDS.
// ADR-0006 §1' specifies /var/run/decepticon-ops.sock for the
// container-internal mount; the host path is rooted under
// $DECEPTICON_HOME so rootless / WSL2 / Mac users do not need write
// access to /var/run. Compose maps the host path → the ADR-mandated
// container path.
//
// Stack-scoped form (DECEPTICON_STACK_NAME=stack2):
//
//	$DECEPTICON_HOME/run/ops.stack2.sock
func HostSocketPath() string {
	return filepath.Join(runDir(), "ops"+stackSuffix()+".sock")
}

// ContainerSocketPath is the ADR-0006 §1' mandated path inside the
// langgraph container. The Python OpsControlClient defaults to it.
// This path is stack-agnostic — each stack gets its own langgraph
// container, so the in-container path can stay constant.
const ContainerSocketPath = "/var/run/decepticon-ops.sock"

// PIDFilePath returns the location of the daemon's PID file. Used by
// `decepticon start` to detect whether a daemon is already running
// and by `decepticon stop` to send SIGTERM.
//
// Stack-scoped form: $DECEPTICON_HOME/run/opscontrol.stack2.pid
func PIDFilePath() string {
	return filepath.Join(runDir(), "opscontrol"+stackSuffix()+".pid")
}

// EnsureRunDir creates $DECEPTICON_HOME/run with mode 0700. Idempotent.
// Used by both the launcher (before spawning the daemon) and the
// daemon itself (before binding the socket).
func EnsureRunDir() error {
	return os.MkdirAll(runDir(), 0o700)
}

// ServiceUnitName returns the OS-native service identifier for the
// current stack. systemd uses it as the .service basename; launchd
// uses it as the label. Stack-scoped form: "decepticon-opscontrol-stack2".
func ServiceUnitName() string {
	suffix := StackName()
	if suffix == "" {
		return "decepticon-opscontrol"
	}
	return "decepticon-opscontrol-" + suffix
}

// ComposeProjectEnv is the explicit override env var. Setting it pins
// both the launcher's and the daemon's compose project so dynamic
// workloads spawn INTO that project alongside any services the user
// already manages there (vendor-dev compose stack, plugin-managed
// projects, etc.) rather than into a separate "decepticon" project.
const ComposeProjectEnv = "DECEPTICON_COMPOSE_PROJECT"

// ComposeProjectName returns the docker compose `-p PROJECT` value
// the launcher AND the daemon must both pass on every compose call.
//
// Resolution order:
//
//  1. DECEPTICON_COMPOSE_PROJECT env — explicit user override. Use
//     this to target an existing compose project (e.g. set
//     DECEPTICON_COMPOSE_PROJECT=decepticon-vendor-dev so ops_start("ad")
//     adds bhce to the running vendor-dev stack rather than spinning
//     up its own decepticon-* containers).
//  2. Stack-name fallback — "decepticon[-${DECEPTICON_STACK_NAME}]".
//     Stable, deterministic, never hardcoded into the binary.
//
// The point of having a single helper called from both sides is that
// "container_name:" fields in docker-compose.yml are global to the
// docker daemon — two compose projects competing for the same
// container_name produce "Conflict. The container name
// '/decepticon-…' is already in use". This helper plus
// ComposeCommandEnv guarantee the launcher and the opscontrol daemon
// agree on the same project.
func ComposeProjectName() string {
	if v := strings.TrimSpace(os.Getenv(ComposeProjectEnv)); v != "" {
		return v
	}
	suffix := StackName()
	if suffix == "" {
		return "decepticon"
	}
	return "decepticon-" + suffix
}

// ComposeCommandEnv returns the environment every docker compose call
// should run with. Both the launcher's Compose wrapper and the
// daemon's DockerComposeBackend pass this slice to exec.Cmd so the
// two never disagree on container_name interpolation
// (DECEPTICON_STACK_NAME) or project name (DECEPTICON_COMPOSE_PROJECT).
//
// Why this matters: compose interpolates ${DECEPTICON_STACK_NAME} into
// the container_name field of every service. If the launcher process
// has it unset and the daemon process has it set (via the
// --env-file fallback), the two write DIFFERENT container_name values
// into the SAME compose project. The next `compose up` from either
// side then sees "config drift", marks the existing containers
// "Recreate", and tears them down mid-engagement.
//
// Normalisation:
//
//   - If the variable is missing from the process environment, we
//     append "VAR=" to the returned env. That forces compose to
//     interpolate the empty string (not the --env-file value, not
//     the literal "${VAR}" placeholder).
func ComposeCommandEnv() []string {
	env := os.Environ()
	for _, key := range []string{
		"DECEPTICON_STACK_NAME",
		ComposeProjectEnv,
	} {
		if _, ok := os.LookupEnv(key); !ok {
			env = append(env, key+"=")
		}
	}
	// COMPOSE_PROFILES is special: docker compose treats it as an
	// implicit `--profile` source whose values UNION with explicit
	// `--profile X` flags. ADR-0006 turns specialist workloads into
	// agent-driven spawns, so the only legal source of profile
	// activation is the launcher's `--profile cli` and the daemon's
	// `--profile <workload>`. Force COMPOSE_PROFILES="" in every
	// compose subprocess so a pre-ADR-0006 install's
	// `COMPOSE_PROFILES=c2-sliver` in $DECEPTICON_HOME/.env does not
	// silently bleed into every up/stop/config call -- if it does,
	// compose merges that c2-sliver into the daemon's "ad" call and
	// the launcher's "cli" call alike, the resulting config-hash
	// drifts between them, and ops_start tags every live container
	// "Recreate" mid-engagement.
	//
	// Last-set-wins in child env so this OVERRIDES whatever the
	// parent process inherited from .env or the operator's shell.
	env = append(env, "COMPOSE_PROFILES=")
	// Match --wait-timeout and normalize for the daemon too: different values
	// change the Compose config hash and can recreate live services unexpectedly.
	env = append(env, "DECEPTICON_STARTUP_TIMEOUT_SECONDS="+config.StartupTimeoutSeconds())
	return env
}
