FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ARG NODE_VERSION=24.21.0
ARG NODE_SHA256=fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6
ARG CODEX_VERSION=0.159.3
# npm dist.integrity sha512-hg4nlqCwRb87GocxNnHvz4sadOrdJ71mO0a4CUYWXjAozJe2gtuAdRFldKGSQXw9m7SgR4ydis12KDVVPgzDkQ==
ARG CODEX_TARBALL_SHA256=31d5e584e93e118dd37dbb8bc89ddbbe9998178fc437dc0b9d0f7cc72ef0cf79
# @openai/codex@0.159.3-linux-x64 integrity
# sha512-xlHydfOksnNt/qz4BEVFvjMWunweaVSNtha/DkVUwWZnVZxbqiJwsXSl2iZDk04b6k/g0OI7jU06XqzHB2s5og==
ARG CODEX_LINUX_X64_SHA256=0719027cfe326e248971a2dff0a391c3532a3d838b4e3cc660bf93b2a606d2ce
ARG AGENT_UID=1000
ARG AGENT_GID=1000

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl xz-utils git ripgrep \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSLo /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
 && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
 && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner \
      --exclude='*/CHANGELOG.md' --exclude='*/README.md' --exclude='*/LICENSE' \
 && rm /tmp/node.tar.xz \
 && curl -fsSLo /tmp/codex.tgz "https://registry.npmjs.org/@openai/codex/-/codex-${CODEX_VERSION}.tgz" \
 && echo "${CODEX_TARBALL_SHA256}  /tmp/codex.tgz" | sha256sum -c - \
 && curl -fsSLo /tmp/codex-linux-x64.tgz "https://registry.npmjs.org/@openai/codex/-/codex-${CODEX_VERSION}-linux-x64.tgz" \
 && echo "${CODEX_LINUX_X64_SHA256}  /tmp/codex-linux-x64.tgz" | sha256sum -c - \
 && npm install -g --omit=optional /tmp/codex.tgz \
 && mkdir -p /tmp/codex-linux \
 && tar -xzf /tmp/codex-linux-x64.tgz -C /tmp/codex-linux \
 && cp -a /tmp/codex-linux/package/vendor /usr/local/lib/node_modules/@openai/codex/vendor \
 && npm cache clean --force \
 && rm -rf /root/.npm /tmp/*

COPY agent-requirements.txt /tmp/agent-requirements.txt
RUN pip install --no-cache-dir -r /tmp/agent-requirements.txt \
 && rm -rf /tmp/agent-requirements.txt /root/.cache \
 && echo /workspace > "$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/workspace.pth"

RUN groupadd -g "${AGENT_GID}" agent \
 && useradd -m -u "${AGENT_UID}" -g agent -d /home/agent -s /bin/bash agent \
 && mkdir -p /workspace /home/agent/.codex \
 && chown agent:agent /workspace /home/agent/.codex \
 && git config --system safe.directory '*' \
 && git config --system user.name agent \
 && git config --system user.email agent@localhost \
 && find / -xdev \( -name AGENTS.md -o -name AGENTS.override.md \) -print -delete

USER agent
ENV HOME=/home/agent \
    CODEX_HOME=/home/agent/.codex
WORKDIR /workspace
