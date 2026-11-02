#!/usr/bin/env bash
# One-time networked pull of nomic-embed-text into build/ollama.
# The image reference is harness.images.OLLAMA_IMAGE (pinned by digest).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL="$(cd "${HERE}/.." && pwd)"
VOL="${EVAL}/build/ollama"
NAME="contextd-c0-ollama-fetch"
mkdir -p "${VOL}"

OLLAMA_IMAGE="$(
  cd "${EVAL}" && env -u PYTHONPATH .venv/bin/python -c 'from harness.images import OLLAMA_IMAGE; print(OLLAMA_IMAGE)'
)"
if [[ "${OLLAMA_IMAGE}" == *REPLACE* ]]; then
  echo "OLLAMA_IMAGE is not pinned yet" >&2
  exit 1
fi

docker pull "${OLLAMA_IMAGE}"

docker rm -f "${NAME}" >/dev/null 2>&1 || true
docker run -d --name "${NAME}" -v "${VOL}:/root/.ollama" "${OLLAMA_IMAGE}"

ready=0
for _ in $(seq 1 90); do
  if docker exec "${NAME}" ollama list >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" != 1 ]]; then
  docker logs "${NAME}" >&2 || true
  docker rm -f "${NAME}" >/dev/null 2>&1 || true
  echo "ollama did not become ready" >&2
  exit 1
fi

docker exec "${NAME}" ollama pull nomic-embed-text
docker exec "${NAME}" ollama show nomic-embed-text | tee "${VOL}/nomic-embed-text.show.txt"
docker exec "${NAME}" ollama list | tee "${VOL}/nomic-embed-text.list.txt"

python3 - "${VOL}" <<'PY'
import hashlib, pathlib, re, sys
vol = pathlib.Path(sys.argv[1])
show = (vol / "nomic-embed-text.show.txt").read_text(encoding="utf-8")
listing = (vol / "nomic-embed-text.list.txt").read_text(encoding="utf-8")
match = re.search(r"(?i)digest[:\s]+(sha256:[0-9a-f]{64}|[0-9a-f]{64})", show)
if match:
    digest = match.group(1)
    if not digest.startswith("sha256:"):
        digest = "sha256:" + digest
else:
    # This ollama build prints a short id in `ollama list` and omits Digest
    # from `ollama show`. The id is the sha256 of the manifest.
    short = re.search(r"nomic-embed-text\S*\s+([0-9a-f]{12})\b", listing)
    if not short:
        raise SystemExit("could not parse model id from ollama list:\n" + listing)
    manifest = vol / "models/manifests/registry.ollama.ai/library/nomic-embed-text/latest"
    digest = "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest()
    if not digest.startswith("sha256:" + short.group(1)):
        raise SystemExit(f"manifest {digest} does not match ollama list id {short.group(1)}")
(vol / "nomic-embed-text.digest").write_text(digest + "\n", encoding="utf-8")
print(digest)
PY

docker rm -f "${NAME}" >/dev/null 2>&1 || true
echo "MODEL OK"
