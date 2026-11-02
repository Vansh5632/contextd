#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd "${HERE}/../docker" && pwd)"
IMAGE="contextd-c0-agent:v0"
EXPECTED_CODEX="codex-cli 0.159.3"

docker build \
  --build-arg AGENT_UID="$(id -u)" \
  --build-arg AGENT_GID="$(id -g)" \
  -f "${DOCKER_DIR}/agent.Dockerfile" \
  -t "${IMAGE}" \
  "${DOCKER_DIR}"

run() {
  docker run --rm --network none "${IMAGE}" bash -c "$1"
}

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

echo "== codex --version"
ver="$(run 'codex --version')"
echo "${ver}"
[[ "${ver}" == "${EXPECTED_CODEX}" ]] || fail "unexpected codex version: ${ver}"

echo "== codex exec --help"
run 'codex exec --help >/dev/null' || fail "codex exec --help"

echo "== python imports"
run 'python -c "import pytest, anyio, trio, h11, h2, socksio, pytest_httpbin, pytest_trio
from importlib.metadata import version as v
print(*(p + \"==\" + v(p) for p in (\"pytest\", \"anyio\", \"trio\", \"h11\", \"h2\", \"socksio\")))"' \
  || fail "python imports"

echo "== httpcore/httpx must not be installed"
if run 'pip show httpcore' >/dev/null 2>&1; then fail "httpcore is installed"; fi
if run 'pip show httpx' >/dev/null 2>&1; then fail "httpx is installed"; fi
if run 'cd / && python -c "import httpcore"' >/dev/null 2>&1; then fail "httpcore importable without /workspace"; fi

echo "== tools"
run 'which rg git node && rg --version | head -1 && git --version && node --version' || fail "tools"

echo "== no AGENTS.md in image"
found="$(run 'find / -xdev \( -name AGENTS.md -o -name AGENTS.override.md \) 2>/dev/null || true')"
[[ -z "${found}" ]] || fail "AGENTS.md present: ${found}"

echo "== user and workdir"
run '[ "$(id -un)" = agent ] && [ "$HOME" = /home/agent ] && [ "$(pwd)" = /workspace ]' || fail "user/workdir"

echo "== pip cache empty"
run '[ ! -d /root/.cache/pip ] && [ ! -d /home/agent/.cache/pip ]' || fail "pip cache present"

docker image inspect "${IMAGE}" --format 'image {{.Id}}'
echo "SMOKE OK"
