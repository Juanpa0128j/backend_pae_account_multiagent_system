FROM mcr.microsoft.com/devcontainers/python:3.11

# Install system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install ngrok — needed for `make inngest-tunnel` (exposes local backend
# to Inngest Cloud branch envs). Auth token must be set at runtime via
# `ngrok config add-authtoken <token>`.
RUN curl -sSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz \
    | tar xz -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/ngrok

# Set shell to bash
SHELL ["/bin/bash", "-c"]
