
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

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set Playwright Browsers Path to a fixed location to ensure persistence and finding
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Playwright Browsers (System-wide)
# Force reinstall to ensure we get the correct version matching the pinned requirements
RUN python -m playwright install --with-deps chromium

# Copy application code
COPY . .

# Expose port
EXPOSE 8080

# Run the API
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

