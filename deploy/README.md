# Deployment skeleton

M0 models the locked single-VPS topology only:

```text
browser -> gateway (Nginx) -> frontend
                         \-> FastAPI backend -> mounted replay directory
```

No database, Redis, broker, Kubernetes, or distributed match storage is part of v1.

Validate with:

```bash
docker compose -f deploy/docker-compose.yml config
```

Run locally with:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

Production TLS/security/performance hardening belongs to Milestone 10.
