# Kliprix backend (optional Docker path; docs/06 "Docker", D-004).
# Node 22 is only copied in as yt-dlp's JavaScript runtime for YouTube (D-025, AA-017).
FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
# ffmpeg (Debian's build includes libass); libgl1/libglib2.0-0 are needed by OpenCV (MediaPipe).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY requirements.txt requirements-nodeps.txt ./
RUN pip install --no-cache-dir -r requirements.txt \n    && pip install --no-cache-dir --no-deps -r requirements-nodeps.txt
COPY . .
ENV DATA_DIR=/app/data \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1
EXPOSE 8000
# 0.0.0.0 inside the container only; docker-compose publishes the port on 127.0.0.1.
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]
