FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NETHER_EARTH_REPLAY_DIR=/app/replays \
    NETHER_EARTH_MAP_DIR=/app/data/maps

COPY engine /app/engine
COPY backend /app/backend
COPY data /app/data
RUN python -m pip install --no-cache-dir /app/engine /app/backend

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
