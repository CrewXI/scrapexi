
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

# Set working directory
WORKDIR /app

# Explicitly set Playwright browsers path to match pre-installed location
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Copy requirements first for caching
COPY requirements.txt .

# Install Python dependencies (without re-downloading browsers)
RUN pip install --no-cache-dir -r requirements.txt

# Verify browsers are available
RUN ls -la /ms-playwright/ || echo "Browsers not in /ms-playwright"

# Copy application code
COPY . .

# Expose port
EXPOSE 8080

# Run the API
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

