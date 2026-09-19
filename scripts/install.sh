#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Decepticon — One-line installer
#
# Usage:
#   curl -fsSL https://decepticon.red/install | bash
#
# Environment variables:
#   CHANNEL              — Update channel (default: stable):
#                            stable — newest final release baked >= SOAK days
#                            latest — newest final release, immediately
#   DECEPTICON_STABLE_SOAK_DAYS — stable soak window (default: 7)
#   VERSION              — Install a specific version (overrides CHANNEL)
#   DECEPTICON_HOME      — Install directory (default: ~/.decepticon)
#   SKIP_PULL            — Skip Docker image pull (default: false)
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────
REPO="BitterSecurity/Decepticon"
BRANCH="${BRANCH:-main}"

# Update channel (Claude-Code-style soak model; both are final-only):
#   stable (default) — newest FINAL release baked >= STABLE_SOAK_DAYS
#   latest           — newest FINAL release, immediately
# Decepticon defaults to stable (conservative for a security tool); the
# channel *semantics* match Claude Code, only the default differs.
# Default + any unrecognized value resolves to stable.
CHANNEL="$(printf '%s' "${CHANNEL:-stable}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
case "$CHANNEL" in
    latest) ;;
    *) CHANNEL="stable" ;;
esac
# Days the stable channel waits before adopting a final release.
STABLE_SOAK_DAYS="${DECEPTICON_STABLE_SOAK_DAYS:-7}"
RAW_BASE="https://raw.githubusercontent.com/$REPO/$BRANCH"
# release asset base — same host every install, used for binary +
# checksum manifests. raw.githubusercontent.com hosts the source-tree
# copy of compose/litellm; their integrity is verified against
# config-checksums.txt fetched from RELEASE_BASE.
RELEASE_BASE="https://github.com/$REPO/releases/download"

# ── Colors ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
DIM='\033[0;2m'
BOLD='\033[1m'
NC='\033[0m'

# ── Helpers ───────────────────────────────────────────────────────
info()    { echo -e "${DIM}$*${NC}"; }
success() { echo -e "${GREEN}$*${NC}"; }
warn()    { echo -e "${YELLOW}$*${NC}"; }
error()   { echo -e "${RED}$*${NC}" >&2; }

# ── Pre-flight checks ────────────────────────────────────────────
preflight() {
    # curl
    if ! command -v curl >/dev/null 2>&1; then
        error "Error: curl is required but not installed."
        exit 1
    fi

    # Container runtime — Docker (preferred) OR Podman 4.4+ (compose v2
    # compatible). Honor explicit DECEPTICON_CONTAINER_RUNTIME override
    # if the user is on a non-default setup (e.g. nerdctl, Rancher Desktop).
    local rt="${DECEPTICON_CONTAINER_RUNTIME:-}"
    if [[ -z "$rt" ]]; then
        if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
            rt="docker"
        elif command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
            rt="podman"
        else
            error "Error: no container runtime found."
            echo -e "${DIM}Install one of:${NC}"
            echo -e "${DIM}  Docker:  ${NC}https://docs.docker.com/get-docker/"
            echo -e "${DIM}  Podman:  ${NC}https://podman.io/docs/installation"
            echo -e "${DIM}  …or set DECEPTICON_CONTAINER_RUNTIME=nerdctl${NC}"
            exit 1
        fi
    elif ! command -v "$rt" >/dev/null 2>&1 || ! "$rt" info >/dev/null 2>&1; then
        error "Error: DECEPTICON_CONTAINER_RUNTIME=$rt requested but unusable."
        exit 1
    fi
    info "Container runtime: $rt"

    # Compose v2 — required for the multi-file `docker compose up --wait`
    # pattern. Docker Desktop ships it; Podman 4.4+ ships `podman compose`
    # built-in (fallback: the podman-compose Python wrapper).
    if [[ "$rt" == "docker" ]]; then
        if ! docker compose version >/dev/null 2>&1; then
            error "Error: Docker Compose v2 is required."
            echo -e "${DIM}Docker Compose is included with Docker Desktop.${NC}"
            echo -e "${DIM}For Linux: ${NC}https://docs.docker.com/compose/install/linux/"
            exit 1
        fi
    elif [[ "$rt" == "podman" ]]; then
        if ! podman compose --help >/dev/null 2>&1 \
            && ! command -v podman-compose >/dev/null 2>&1; then
            error "Error: 'podman compose' (Podman 4.4+) or 'podman-compose' is required."
            echo -e "${DIM}Install: ${NC}https://github.com/containers/podman-compose"
            exit 1
        fi
    fi

    # sha256 tool — required for download integrity verification. Linux
    # ships sha256sum; macOS ships shasum (-a 256). Either is acceptable.
    if ! command -v sha256sum >/dev/null 2>&1 \
        && ! command -v shasum     >/dev/null 2>&1; then
        error "Error: neither sha256sum nor shasum found."
        echo -e "${DIM}Install coreutils (Linux) or ensure /usr/bin/shasum (macOS) is on PATH.${NC}"
        exit 1
    fi
}

