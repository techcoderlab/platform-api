# =========================
# Stage 1: Builder
# =========================
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build tools needed to compile Python packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip

# Copy requirements and install packages into user space
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# =========================
# Stage 2: Production
# =========================
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies for OpenCV, Pillow, and Playwright
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 chromium && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create a non-root user and setup directories
RUN useradd -m -s /bin/bash appuser && \
    mkdir -p /app/data/image_zips && \
    chown -R appuser:appuser /app

# Copy installed packages from builder stage
COPY --from=builder /root/.local /home/appuser/.local

# Copy application code
COPY --chown=appuser:appuser app ./app

# Switch to non-root user
USER appuser

# Update PATH for user-installed Python packages
ENV PATH=/home/appuser/.local/bin:$PATH
ENV PYTHONPATH=/app


# Expose application port
EXPOSE 8012

# Production entrypoint
CMD ["python", "-m", "uvicorn", "app.main:app", "--host=0.0.0.0", "--port=8012"]
