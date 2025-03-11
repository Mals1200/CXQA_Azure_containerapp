version: '3.8'

services:
  # Original Bot Service
  main-bot:
    build: .
    ports:
      - "3000:80"  # Original bot on port 3000
    environment:
      - MICROSOFT_APP_ID=OLD_APP_ID_HERE
      - MICROSOFT_APP_PASSWORD=OLD_APP_PASSWORD_HERE
      - FLASK_ENV=production

  # New Voice Interface
  voice-bot:
    build: .
    ports:
      - "3001:8080"  # Voice interface on port 3001
    command: gunicorn app_voice:app --bind 0.0.0.0:8080 --workers 2
    environment:
      - SPEECH_KEY=DASZPVLJFKpMzpbXDFkAuCQwDMZTHlHM4IehdaGlyapdHIKTqrQQWBCACvRgFU3vAAAAwKCQsfg
      - BOT_ENDPOINT=http://main-bot:80/ask

  # Reverse Proxy with SSL
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./certbot/conf:/etc/letsencrypt
      - ./certbot/www:/var/www/certbot
    depends_on:
      - main-bot
      - voice-bot
