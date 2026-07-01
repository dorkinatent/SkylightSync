FROM python:3.11-slim

# No browser needed: photos are fetched via the iCloud web-stream JSON API.

# Set up working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create directories for downloads and data
RUN mkdir -p downloads data

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Default command
CMD ["python", "skylight_sync.py"]
