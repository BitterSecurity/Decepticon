# ─────────────────────────────────────────────────────────────────────
# Decepticon — Windows installer (PowerShell)
#
# The Windows-native counterpart of scripts/install.sh. Windows 10/11 ship
# Windows PowerShell 5.1; this script also runs unchanged under PowerShell 7+.
#
# Usage:
#   irm https://decepticon.red/install.ps1 | iex
#
# Environment variables:
#   DECEPTICON_VERSION   — install a specific version (default: latest)
#   DECEPTICON_HOME      — install directory (default: %USERPROFILE%\.decepticon)
#   SKIP_PULL            — set to "true" to skip the Docker image pull
#   DECEPTICON_SKIP_VERIFY — set to "1" to opt out of checksum verification
# ─────────────────────────────────────────────────────────────────────

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repo        = 'BitterSecurity/Decepticon'
$Branch      = if ($env:BRANCH) { $env:BRANCH } else { 'main' }
$RawBase     = "https://raw.githubusercontent.com/$Repo/$Branch"
$ReleaseBase = "https://github.com/$Repo/releases/download"

function Write-Info    { param($m) Write-Host $m -ForegroundColor DarkGray }
function Write-Success { param($m) Write-Host $m -ForegroundColor Green }
function Write-Warn    { param($m) Write-Host $m -ForegroundColor Yellow }
function Write-Err     { param($m) Write-Host $m -ForegroundColor Red }

