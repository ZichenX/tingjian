# Minor line is deliberate: receive Debian/Python security fixes on --pull.
# After acceptance, pin the resulting image digest for your production release.
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MODEL_DIR=/models \
    OMP_NUM_THREADS=3 OPENBLAS_NUM_THREADS=1 MALLOC_ARENA_MAX=2
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt \
    && python -m pip freeze > /opt/runtime-installed.txt
RUN groupadd --gid 10001 tingjian && useradd --uid 10001 --gid tingjian --no-create-home --shell /usr/sbin/nologin tingjian \
    && mkdir -p /models
COPY app /app/app
COPY web /app/web
COPY scripts /app/scripts
COPY MODEL_LICENSES.md /app/MODEL_LICENSES.md
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--ws", "websockets", "--ws-max-size", "131072", "--ws-max-queue", "4", "--ws-ping-interval", "20", "--ws-ping-timeout", "20", "--limit-concurrency", "32", "--backlog", "64", "--timeout-keep-alive", "10", "--timeout-graceful-shutdown", "25"]

FROM runtime AS test
USER root
COPY requirements-dev.txt /app/requirements-dev.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements-dev.txt
COPY tests /app/tests
COPY pyproject.toml /app/pyproject.toml
CMD ["python", "-m", "pytest", "-q"]