# Compute the sha256 of a file using whichever tool is available.
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# Verify a single file against an "<expected_hash>  <ignored_filename>"
# manifest line. Aborts the installer on mismatch.
assert_sha256() {
    local file="$1" expected="$2" label="$3"
    if [[ -z "$expected" ]]; then
        error "Integrity check failed: no checksum recorded for $label."
        error "The release at v${DECEPTICON_VERSION} predates checksum verification."
        error "Either install a newer release (>=1.0.27) or set"
        error "  DECEPTICON_SKIP_VERIFY=1   to explicitly opt out (NOT recommended)."
        [[ "${DECEPTICON_SKIP_VERIFY:-}" == "1" ]] || exit 1
        warn "  → skipping verification because DECEPTICON_SKIP_VERIFY=1."
        return
    fi
    local actual
    actual=$(sha256_of "$file")
    if [[ "$actual" != "$expected" ]]; then
        error "Checksum mismatch for $label — possible tampering or partial download."
        error "  expected: $expected"
        error "  got:      $actual"
        exit 1
    fi
}

# ── Version resolution ───────────────────────────────────────────
# Resolve the newest FINAL release that has baked >= STABLE_SOAK_DAYS.
# Needs python3 for the published_at date filter; prints nothing (caller
# falls back to /releases/latest) when python3 is absent or none qualify.
resolve_stable_soaked() {
    command -v python3 >/dev/null 2>&1 || return 0
    curl -s "https://api.github.com/repos/$REPO/releases?per_page=30" | python3 -c '
import sys, json, datetime
try:
    soak = float(sys.argv[1])
except (IndexError, ValueError):
    soak = 7.0
now = datetime.datetime.now(datetime.timezone.utc)
try:
    rels = json.load(sys.stdin)
except Exception:
    sys.exit(0)
def key(tag):
    base = tag.split("-", 1)[0].split(".")
    out = []
    for i in range(3):
        out.append(int(base[i]) if i < len(base) and base[i].isdigit() else 0)
    return tuple(out)
best = None
for r in rels if isinstance(rels, list) else []:
    if r.get("draft") or r.get("prerelease"):
        continue
    tag = (r.get("tag_name") or "").lstrip("v")
    pub = r.get("published_at")
    if not tag or not pub:
        continue
    try:
        p = datetime.datetime.fromisoformat(pub.replace("Z", "+00:00"))
    except Exception:
        continue
    if (now - p).total_seconds() < soak * 86400:
        continue  # not yet soaked
    if best is None or key(tag) > key(best):
        best = tag
print(best or "")
' "$STABLE_SOAK_DAYS" 2>/dev/null
}

