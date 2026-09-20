.PHONY: python-check engine-test backend-test frontend-check protocol-check compose-check

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
	docker compose -f deploy/docker-compose.yml config >/dev/null
