FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Both services run from this one image with different commands:
#   watcher:  campwatch-watcher
#   receiver: campwatch-receiver
ENV CAMPWATCH_CONFIG=/app/config.yaml \
    CAMPWATCH_RUNTIME_DIR=/app/runtime/camply \
    PYTHONUNBUFFERED=1

CMD ["campwatch-receiver"]
