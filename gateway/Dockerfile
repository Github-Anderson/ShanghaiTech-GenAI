FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SHANGHAITECH_GENAI_CONFIG=/app/config/local.json

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid gateway --no-create-home --shell /usr/sbin/nologin gateway

RUN install -d -m 0700 -o 10001 -g 10001 /app/browser-credentials

# Build context uses an allowlist; private files are excluded.
COPY --chown=10001:10001 gateway/ ./gateway/

USER 10001:10001

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--log-level", "critical"]
