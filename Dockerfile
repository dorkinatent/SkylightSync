FROM python:3.11-slim

# Install Chromium from Debian repos (avoids deprecated apt-key and Google's repo)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create directories for downloads and data
RUN mkdir -p downloads data

ENV CHROME_BIN=/usr/bin/chromium

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Default command
CMD ["python", "skylight_sync.py"]
