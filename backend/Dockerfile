# PlanLab API (FastAPI). Also used by the Render cron services with a different command.
#   docker build -t planlab-api ./backend
#   docker run -p 8000:8000 --env-file backend/.env planlab-api
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    PLANLAB_DB=/var/data/planlab.db

WORKDIR /app

# Dependencies first, so code changes reuse this layer. Playwright is only the client:
# KERNEL bots connect to KERNEL's cloud browsers, so no browser is installed here.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY db ./db

# SQLite lives on a mounted volume / Render disk. The container runs as root because Render
# mounts disks owned by root; nothing else in the image is writable that matters.
RUN mkdir -p /var/data
VOLUME ["/var/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
