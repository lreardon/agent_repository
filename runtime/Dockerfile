# Arcoa Hosted Agent Base Runtime
#
# This is the base image for hosted agents. The deploy pipeline generates
# a per-agent Dockerfile that extends this pattern, but this file serves
# as documentation and a build target for pre-warming the image cache.

FROM python:3.13-slim

WORKDIR /app

# System dependencies commonly needed by agents
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Pre-install the Arcoa SDK
RUN pip install --no-cache-dir arcoa

# Non-root user
RUN adduser --disabled-password --gecos '' --uid 10001 agent

USER agent

# Default entrypoint — overridden by per-agent build
CMD ["python", "/app/_arcoa_entrypoint.py"]
