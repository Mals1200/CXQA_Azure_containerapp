# Use official Python base image
FROM python:3.9-slim

# Install system dependencies for Azure Speech SDK
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy all application files
COPY . .

# Expose ports for:
# - Original bot (80)
# - Voice interface (8080)
EXPOSE 80 8080

# Run both services simultaneously
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:80 --workers 4 & gunicorn app_voice:app --bind 0.0.0.0:8080 --workers 2"]
