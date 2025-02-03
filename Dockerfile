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

# Upgrade pip first
RUN pip install --upgrade pip

# Copy dependency file separately to leverage Docker caching
COPY requirements.txt .

# Fix package conflict issues by installing critical dependencies first
RUN pip install --no-cache-dir "babel==2.9.1"
RUN pip install --no-cache-dir "botbuilder-core==4.16.2" "botbuilder-dialogs==4.16.2" "botbuilder-schema==4.16.2" \
    "botframework-connector==4.16.2" "botframework-streaming==4.16.2" "botbuilder-ai==4.16.2"

# Install all other dependencies
RUN pip install --no-cache-dir -r requirements.txt --no-deps

# Copy application code
COPY . .

# Expose the correct port
EXPOSE 80

# Start Gunicorn on port 80
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:80", "--workers", "4"]
