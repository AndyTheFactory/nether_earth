.PHONY: build down python-check engine-test backend-test frontend-check protocol-check compose-check images lock

# NETHER_EARTH_COMMIT is baked into both images and logged at startup, so a
# running container reports the commit it was built from.
export NETHER_EARTH_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

build:
	docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build --wait

down:
	docker compose -f deploy/docker-compose.yml --env-file deploy/.env down

python-check:
	ruff check engine backend
	cd engine && mypy src
	cd backend && mypy app
	pytest engine/tests backend/tests

engine-test:
	pytest engine/tests

backend-test:
	pytest backend/tests

protocol-check:
	cd frontend && npm run protocol:validate && npm run protocol:generate

frontend-check:
	cd frontend && npm run protocol:validate && npm run protocol:generate && npm run typecheck && npm test && npm run build

frontend-live-check:
	cd frontend && npm run live:check

compose-check:
	docker compose -f deploy/docker-compose.yml --env-file deploy/.env.example config -q
	NETHER_EARTH_TLS_CERT_DIR=/tmp NETHER_EARTH_ACME_WEBROOT=/tmp docker compose -f deploy/docker-compose.yml \
	  -f deploy/docker-compose.tls.yml --env-file deploy/.env.example config -q
	docker compose -f deploy/docker-compose.yml --env-file deploy/.env.example run --rm --no-deps -T gateway nginx -t

VERSION ?= dev

images:
	docker build -f backend/Dockerfile --build-arg GIT_COMMIT=$(NETHER_EARTH_COMMIT) -t nether-earth-backend:$(VERSION) .
	docker build -f frontend/Dockerfile --build-arg GIT_COMMIT=$(NETHER_EARTH_COMMIT) -t nether-earth-frontend:$(VERSION) .

lock:
	docker run --rm -v "$(CURDIR)":/src:ro python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 sh -c '\
	  cp -r /src/engine /src/backend /tmp/ && \
	  pip install -q --root-user-action=ignore --disable-pip-version-check /tmp/engine /tmp/backend && \
	  pip freeze --exclude nether-earth-engine --exclude nether-earth-backend' > backend/requirements.lock.new
	{ head -3 backend/requirements.lock; cat backend/requirements.lock.new; } > backend/requirements.lock.tmp
	mv backend/requirements.lock.tmp backend/requirements.lock && rm backend/requirements.lock.new
