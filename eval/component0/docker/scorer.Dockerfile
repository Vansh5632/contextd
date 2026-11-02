FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# httpcore 1.0.9 runtime extras and test deps, resolved as of the tag date.
# httpcore itself is never installed; the workspace is put on PYTHONPATH at run time.
COPY scorer-requirements.txt /tmp/scorer-requirements.txt
RUN pip install --no-cache-dir -r /tmp/scorer-requirements.txt \
 && rm -rf /tmp/scorer-requirements.txt /root/.cache \
 && ! python -c "import httpcore" 2>/dev/null \
 && git config --system safe.directory '*'

WORKDIR /work
