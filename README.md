# Nether Earth

Browser-based multiplayer clone of the ZX Spectrum version of **Nether Earth**.

The repository is specification-driven. Read `AGENTS.md` and `_specs/` before implementation work.

## M0 development setup

### Python

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e './engine[dev]' -e './backend[dev]'
make python-check
```

Run the backend:

```bash
uvicorn app.main:app --app-dir backend --reload
```

Health check: `http://localhost:8000/health`.

### Frontend

Requires Node.js 22+.

```bash
cd frontend
npm install
npm run protocol:validate
npm run protocol:generate
npm run typecheck
npm run build
npm run dev
```

### Docker topology

Validate the Compose topology:

```bash
docker compose -f deploy/docker-compose.yml config
```

Run it locally:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

The public M0 gateway is exposed on `http://localhost:8080`.

## Quality commands

```bash
make python-check
make frontend-check
make compose-check
```

M0 intentionally provides only foundation behavior. Gameplay systems begin in later milestones.
