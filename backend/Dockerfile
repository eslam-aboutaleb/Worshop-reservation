FROM python:3.12-slim AS builder
WORKDIR /app
# Pin the uv installer image to a specific version so
# the build is reproducible and cannot silently pick up
# a breaking (or compromised) new uv release.
COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /uvx /bin/
COPY pyproject.toml .
# The root project depends on the ws-core workspace
# member, so its source must be present for the install.
COPY libs ./libs
RUN uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

FROM python:3.12-slim
WORKDIR /app
# Keep the container quiet and free of stale bytecode in the image.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN addgroup --system app && adduser --system --ingroup app app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY . .
RUN chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["sh", "-c", "alembic upgrade head && python -m src.seed && python -m src.main"]
