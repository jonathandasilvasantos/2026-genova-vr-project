# Genova VR server and builder in one image.
# Build:  docker build -t genova-vr-server .
# Run:    docker run --rm -p 8080:8080 --env-file .env -v "$PWD/data:/app/data" genova-vr-server
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GENOVA_VR_DATA=/app/data \
    COOKIE_SECURE=0

RUN apt-get update \
 && apt-get install -y --no-install-recommends espeak-ng ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./requirements.txt
COPY builder/requirements.txt ./builder/requirements.txt
# CPU-only PyTorch keeps the image small (the default Linux wheel pulls CUDA libraries).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt -r builder/requirements.txt gunicorn

COPY . .
RUN mkdir -p /app/data

EXPOSE 8080
# Single worker: the app keeps job state in process and in the data folder.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "600", "app:app"]
