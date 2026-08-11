# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM --platform=$TARGETPLATFORM node:22.22.1-bookworm-slim@sha256:4f77a690f2f8946ab16fe1e791a3ac0667ae1c3575c3e4d0d4589e9ed5bfaf3d AS node_runtime

FROM --platform=$TARGETPLATFORM ubuntu:22.04@sha256:3b06811b2afd352be909dd088a004166d665dc76d38b13eada33522a9d915c6f AS runtime

ARG TARGETPLATFORM
ARG BUILD_DATE=""
ARG VCS_REF=""
ARG VERSION="0.1.0-rc.1"
ARG SOURCE_URL=""
LABEL org.opencontainers.image.title="JarvisBench OpenClaw Runtime" \
      org.opencontainers.image.description="Pinned OpenClaw runtime for JarvisBench" \
      org.opencontainers.image.created="$BUILD_DATE" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.source="$SOURCE_URL"
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/jarvisbench/src:/opt/jarvisbench \
    PATH=/opt/jarvisbench-runtime/venv/bin:$PATH \
    JARVISBENCH_ROOT=/opt/jarvisbench \
    JARVISBENCH_APT_SNAPSHOT=20260810T000000Z \
    OPENCLAW_HOME=/var/lib/jarvisbench \
    OPENCLAW_STATE_DIR=/var/lib/jarvisbench/openclaw \
    OPENCLAW_CONFIG_PATH=/var/lib/jarvisbench/openclaw/openclaw.json

COPY --from=node_runtime /usr/local/ /usr/local/
COPY runtime/docker/ubuntu-snapshot.sources /etc/apt/sources.list.d/ubuntu.sources
COPY runtime/docker/99snapshot /etc/apt/apt.conf.d/99snapshot
COPY runtime/docker/98snapshot-bootstrap-tls /etc/apt/apt.conf.d/98snapshot-bootstrap-tls
COPY runtime/docker/python-requirements.lock /opt/jarvisbench-runtime/python-requirements.lock
RUN rm -f /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -f /etc/apt/apt.conf.d/98snapshot-bootstrap-tls \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       git pandoc poppler-utils=22.02.0-2ubuntu0.13 python3 python3-venv=3.10.6-1~22.04.1 sqlite3 tini wkhtmltopdf \
    && python3 -m venv /opt/jarvisbench-runtime/venv \
    && /opt/jarvisbench-runtime/venv/bin/python -m pip install \
       --no-cache-dir \
       --require-hashes \
       --only-binary=:all: \
       --requirement /opt/jarvisbench-runtime/python-requirements.lock \
    && rm -rf /var/lib/apt/lists/*

COPY runtime/npm/package.json runtime/npm/package-lock.json /opt/openclaw-runtime/
RUN cd /opt/openclaw-runtime \
    && npm ci --omit=dev --ignore-scripts \
    && ln -s /opt/openclaw-runtime/node_modules/.bin/openclaw /usr/local/bin/openclaw \
    && npm cache clean --force

WORKDIR /opt/jarvisbench
COPY pyproject.toml README.md ./
COPY src ./src
COPY plugins ./plugins
COPY TASKS_PROVENANCE.json TASKS_SHA256SUMS ./
COPY scripts/__init__.py scripts/verify_checksums.py ./scripts/

COPY runtime/docker/entrypoint.sh /usr/local/bin/jarvisbench-entrypoint
COPY runtime/docker/capability_manifest.py /usr/local/bin/jarvisbench-capabilities
COPY runtime/docker/validate_runtime.py /usr/local/bin/jarvisbench-runtime-validate
COPY runtime/bin/jb /usr/local/bin/jb
RUN chmod 0755 \
       /usr/local/bin/jarvisbench-entrypoint \
       /usr/local/bin/jarvisbench-capabilities \
       /usr/local/bin/jarvisbench-runtime-validate \
       /usr/local/bin/jb \
    && chmod 0444 /opt/jarvisbench/TASKS_PROVENANCE.json /opt/jarvisbench/TASKS_SHA256SUMS \
    && python3 /usr/local/bin/jarvisbench-runtime-validate --runtime-only \
    && install -d -m 0700 /var/lib/jarvisbench/openclaw /var/lib/jarvisbench/episodes /workspace \
    && groupadd --system --gid 10001 jarvisbench \
    && useradd --system --uid 10001 --gid 10001 --home-dir /var/lib/jarvisbench --shell /usr/sbin/nologin jarvisbench \
    && chown -R jarvisbench:jarvisbench /var/lib/jarvisbench /workspace

ENV JARVISBENCH_WORKSPACE_ROOT=/workspace

USER jarvisbench
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/jarvisbench-entrypoint"]
CMD ["--help"]
