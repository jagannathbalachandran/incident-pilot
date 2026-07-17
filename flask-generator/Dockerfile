# flask-generator/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (none needed for this minimal setup)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config.py .
COPY incident_scenarios.py .
COPY metrics_exporter.py .
COPY log_generator.py .
COPY app.py .

EXPOSE 5000

CMD ["python", "app.py"]