resolve_version() {
    if [[ -n "${VERSION:-}" ]]; then
        DECEPTICON_VERSION="$VERSION"
        return
    fi

    local resolved=""
    if [[ "$CHANNEL" == "stable" ]]; then
        # stable: newest FINAL release baked >= STABLE_SOAK_DAYS.
        info "Fetching stable version (soaked >= ${STABLE_SOAK_DAYS}d)..."
        resolved=$(resolve_stable_soaked)
    fi
    if [[ -z "$resolved" ]]; then
        # latest channel, OR stable with nothing soaked / no python3 —
        # fall back to GitHub "latest" (newest final, excludes pre-releases).
        [[ "$CHANNEL" == "latest" ]] && info "Fetching latest version (newest final)..."
        resolved=$(curl -s "https://api.github.com/repos/$REPO/releases/latest" \
            | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')
    fi

    if [[ -z "$resolved" ]]; then
        # No releases at all — fall back to the moving channel image tag.
        DECEPTICON_VERSION="$CHANNEL"
        info "No releases found, using the :$CHANNEL image tag."
    else
        DECEPTICON_VERSION="$resolved"
        # Pin config downloads to the release tag (not the moving main branch)
        RAW_BASE="https://raw.githubusercontent.com/$REPO/v$DECEPTICON_VERSION"
    fi
}

# ── Download files ────────────────────────────────────────────────
download_files() {
    local install_dir="$1"

    info "Downloading configuration files..."

    # docker-compose.yml (always overwrite — this is infrastructure, not user config)
    curl -fsSL "$RAW_BASE/docker-compose.yml" -o "$install_dir/docker-compose.yml"

    # .env.example — reference template only. Do NOT auto-create .env: the
    # onboard wizard checks for the file's presence to decide whether to run,
    # so a pre-seeded template would silently skip first-time configuration.
    curl -fsSL "$RAW_BASE/.env.example" -o "$install_dir/.env.example"

    # Existing .env (upgrade path) — preserve user's keys, just ensure
    # DECEPTICON_HOME points at the current install dir.
    if [[ -f "$install_dir/.env" ]]; then
        if ! grep -q "^DECEPTICON_HOME=" "$install_dir/.env" 2>/dev/null; then
            echo "DECEPTICON_HOME=$install_dir" >> "$install_dir/.env"
        fi
        # Record the update channel so future `decepticon update` runs and
        # the launch-time self-update track the same stream. An existing
        # value is preserved (the user's prior choice wins).
        if ! grep -q "^DECEPTICON_CHANNEL=" "$install_dir/.env" 2>/dev/null; then
            echo "DECEPTICON_CHANNEL=$CHANNEL" >> "$install_dir/.env"
        fi
        info ".env already exists, preserving your configuration."
    else
        info "No .env yet — run 'decepticon onboard' to create one."
        if [[ "$CHANNEL" == "latest" ]]; then
            info "After onboarding, set DECEPTICON_CHANNEL=latest in .env to keep tracking the latest channel."
        fi
    fi

    # LiteLLM config
    mkdir -p "$install_dir/config"
    curl -fsSL "$RAW_BASE/config/litellm.yaml" -o "$install_dir/config/litellm.yaml"

    # Workspace directory (bind-mounted into containers)
    mkdir -p "$install_dir/workspace"

    # Verify the three files we just wrote against the release-pinned
    # config-checksums.txt manifest before announcing success. raw.* is
    # served from GitHub's CDN; GitHub Releases assets carry the manifest
    # for the same tag. Cross-checking closes the "tampered raw download"
    # vector that previously let an attacker swap docker-compose.yml.
    verify_config_manifest "$install_dir"

    # Version marker
    echo "$DECEPTICON_VERSION" > "$install_dir/.version"
}

# Download config-checksums.txt for the resolved release tag, then verify
# every config file we wrote in download_files. The manifest is in
# sha256sum format ("<hex>  <relative-path>"), produced by the launcher
# release job (.github/workflows/release.yml).
verify_config_manifest() {
    local install_dir="$1"
    if [[ "$DECEPTICON_VERSION" == "latest" || "$DECEPTICON_VERSION" == "stable" ]]; then
        # resolve_version's fallback path. No release tag → no manifest.
        # We already abort the launcher download in that branch, so this
        # path is only hit when someone runs the installer with branch-
        # tracking on a fresh repo; verification stays opt-out.
        warn "No release tag resolved — skipping config manifest verification."
        return
    fi
    local manifest_url="$RELEASE_BASE/v${DECEPTICON_VERSION}/config-checksums.txt"
    local manifest="$install_dir/.config-checksums.txt"
    if ! curl -fsSL "$manifest_url" -o "$manifest" 2>/dev/null; then
        error "Failed to download config-checksums.txt from release v${DECEPTICON_VERSION}."
        error "  url: $manifest_url"
        error "Release predates checksum verification (<1.0.27) — refusing to install."
        error "Either install a newer release, or opt out with DECEPTICON_SKIP_VERIFY=1."
        [[ "${DECEPTICON_SKIP_VERIFY:-}" == "1" ]] || exit 1
        warn "Skipping config manifest verification (DECEPTICON_SKIP_VERIFY=1)."
        return
    fi
    info "Verifying configuration files against release manifest..."
    while IFS=' ' read -r expected _ path; do
        [[ -z "$expected" || -z "$path" ]] && continue
        # path is whatever the release job recorded (e.g. "docker-compose.yml",
        # "config/litellm.yaml", ".env.example"). Resolve relative to install_dir.
        local target="$install_dir/$path"
        if [[ ! -f "$target" ]]; then
            # .env.example may be absent if download_files skipped it; for now
            # we always write it. Surface missing files as a hard error so a
            # silent skip doesn't mask a download failure.
            error "Manifest lists $path but the file is missing under $install_dir."
            exit 1
        fi
        assert_sha256 "$target" "$expected" "$path"
    done < "$manifest"
    rm -f "$manifest"
}

# ── Download launcher binary ─────────────────────────────────────
create_launcher() {
    local bin_dir="$1"

    mkdir -p "$bin_dir"

    # Detect OS and architecture
    local os arch
    os=$(uname -s | tr '[:upper:]' '[:lower:]')
    arch=$(uname -m)
    case "$arch" in
        x86_64)  arch="amd64" ;;
        aarch64) arch="arm64" ;;
        arm64)   arch="arm64" ;;
        *)
            error "Unsupported architecture: $arch"
            exit 1
            ;;
    esac

    local binary_name="decepticon-${os}-${arch}"

    if [[ "$DECEPTICON_VERSION" == "latest" || "$DECEPTICON_VERSION" == "stable" ]]; then
        error "Could not resolve a release version automatically."
        error "Set VERSION explicitly with a tag from:"
        error "  https://github.com/$REPO/releases"
        error "Example: VERSION=<x.y.z> curl -fsSL https://decepticon.red/install | bash"
        exit 1
    fi

    local download_url="$RELEASE_BASE/v${DECEPTICON_VERSION}/${binary_name}"
    info "Downloading launcher binary ($binary_name)..."
    if ! curl -fsSL "$download_url" -o "$bin_dir/decepticon" 2>/dev/null; then
        error "No launcher binary for ${os}/${arch} in v${DECEPTICON_VERSION}."
        error "Supported targets: linux/amd64, linux/arm64, darwin/amd64, darwin/arm64."
        error "If you need another target, please open an issue."
        exit 1
    fi

    # Verify the downloaded binary against the GoReleaser checksums.txt
    # asset for the same tag. The OSS launcher executes as the user's
    # session entry point — pinning its integrity is the highest-value
    # check in the installer.
    verify_launcher_binary "$bin_dir/decepticon" "$binary_name"

    chmod 755 "$bin_dir/decepticon"
}

