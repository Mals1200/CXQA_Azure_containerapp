# Use the official Python image as the base
FROM python:3.9-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency file separately to leverage Docker caching
COPY requirements.txt .

# Install dependencies with no-cache to prevent conflicts
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure the container exposes the correct port
EXPOSE 80

# Start Gunicorn on port 80
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:80", "--workers", "4"]
