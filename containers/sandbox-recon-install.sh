#!/bin/bash
# Install additional security tools not available in Kali apt repos.
# Run inside the sandbox container build.
set -euo pipefail

echo "[+] Installing Go-based security tools..."

# Install Go (needed for some tools)
apt-get update && apt-get install -y --no-install-recommends golang-go

# interactsh-client — OOB callback validation
go install -v github.com/projectdiscovery/interactsh/cmd/interactsh-client@latest 2>/dev/null || true

# Install Python-based tools
pip3 install --break-system-packages --no-cache-dir \
    "trufflehog>=3.0" \
    "gitleaks>=8.0" 2>/dev/null || \
pip3 install --break-system-packages --no-cache-dir \
    "detect-secrets>=1.4" 2>/dev/null || true

# Install semgrep for SAST
pip3 install --break-system-packages --no-cache-dir \
    "semgrep>=1.50" 2>/dev/null || true

# wafw00f — WAF detection
pip3 install --break-system-packages --no-cache-dir \
    "wafw00f>=2.2" 2>/dev/null || true

# arjun — HTTP parameter discovery
pip3 install --break-system-packages --no-cache-dir \
    "arjun>=2.2" 2>/dev/null || true

# dirsearch — directory bruteforcing
pip3 install --break-system-packages --no-cache-dir \
    "dirsearch>=0.4" 2>/dev/null || true

# Move Go binaries to PATH
if [ -d /root/go/bin ]; then
    cp /root/go/bin/* /usr/local/bin/ 2>/dev/null || true
fi

echo "[+] Additional tools installed."
