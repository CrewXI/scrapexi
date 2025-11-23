
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

# Set working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# CRITICAL: Install Playwright browsers explicitly for the Python package
# The base image has Node.js browsers, but Python needs its own
RUN python -m playwright install chromium --with-deps

# Copy application code
COPY . .

# Expose port
EXPOSE 8080

# Run the API
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

# Force Railway rebuild
