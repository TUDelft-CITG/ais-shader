FROM mcr.microsoft.com/devcontainers/python:3.13

# Install system dependencies for CGAL C++ extensions and geospatial libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libcgal-dev \
    libboost-dev \
    libgmp-dev \
    libmpfr-dev \
    libgdal-dev \
    gdal-bin \
    libgeos-dev \
    libproj-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv using the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Keep container virtualenv isolated in container user home to prevent host-mount collisions
ENV UV_PROJECT_ENVIRONMENT=/home/vscode/.venv
ENV PATH="/home/vscode/.venv/bin:${PATH}"
ENV UV_LINK_MODE=copy