# Download the GoReleaser-produced checksums.txt, extract the line for
# our binary, and compare. Aborts on mismatch.
verify_launcher_binary() {
    local binary_path="$1" binary_name="$2"
    local checksums_url="$RELEASE_BASE/v${DECEPTICON_VERSION}/checksums.txt"
    local tmp
    tmp=$(mktemp)
    if ! curl -fsSL "$checksums_url" -o "$tmp" 2>/dev/null; then
        rm -f "$tmp"
        error "Failed to download checksums.txt from release v${DECEPTICON_VERSION}."
        error "Release predates checksum verification (<1.0.27) — refusing to install."
        error "Either install a newer release, or opt out with DECEPTICON_SKIP_VERIFY=1."
        [[ "${DECEPTICON_SKIP_VERIFY:-}" == "1" ]] || exit 1
        warn "Skipping launcher binary verification (DECEPTICON_SKIP_VERIFY=1)."
        return
    fi
    local expected
    expected=$(awk -v name="$binary_name" '$2 == name {print $1; exit}' "$tmp")
    rm -f "$tmp"
    assert_sha256 "$binary_path" "$expected" "$binary_name"
}

# ── Detect stale `decepticon` in PATH ─────────────────────────────
# A previous install via `npm link`, manual symlink, or alternate package
# manager can leave a `decepticon` executable elsewhere on PATH. That stale
# entry will shadow our launcher and produce confusing errors (e.g. node
# MODULE_NOT_FOUND). Surface the conflict so the user can clean it up.
detect_stale_launcher() {
    local bin_dir="$1"
    local found=()
    local seen=":"
    local IFS=':'
    for d in $PATH; do
        [[ -z "$d" ]] && continue
        # Dedupe — PATH often lists the same dir twice (.bashrc + .profile etc.)
        case "$seen" in *":$d:"*) continue;; esac
        seen="$seen$d:"
        if [[ -e "$d/decepticon" && "$d" != "$bin_dir" ]]; then
            found+=("$d/decepticon")
        fi
    done

    if [[ ${#found[@]} -gt 0 ]]; then
        echo ""
        warn "Found other 'decepticon' executable(s) on PATH:"
        for f in "${found[@]}"; do
            echo "  $f"
        done
        warn "These may shadow the launcher just installed at $bin_dir/decepticon."
        echo -e "${DIM}Remove them, then run 'hash -r' or restart your shell.${NC}"
    fi
}

# ── PATH setup (bash/zsh/fish) ────────────────────────────────────
setup_path() {
    local bin_dir="$1"
    local path_export="export PATH=\"$bin_dir:\$PATH\""

    # Already in PATH?
    if echo "$PATH" | tr ':' '\n' | grep -qx "$bin_dir"; then
        info "PATH already includes $bin_dir"
        return
    fi

    # GitHub Actions
    if [[ -n "${GITHUB_PATH:-}" ]]; then
        echo "$bin_dir" >> "$GITHUB_PATH"
        return
    fi

    local current_shell
    current_shell=$(basename "${SHELL:-bash}")
    local XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

    case "$current_shell" in
        fish)
            local fish_config="$XDG_CONFIG_HOME/fish/config.fish"
            if [[ -f "$fish_config" ]]; then
                if ! grep -q "$bin_dir" "$fish_config" 2>/dev/null; then
                    echo -e "\n# decepticon" >> "$fish_config"
                    echo "fish_add_path $bin_dir" >> "$fish_config"
                    info "Added to PATH in $fish_config"
                fi
            fi
            ;;
        zsh)
            local zshrc="${ZDOTDIR:-$HOME}/.zshrc"
            if [[ -f "$zshrc" ]] || [[ -w "$(dirname "$zshrc")" ]]; then
                if ! grep -q "$bin_dir" "$zshrc" 2>/dev/null; then
                    echo -e "\n# decepticon" >> "$zshrc"
                    echo "$path_export" >> "$zshrc"
                    info "Added to PATH in $zshrc"
                fi
            fi
            ;;
        *)
            # bash and others
            local bashrc="$HOME/.bashrc"
            local profile="$HOME/.profile"
            local target="$bashrc"
            [[ ! -f "$target" ]] && target="$profile"

            if [[ -f "$target" ]] || [[ -w "$(dirname "$target")" ]]; then
                if ! grep -q "$bin_dir" "$target" 2>/dev/null; then
                    echo -e "\n# decepticon" >> "$target"
                    echo "$path_export" >> "$target"
                    info "Added to PATH in $target"
                fi
            fi
            ;;
    esac
}

