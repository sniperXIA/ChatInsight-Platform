# ChatInsight Platform Production Dockerfile
# Optimized for Ubuntu/Debian Cloud Server Deployment

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai

# Set working directory
WORKDIR /app

# Install system runtime dependencies: FFmpeg, FFprobe, curl, ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and configurations
COPY apps/ /app/apps/
COPY packages/ /app/packages/
COPY config/ /app/config/
COPY contracts/ /app/contracts/
COPY sample_data/ /app/sample_data/
COPY cli.py /app/cli.py
COPY pytest.ini /app/pytest.ini
COPY .env.example /app/.env.example

# Create data and cache directories
RUN mkdir -p /app/data/cache /app/data/uploads /app/logs

# Expose default HTTP port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Default command: launch FastAPI via Uvicorn
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
