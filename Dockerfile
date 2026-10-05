FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy offline wheels and requirements
COPY requirements.txt ./
COPY wheels/ ./wheels/

# Install directly from local wheels (100% offline, zero network requests, instant!)
RUN pip install --no-cache-dir --no-index --find-links=wheels -r requirements.txt && rm -rf ./wheels

# Copy application source code and migrations
COPY app/ ./app/
COPY alembic.ini ./
COPY migrations/ ./migrations/

# Create persistent session folder for Telethon
RUN mkdir -p /app/sessions

EXPOSE 8001

# Healthcheck using python standard library (zero external binary dependencies)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
