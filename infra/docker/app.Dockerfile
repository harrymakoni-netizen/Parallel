# syntax=docker/dockerfile:1.7
# Parallel application image: API, results worker, recovery scheduler, migrations and admin commands.
# Engines are not installed here; simulation and reconstruction run in the worker image.
#
#   docker build -f infra/docker/app.Dockerfile -t parallel-app .
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE}

ARG UV_VERSION=0.11.32
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH

# ffprobe: the API inspects every uploaded video (duration, resolution, codec) before accepting it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY infra/docker/build-ca.sh /usr/local/bin/build-ca
RUN --mount=type=secret,id=ca,required=false \
    build-ca pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app
COPY . /app
# Sources are made readable by the runtime user whatever file modes the build context had (before the
# environment is created, so only the small source tree is touched); root keeps ownership.
RUN --mount=type=secret,id=ca,required=false --mount=type=cache,target=/root/.cache/uv \
    chmod -R a+rX /app && build-ca uv sync --frozen --no-dev --all-packages --python /usr/local/bin/python3

# Non-root runtime user; storage and workspaces live on mounted volumes
RUN useradd --uid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin parallel \
 && mkdir -p /data/storage /data/workspaces && chown -R parallel:parallel /data
USER parallel
ENV PARALLEL_STORAGE_ROOT=/data/storage \
    PARALLEL_WORKSPACE_ROOT=/data/workspaces

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "parallel_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
