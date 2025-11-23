
FROM python:3.11-slim

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .

# Force rebuild of dependencies
ENV REFRESH_DEPENDENCIES=2025-11-23_v1

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Browsers (System-wide)
# Use module syntax to ensure we use the installed package's version manager
RUN python -m playwright install --with-deps chromium

# Copy application code
COPY . .

# Expose port
EXPOSE 8080

# Run the API
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