function Test-Command { param($Name) [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

# ── Pre-flight checks ────────────────────────────────────────────────
function Invoke-Preflight {
    if (-not (Test-Command 'docker')) {
        Write-Err 'Error: Docker is required but not installed.'
        Write-Info 'Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/'
        exit 1
    }
    try { docker info *> $null } catch {
        Write-Err 'Error: Docker daemon is not running.'
        Write-Info 'Start Docker Desktop and re-run the installer.'
        exit 1
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Err 'Error: Docker daemon is not running. Start Docker Desktop and re-run.'
        exit 1
    }
    try { docker compose version *> $null } catch {
        Write-Err 'Error: Docker Compose v2 is required (bundled with Docker Desktop).'
        exit 1
    }
}

# ── Architecture detection ───────────────────────────────────────────
function Get-Arch {
    $a = $env:PROCESSOR_ARCHITECTURE
    switch ($a) {
        'AMD64' { return 'amd64' }
        'ARM64' { return 'arm64' }
        'x86'   { Write-Err '32-bit Windows is not supported.'; exit 1 }
        default { Write-Err "Unsupported architecture: $a"; exit 1 }
    }
}

# ── Version resolution ───────────────────────────────────────────────
function Resolve-Version {
    if ($env:DECEPTICON_VERSION) { return $env:DECEPTICON_VERSION }
    Write-Info 'Fetching latest version...'
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
            -Headers @{ 'User-Agent' = 'decepticon-installer' }
        return ($rel.tag_name -replace '^v', '')
    } catch {
        Write-Err 'Could not resolve a release version automatically.'
        Write-Err "Set DECEPTICON_VERSION explicitly from https://github.com/$Repo/releases"
        exit 1
    }
}

# ── SHA-256 helpers ──────────────────────────────────────────────────
function Get-Sha256 { param($Path) (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower() }

function Assert-Sha256 {
    param($Path, $Expected, $Label)
    if (-not $Expected) {
        Write-Err "Integrity check failed: no checksum recorded for $Label."
        if ($env:DECEPTICON_SKIP_VERIFY -eq '1') {
            Write-Warn '  -> skipping verification (DECEPTICON_SKIP_VERIFY=1).'
            return
        }
        exit 1
    }
    $actual = Get-Sha256 $Path
    if ($actual -ne $Expected.ToLower()) {
        Write-Err "Checksum mismatch for $Label - possible tampering or partial download."
        Write-Err "  expected: $Expected"
        Write-Err "  got:      $actual"
        exit 1
    }
}

# ── Main ─────────────────────────────────────────────────────────────
function Main {
    Write-Host ''
    Write-Host 'Decepticon' -ForegroundColor White -NoNewline
    Write-Host ' - Windows Installer'
    Write-Host ''

    Invoke-Preflight

    $version    = Resolve-Version
    $arch       = Get-Arch
    $installDir = if ($env:DECEPTICON_HOME) { $env:DECEPTICON_HOME } else { Join-Path $env:USERPROFILE '.decepticon' }
    $binDir     = Join-Path $installDir 'bin'

    Write-Info "Installing Decepticon $version  (windows/$arch)"
    Write-Info "Directory: $installDir"
    Write-Host ''

    New-Item -ItemType Directory -Force -Path $installDir, $binDir, (Join-Path $installDir 'config'), (Join-Path $installDir 'workspace') | Out-Null

    # ── Config files (pinned to the release tag) ─────────────────────
    $rawBase = "https://raw.githubusercontent.com/$Repo/v$version"
    Write-Info 'Downloading configuration files...'
    Invoke-WebRequest -Uri "$rawBase/docker-compose.yml" -OutFile (Join-Path $installDir 'docker-compose.yml')
    Invoke-WebRequest -Uri "$rawBase/.env.example"       -OutFile (Join-Path $installDir '.env.example')
    Invoke-WebRequest -Uri "$rawBase/config/litellm.yaml" -OutFile (Join-Path $installDir 'config\litellm.yaml')

    # Verify config files against the release-pinned manifest.
    if ($env:DECEPTICON_SKIP_VERIFY -ne '1') {
        $manifestPath = Join-Path $installDir '.config-checksums.txt'
        try {
            Invoke-WebRequest -Uri "$ReleaseBase/v$version/config-checksums.txt" -OutFile $manifestPath
        } catch {
            Write-Err "Failed to download config-checksums.txt for v$version (pre-1.0.27 release?)."
            Write-Err 'Install a newer release or set DECEPTICON_SKIP_VERIFY=1 to opt out.'
            exit 1
        }
        Write-Info 'Verifying configuration files against release manifest...'
        foreach ($line in Get-Content $manifestPath) {
            $parts = $line -split '\s+', 2 | Where-Object { $_ }
            if ($parts.Count -ne 2) { continue }
            $target = Join-Path $installDir ($parts[1].Trim())
            if (Test-Path $target) { Assert-Sha256 $target $parts[0].Trim() $parts[1].Trim() }
        }
        Remove-Item $manifestPath -ErrorAction SilentlyContinue
    }
    Set-Content -Path (Join-Path $installDir '.version') -Value $version
    Write-Success 'Configuration files downloaded.'

    # ── Launcher binary ──────────────────────────────────────────────
    $binaryName = "decepticon-windows-$arch.exe"
    $binaryPath = Join-Path $binDir 'decepticon.exe'
    Write-Info "Downloading launcher binary ($binaryName)..."
    try {
        Invoke-WebRequest -Uri "$ReleaseBase/v$version/$binaryName" -OutFile $binaryPath
    } catch {
        Write-Err "No launcher binary for windows/$arch in v$version."
        exit 1
    }
    if ($env:DECEPTICON_SKIP_VERIFY -ne '1') {
        $sumsPath = Join-Path $env:TEMP 'decepticon-checksums.txt'
        try {
            Invoke-WebRequest -Uri "$ReleaseBase/v$version/checksums.txt" -OutFile $sumsPath
            $expected = $null
            foreach ($line in Get-Content $sumsPath) {
                $p = $line -split '\s+', 2 | Where-Object { $_ }
                if ($p.Count -eq 2 -and $p[1].Trim() -eq $binaryName) { $expected = $p[0].Trim() }
            }
            Remove-Item $sumsPath -ErrorAction SilentlyContinue
            Assert-Sha256 $binaryPath $expected $binaryName
        } catch {
            Write-Err 'Failed to verify launcher binary; set DECEPTICON_SKIP_VERIFY=1 to opt out.'
            exit 1
        }
    }
    Write-Success "Launcher installed to $binaryPath"

    # ── PATH (user scope) ────────────────────────────────────────────
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($userPath -split ';') -notcontains $binDir) {
        [Environment]::SetEnvironmentVariable('Path', "$userPath;$binDir", 'User')
        $env:Path = "$env:Path;$binDir"
        Write-Info "Added $binDir to your user PATH."
    } else {
        Write-Info "PATH already includes $binDir"
    }

    # ── Docker images ────────────────────────────────────────────────
    if ($env:SKIP_PULL -ne 'true') {
        Write-Host ''
        Write-Info 'Pulling Docker images (this may take a few minutes)...'
        Push-Location $installDir
        $env:DECEPTICON_VERSION = $version
        $env:DECEPTICON_HOME    = $installDir
        try { docker compose --profile cli pull } catch { Write-Warn 'Warning: failed to pull some images - run "decepticon update" later.' }
        Pop-Location
    }

    Write-Host ''
    Write-Success '─────────────────────────────────────────────'
    Write-Success '  Decepticon installed successfully!'
    Write-Success '─────────────────────────────────────────────'
    Write-Host ''
    Write-Host '  1. Configure your API keys:  ' -NoNewline; Write-Host 'decepticon onboard' -ForegroundColor White
    Write-Host '  2. Start Decepticon:         ' -NoNewline; Write-Host 'decepticon' -ForegroundColor White
    Write-Host ''
    Write-Info '  Open a new terminal so the updated PATH takes effect.'
    Write-Host ''
}

Main
