FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PORT=7860

# Native deps for shapely / pyproj / osmnx.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgeos-dev \
        libspatialindex-dev \
        libproj-dev \
        proj-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend sources. .dockerignore filters out pycache, plots, secrets.
COPY . .

EXPOSE 7860

# Listen on $PORT (set by HF Spaces, Cloud Run, Fly, Render). Fallback to 7860.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]
