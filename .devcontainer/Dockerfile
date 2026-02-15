FROM mcr.microsoft.com/devcontainers/python:3.12

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /workspace

# Install dependencies if pyproject.toml exists
# COPY pyproject.toml uv.lock ./
# RUN uv sync --frozen --no-cache

# Set shell to bash
SHELL ["/bin/bash", "-c"]
