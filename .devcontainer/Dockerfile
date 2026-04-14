FROM mcr.microsoft.com/devcontainers/python:3.11

# Install system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set shell to bash
SHELL ["/bin/bash", "-c"]
