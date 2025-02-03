# Use Python 3.10 for better compatibility
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install numpy first to avoid conflicts
RUN pip install --upgrade pip && pip install numpy>=1.23.0,<2.0

# Copy dependency file separately to leverage Docker caching
COPY requirements.txt .

# Install dependencies with relaxed resolution
RUN pip install --no-cache-dir --use-deprecated=legacy-resolver -r requirements.txt

# Copy application code
COPY . .

# Ensure the container exposes the correct port
EXPOSE 80

# Install Gunicorn explicitly (if not in requirements.txt)
RUN pip install gunicorn

# Start Gunicorn on port 80
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:80", "--workers", "4"]