# ── Pull Docker images ────────────────────────────────────────────
pull_images() {
    local install_dir="$1"

    if [[ "${SKIP_PULL:-}" == "true" ]]; then
        info "Skipping Docker image pull (SKIP_PULL=true)."
        return
    fi

    echo ""
    info "Pulling Docker images (this may take a few minutes)..."
    # No --env-file: .env doesn't exist yet (onboard hasn't run). All
    # ${VAR} interpolations in docker-compose.yml have :-defaults, and we
    # inject DECEPTICON_VERSION/HOME explicitly so the image tags resolve.
    (cd "$install_dir" && \
        DECEPTICON_VERSION="$DECEPTICON_VERSION" \
        DECEPTICON_HOME="$install_dir" \
        docker compose --profile cli pull) || {
        warn "Warning: Failed to pull some images."
        info "You can pull them manually later: decepticon update"
    }
}

# ── Main ──────────────────────────────────────────────────────────
main() {
    local install_dir="${DECEPTICON_HOME:-$HOME/.decepticon}"
    local bin_dir="$HOME/.local/bin"


    echo ""
    echo -e "${BOLD}Decepticon${NC} — Installer"
    echo ""

    # Pre-flight
    preflight

    # Version
    resolve_version

    mkdir -p "$install_dir"

    info "Installing Decepticon $DECEPTICON_VERSION"
    info "Directory: $install_dir"
    echo ""

    # Download
    download_files "$install_dir"
    success "Configuration files downloaded."

    # Launcher
    create_launcher "$bin_dir"
    success "Launcher installed to $bin_dir/decepticon"

    # PATH
    setup_path "$bin_dir"

    # Stale launcher detection (runs after PATH setup so $bin_dir is the
    # source of truth for "where the new launcher lives")
    detect_stale_launcher "$bin_dir"

    # Docker images
    pull_images "$install_dir"

    # Done
    echo ""
    echo -e "${GREEN}────────────────────────────────────────────${NC}"
    echo -e "${GREEN}  Decepticon installed successfully!${NC}"
    echo -e "${GREEN}────────────────────────────────────────────${NC}"
    echo ""
    echo -e "  ${BOLD}1.${NC} Configure your API keys:"
    echo -e "     ${BOLD}decepticon onboard${NC}"
    echo ""
    echo -e "  ${BOLD}2.${NC} Start Decepticon:"
    echo -e "     ${BOLD}decepticon${NC}"
    echo ""

    # Reload-shell hint — always show it.
    # Two failure modes a user can hit on a fresh shell:
    #   (a) $bin_dir was just added to .bashrc/.zshrc/etc. but the current
    #       shell hasn't sourced it yet → `decepticon` not found.
    #   (b) The shell already has $bin_dir on PATH but a stale `decepticon`
    #       (e.g. removed npm shim) is cached in its hash table → the wrong
    #       binary is invoked or "No such file or directory" is reported.
    # Spelling both fixes out unconditionally is cheaper than diagnosing
    # either failure after the fact.
    echo -e "  ${DIM}Reload your shell to pick up the new launcher:${NC}"
    echo -e "     ${BOLD}exec \$SHELL${NC}     ${DIM}# or open a new terminal${NC}"
    echo -e "  ${DIM}If $bin_dir is already on PATH (e.g. you upgraded), refresh the cache:${NC}"
    echo -e "     ${BOLD}hash -r${NC}"
    echo ""
}

main "$@"
