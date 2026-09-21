# Deployment

Single-VPS Docker Compose stack (locked v1 topology):

```text
browser -> gateway (Nginx) -> frontend (static files)
                          \-> backend (FastAPI) -> host-mounted replay directory
```

No database, Redis, broker, Kubernetes, or distributed match storage is part of v1.

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | production services, hardening, health checks, replay volume |
| `.env.example` | required/optional configuration; copy to `deploy/.env` |
| `nginx/` | gateway reverse-proxy configuration |

Quick start (details, TLS, update and rollback: `docs/operations/runbook.md`):

```bash
cp deploy/.env.example deploy/.env   # then edit it
sudo install -d -o 10001 -g 10001 /srv/nether-earth/replays
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build --wait
```

Validate configuration only: `make compose-check`.
