# Dockerfile
#
# Single Dockerfile for all five agents.
# Build with --build-arg AGENT_NAME=<name> to tag which agent this image runs.
#
# Example builds:
#   docker build --build-arg AGENT_NAME=agent1a -t ai-news-agent-1a .
#   docker build --build-arg AGENT_NAME=agent1b -t ai-news-agent-1b .
#   docker build --build-arg AGENT_NAME=agent2a -t ai-news-agent-2a .
#   docker build --build-arg AGENT_NAME=agent2b -t ai-news-agent-2b .
#   docker build --build-arg AGENT_NAME=agent3  -t ai-news-agent-3  .

# ── Base image ───────────────────────────────────────────────────────────────
# python:3.11-slim is a minimal Debian image with Python 3.11 pre-installed.
# "slim" means no build tools, docs, or test files — keeps the image small.
FROM python:3.11-slim

# ── Working directory ─────────────────────────────────────────────────────────
# All subsequent commands run from /app inside the container.
# This is also where your code will live.
WORKDIR /app

# ── System dependencies ───────────────────────────────────────────────────────
# newspaper3k needs libxml2 and libxslt for HTML parsing.
# --no-install-recommends keeps the layer small.
# We clean up apt caches in the same RUN command to avoid bloating the layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy requirements.txt BEFORE the rest of the code.
# Docker caches each layer. If requirements.txt hasn't changed, this
# expensive pip install step is skipped on subsequent builds entirely.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Also install FastAPI + uvicorn (our HTTP server) — not in requirements.txt
# since they're only needed for Cloud Run, not local runs.
RUN pip install --no-cache-dir fastapi uvicorn

# ── Application code ──────────────────────────────────────────────────────────
# Copy everything else after deps. Changes to .py files won't invalidate
# the expensive pip install layer above.
COPY . .

# ── Agent selection ───────────────────────────────────────────────────────────
# ARG is a build-time variable. It gets baked into the image as an ENV var
# so the running container knows which agent it is.
ARG AGENT_NAME
ENV AGENT_NAME=${AGENT_NAME}

# ── Port ──────────────────────────────────────────────────────────────────────
# Cloud Run injects PORT at runtime (default 8080). We expose it here
# for documentation purposes — EXPOSE doesn't actually open the port,
# it's just metadata.
EXPOSE 8080

# ── Entrypoint ────────────────────────────────────────────────────────────────
# uvicorn is the ASGI server that runs our FastAPI app.
# --host 0.0.0.0 means accept connections from outside the container (required).
# --port uses the PORT env var injected by Cloud Run (default 8080).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]