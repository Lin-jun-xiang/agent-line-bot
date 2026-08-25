# This service is not pure Python: the agent runs the `claude` CLI as a
# subprocess, so the image needs Node alongside Python. That is also why Render's
# native Python runtime cannot host this project — deploy it as Docker.

FROM python:3.12-slim

# - nodejs/npm : required to install and run the Claude Code CLI
# - tzdata     : zoneinfo needs it, otherwise AGENT_TIMEZONE silently falls back to UTC
# - curl       : used by the NodeSource setup script below
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg tzdata \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# zhipuai goes in separately with --no-deps: its pyjwt pin conflicts with the
# agent SDK's, and letting pip resolve it yields the ancient 1.0.7. See the note
# in requirements.txt.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps zhipuai==2.1.5.20250825

COPY . .

# The CLI writes its own state under $HOME; give it a writable one.
ENV HOME=/app \
    PYTHONUNBUFFERED=1 \
    PORT=8090 \
    AGENT_WORKSPACE_ROOT=/data/workspace

# Mount a persistent disk here on Render, otherwise every deploy wipes each
# user's MEMORY.md. See docs/deployment.md.
RUN mkdir -p /data/workspace

EXPOSE 8090

# Single worker: each turn holds a `claude` subprocess, and concurrency is
# handled inside the app on its own event loop.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8090} --workers 1"]
