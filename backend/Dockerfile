FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY app ./app
COPY config ./config
COPY prompts ./prompts

ENV OMP_NUM_THREADS=4

EXPOSE 8000

# Shell form (not exec-array form) so $PORT actually expands — Render's
# Docker services inject a dynamic PORT env var the container must bind to;
# docker-compose.yml never sets PORT, so this falls back to 8000, identical
# to the previous hardcoded behavior for local/Docker Compose use.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
