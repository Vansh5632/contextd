#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EGRESS_DIR="$(cd "${HERE}/../docker/egress" && pwd)"
IMAGE="contextd-c0-agent:v0"
NETWORK="contextd-c0-internal"
PROXY="http://egress:8888"

docker compose -f "${EGRESS_DIR}/compose.yaml" up -d --wait

# CONNECT-only probes: the tunnel is closed before any TLS or HTTP bytes are sent upstream.
docker run --rm -i --network "${NETWORK}" -e HTTPS_PROXY="${PROXY}" "${IMAGE}" python - <<'EOF'
import socket, sys

def connect_status(host, port=443):
    s = socket.create_connection(("egress", 8888), 5)
    s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    line = s.recv(256).split(b"\r\n", 1)[0].decode()
    s.close()
    return line

def direct(host, port):
    try:
        socket.create_connection((host, port), 3).close()
        return "OPEN"
    except OSError as e:
        return f"blocked ({e.__class__.__name__})"

results = {
    "direct 1.1.1.1:80": direct("1.1.1.1", 80),
    "direct 1.1.1.1:443": direct("1.1.1.1", 443),
    "proxy CONNECT api.openai.com:443": connect_status("api.openai.com"),
    "proxy CONNECT example.com:443": connect_status("example.com"),
    "proxy CONNECT chatgpt.com:443": connect_status("chatgpt.com"),
    "proxy CONNECT api.openai.com:80": connect_status("api.openai.com", 80),
}
for k, v in results.items():
    print(f"{k}: {v}")

ok = (
    results["direct 1.1.1.1:80"].startswith("blocked")
    and results["direct 1.1.1.1:443"].startswith("blocked")
    and " 200 " in results["proxy CONNECT api.openai.com:443"] + " "
    and " 403 " in results["proxy CONNECT example.com:443"] + " "
    and " 403 " in results["proxy CONNECT chatgpt.com:443"] + " "
    and " 200 " not in results["proxy CONNECT api.openai.com:80"] + " "
)
print("EGRESS OK" if ok else "EGRESS FAIL")
sys.exit(0 if ok else 1)
EOF
