FROM python:3.13-slim

WORKDIR /app

# System deps for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo-dev libpng-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Durable data dir (DB + photos). Mount a volume at /app/storage in production.
RUN mkdir -p /app/storage/images
ENV STORAGE_ROOT=/app/storage
ENV DATABASE_PATH=/app/storage/pack_manager.db
ENV IMAGE_STORAGE_PATH=/app/storage/images

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
