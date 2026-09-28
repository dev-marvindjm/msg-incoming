FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Install system dependencies for networking and asyncpg build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, reliable dependency installation
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Copy dependency definition files
COPY pyproject.toml requirements.txt ./

# Install python dependencies into system environment
RUN uv pip install --system -r requirements.txt

# Copy application source code
COPY app/ ./app/

# Create persistent session folder for Telethon
RUN mkdir -p /app/sessions

# Expose microservice HTTP port
EXPOSE 8001

# Healthcheck probe hitting the internal /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Launch uvicorn ASGI production server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
