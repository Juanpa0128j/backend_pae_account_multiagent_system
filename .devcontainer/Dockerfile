FROM mcr.microsoft.com/devcontainers/python:3.11

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set shell to bash
SHELL ["/bin/bash", "-c"]
