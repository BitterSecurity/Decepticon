#!/bin/bash
# Entrypoint for the Havoc C2 teamserver container.
# Generates a default profile on first run, then starts the teamserver.
set -euo pipefail

# Generate default profile if none provided
if [ ! -f /opt/havoc/profiles/havoc.yaotl ]; then
    mkdir -p /opt/havoc/profiles
    cat > /opt/havoc/profiles/havoc.yaotl <<'EOF'
Teamserver {
    Host = "0.0.0.0"
    Port = 40056

    Build {
        Compiler64 = "/usr/bin/x86_64-w64-mingw32-gcc"
        Compiler86 = "/usr/bin/i686-w64-mingw32-gcc"
        Nasm = "/usr/bin/nasm"
    }
}

Operators {
    user "decepticon" {
        Password = "decepticon-havoc-c2"
    }
}

Listeners {
    Http {
        Name = "decepticon-https"
        Hosts = ["0.0.0.0"]
        HostBind = "0.0.0.0"
        PortBind = 443
        HostRotation = "round-robin"
        Secure = true
    }
}
EOF
    echo "[c2-havoc] Default profile generated → /opt/havoc/profiles/havoc.yaotl"
fi

exec /opt/havoc/teamserver server \
    --profile /opt/havoc/profiles/havoc.yaotl \
    "$@"
