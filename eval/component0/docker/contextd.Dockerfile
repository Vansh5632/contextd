# Builder is a pinned Rust toolchain (>= 1.88, the workspace rust-version).
# Runtime matches docker/scorer.Dockerfile's python base so the session's pytest
# install is the same one the offline scorer uses. netcat-openbsd provides `nc -N`,
# which the shell hook needs in order to half-close the socket.
FROM rust:1.88-bookworm@sha256:af306cfa71d987911a781c37b59d7d67d934f49684058f96cf72079c3626bfe0 AS builder

WORKDIR /src
COPY src/ /src/
RUN cargo build --release --locked

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      git \
      netcat-openbsd \
      procps \
      socat \
 && rm -rf /var/lib/apt/lists/*

COPY scorer-requirements.txt /tmp/scorer-requirements.txt
RUN pip install --no-cache-dir -r /tmp/scorer-requirements.txt \
 && rm -rf /tmp/scorer-requirements.txt /root/.cache \
 && ! python -c "import httpcore" 2>/dev/null \
 && git config --system safe.directory '*' \
 && git config --system commit.gpgsign false

COPY --from=builder /src/target/release/contextd /usr/local/bin/contextd

ARG REPLAY_UID=1000
ARG REPLAY_GID=1000
RUN groupadd -g "${REPLAY_GID}" replay \
 && useradd -m -u "${REPLAY_UID}" -g replay -d /home/replay -s /bin/bash replay \
 && mkdir -p /work /data \
 && chown replay:replay /work /data

USER replay
ENV HOME=/home/replay
WORKDIR /work
