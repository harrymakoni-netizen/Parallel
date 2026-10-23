# syntax=docker/dockerfile:1.7
# Parallel processing worker: managed engines (JuPedSim, SUMO, SWMM, EPANET, SimPy, OpenStudio and
# OpenFOAM v1912), reconstruction (COLMAP, FFmpeg), the demonstration seeding command and backups
# (PostgreSQL 16 client tools, matching the database server).
# Ubuntu 24.04 is the platform the engine integrations were verified on (docs/engine-matrix.md).
# Each Python engine gets its own isolated environment, so engine dependencies never conflict.
#
#   docker build -f infra/docker/worker.Dockerfile -t parallel-worker .
#
# Licensed or GPU engines (Isaac Sim, FDS, EnergyPlus, HEC-RAS, Ventsim, FLAC3D, 3DEC, Virtual CRASH,
# PC-Crash) are not installed here; see docs/deployment.md for connected and file-exchange setups.
ARG UBUNTU_IMAGE=ubuntu:24.04
FROM ${UBUNTU_IMAGE}

ARG UV_VERSION=0.11.32
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH

COPY infra/docker/build-ca.sh /usr/local/bin/build-ca
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv python3-pip \
      openfoam colmap ffmpeg libgl1 libglib2.0-0 postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=ca,required=false \
    build-ca pip install --no-cache-dir --break-system-packages "uv==${UV_VERSION}"

WORKDIR /app
COPY . /app
# Sources are made readable by the runtime user whatever file modes the build context had (before the
# environment is created, so only the small source tree is touched); root keeps ownership.
# The worker platform's Python (3.12 on Ubuntu 24.04) is within the project's supported range.
RUN --mount=type=secret,id=ca,required=false --mount=type=cache,target=/root/.cache/uv \
    chmod -R a+rX /app && build-ca uv sync --frozen --no-dev --all-packages --python /usr/bin/python3 \
 && PARALLEL_RUNTIME_PYTHON=/usr/bin/python3 build-ca scripts/setup_runtimes.sh

RUN useradd --uid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin parallel \
 && mkdir -p /data/storage /data/workspaces && chown -R parallel:parallel /data
USER parallel
ENV PARALLEL_STORAGE_ROOT=/data/storage \
    PARALLEL_WORKSPACE_ROOT=/data/workspaces \
    PARALLEL_RUNTIMES_DIR=/app/.runtimes \
    PARALLEL_OPENFOAM_DIR=/usr/share/openfoam

CMD ["celery", "-A", "parallel_simulation.worker", "worker", "-Q", "cpu,maintenance", "-c", "2", "-n", "sim@%h", "--loglevel=INFO"]
