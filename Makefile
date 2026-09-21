.PHONY: python-check engine-test backend-test frontend-check protocol-check compose-check images lock

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

VERSION ?= dev

images:
	docker build -f backend/Dockerfile -t nether-earth-backend:$(VERSION) .
	docker build -f frontend/Dockerfile -t nether-earth-frontend:$(VERSION) .

lock:
	docker run --rm -v "$(CURDIR)":/src:ro python:3.12-slim-bookworm sh -c '\
	  cp -r /src/engine /src/backend /tmp/ && \
	  pip install -q --root-user-action=ignore --disable-pip-version-check /tmp/engine /tmp/backend && \
	  pip freeze --exclude nether-earth-engine --exclude nether-earth-backend' > backend/requirements.lock.new
	{ head -3 backend/requirements.lock; cat backend/requirements.lock.new; } > backend/requirements.lock.tmp
	mv backend/requirements.lock.tmp backend/requirements.lock && rm backend/requirements.lock.new
