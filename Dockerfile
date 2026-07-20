FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# All services run from this one image with different commands:
#   combined (single container, Unraid default): campwatch-combined
#   split (docker-compose): campwatch-receiver / campwatch-watcher
ENV CAMPWATCH_CONFIG=/app/config.yaml \
    CAMPWATCH_RUNTIME_DIR=/app/runtime/camply \
    PYTHONUNBUFFERED=1

CMD ["campwatch-combined"]
