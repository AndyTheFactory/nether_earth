# syntax=docker/dockerfile:1
# Production backend image. Build context: repository root.
#   docker build -f backend/Dockerfile -t nether-earth-backend:<version> .

# Digest-pinned (NE-09); Dependabot (docker ecosystem) proposes updates.
FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY backend/requirements.lock backend/requirements.lock
# Third-party dependencies come only from the pinned lock file; the two
# first-party packages are then built without dependency resolution.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-deps --require-virtualenv --require-hashes -r backend/requirements.lock
COPY engine engine
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN /opt/venv/bin/pip install --no-deps --require-virtualenv ./engine ./backend \
 && /opt/venv/bin/pip check

# Digest-pinned (NE-09); Dependabot (docker ecosystem) proposes updates.
FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS runtime
# Which commit this image was built from, logged at startup so a running
# container can be identified without guessing from image timestamps.
ARG GIT_COMMIT=unknown
ENV NETHER_EARTH_COMMIT=$GIT_COMMIT \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    NETHER_EARTH_ENV=production \
    NETHER_EARTH_REPLAY_DIR=/var/lib/nether-earth/replays \
    NETHER_EARTH_MAP_DIR=/opt/nether-earth/data/maps
RUN groupadd --system --gid 10001 nether \
 && useradd --system --uid 10001 --gid nether --no-create-home --shell /usr/sbin/nologin nether \
 && install -d -o nether -g nether /var/lib/nether-earth/replays
COPY --from=build /opt/venv /opt/venv
COPY data/maps /opt/nether-earth/data/maps
USER nether:nether
WORKDIR /var/lib/nether-earth
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).status == 200 else 1)"]
# --ws-max-size bounds inbound WebSocket frames at the server. Forwarded
# headers are trusted only from 127.0.0.1 (uvicorn's default): the app never
# uses the client address, and trusting any peer would become a spoofing
# vector if the backend port were ever published. Access logs come from the
# Nginx gateway instead.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--no-server-header", \
     "--no-access-log", "--ws-max-size", "16384", "--timeout-graceful-shutdown", "10"]
