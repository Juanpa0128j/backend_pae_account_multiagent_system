FROM mcr.microsoft.com/devcontainers/python:3.11

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /workspace

# Pre-install dependencies into the image for fast container startup.
# The lock file pins exact versions; --frozen ensures no resolution happens.
COPY pyproject.toml uv.lock ./
# --no-install-project: install deps only; the project itself is mounted at runtime
RUN uv sync --frozen --no-cache --no-install-project

# Set shell to bash
SHELL ["/bin/bash", "-c"]
