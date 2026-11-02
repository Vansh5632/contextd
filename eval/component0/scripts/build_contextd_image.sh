#!/usr/bin/env bash
# Snapshot the contextd working tree, build contextd-c0-replay:v0, smoke-test the socket.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL="$(cd "${HERE}/.." && pwd)"
REPO="$(cd "${EVAL}/../.." && pwd)"
SNAP="${EVAL}/build/contextd-src"
CTX="${EVAL}/build/contextd-image"
IMAGE="contextd-c0-replay:v0"

rm -rf "${SNAP}" "${CTX}"
mkdir -p "${SNAP}" "${CTX}"

rsync -a \
  --exclude 'target/' \
  --exclude 'eval/' \
  --exclude '.env' \
  --exclude '.cursor/' \
  --exclude '.git/' \
  --exclude 'node_modules/' \
  "${REPO}/" "${SNAP}/"

python3 - "${REPO}" "${SNAP}/PROVENANCE.json" <<'PY'
import hashlib, json, subprocess, sys
repo, dest = sys.argv[1], sys.argv[2]
head = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
diff = subprocess.check_output(["git", "-C", repo, "diff", "HEAD"])
status = subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True)
dirty = []
for line in status.splitlines():
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    dirty.append(path)
doc = {
    "head": head,
    "diff_sha256": hashlib.sha256(diff).hexdigest(),
    "dirty_files": dirty,
}
with open(dest, "w", encoding="utf-8") as handle:
    json.dump(doc, handle, indent=2)
    handle.write("\n")
print(f"provenance head={head} diff_sha256={doc['diff_sha256']} dirty={len(dirty)}")
PY

mkdir -p "${CTX}/src"
rsync -a "${SNAP}/" "${CTX}/src/"
cp "${EVAL}/docker/scorer-requirements.txt" "${CTX}/scorer-requirements.txt"

docker build \
  --build-arg REPLAY_UID="$(id -u)" \
  --build-arg REPLAY_GID="$(id -g)" \
  -f "${EVAL}/docker/contextd.Dockerfile" \
  -t "${IMAGE}" \
  "${CTX}"

echo "== smoke"
docker run --rm -i --network none \
  -e HOME=/tmp/smoke-home \
  -e XDG_CONFIG_HOME=/tmp/smoke-home/.config \
  "${IMAGE}" \
  python - <<'PY'
import os, pathlib, socket, subprocess, time, sys
home = pathlib.Path("/tmp/smoke-home")
cfg = home / ".config" / "contextd"
data = home / "data"
cfg.mkdir(parents=True)
data.mkdir(parents=True)
sock = data / "contextd.sock"
db = data / "events.db"
(cfg / "config.toml").write_text(
    f'db_path = "{db}"\n'
    f'socket_path = "{sock}"\n'
    'ollama_url = "http://127.0.0.1:11434"\n'
    'embedding_model = "nomic-embed-text"\n'
    'watch_root = "/tmp"\n'
)
env = os.environ.copy()
env["HOME"] = str(home)
env["XDG_CONFIG_HOME"] = str(cfg.parent)
proc = subprocess.Popen(
    ["contextd", "run"],
    cwd="/tmp",
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
try:
    deadline = time.time() + 30
    while time.time() < deadline and not sock.exists():
        if proc.poll() is not None:
            out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
            sys.exit(f"daemon exited early:\n{out}")
        time.sleep(0.1)
    if not sock.exists():
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        sys.exit(f"socket missing:\n{out}")
    marker = "echo smoke-marker"
    event = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    event.connect(str(sock))
    body = (
        '{"source":"shell","payload":{"command":"%s","exit_code":0,"cwd":"/tmp"}}\n' % marker
    ).encode()
    event.sendall(body)
    event.shutdown(socket.SHUT_WR)
    event.close()
    time.sleep(0.8)
    query = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    query.settimeout(20)
    query.connect(str(sock))
    query.sendall(b'{"query":"now"}\n')
    query.shutdown(socket.SHUT_WR)
    reply = b""
    while True:
        chunk = query.recv(65536)
        if not chunk:
            break
        reply += chunk
    text = reply.decode("utf-8", "replace")
    if marker not in text:
        sys.exit(f"command missing from reply:\n{text[:2000]}")
    print(text[:400])
finally:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
PY

docker image inspect "${IMAGE}" --format 'image {{.Id}}'
echo "SMOKE OK"
