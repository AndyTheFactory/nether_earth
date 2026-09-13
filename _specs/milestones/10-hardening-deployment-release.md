# Milestone 10 — Hardening, Deployment & v1 Release

## Goal

Turn the completed PvP vertical slice into a reliable v1 deployment on the locked single-VPS architecture, with production-ready configuration, observability, security basics, operational documentation, regression protection, and a reproducible release process.

## Spec references

- `_specs/technical-spec.md` deployment, persistence, backend/runtime, replay, and architecture sections
- `_specs/functional-spec.md` v1 scope and non-goals
- `_specs/agentic-programming-prd.md` quality/integration gates

## Dependencies

- Milestone 9 complete.

## Deliverable

A documented and reproducible v1 deployment using Docker Compose and Nginx, with frontend/backend containers, mounted replay/debug storage, health checks, production configuration, CI release gates, operational logging, basic security hardening, and rollback/recovery instructions appropriate to the intentionally simple architecture.

## Workstreams and candidate tasks

### Production containerization
Finalize frontend/backend build images, runtime users, dependency locking, minimal images where practical, health checks, and deterministic configuration.

### Docker Compose topology
Finalize the single-VPS service topology and mounted replay/debug volume. Do not add PostgreSQL, Redis, brokers, Kubernetes, or distributed match infrastructure.

### Nginx and TLS-facing configuration
Configure static/frontend serving, backend API routing, WebSocket upgrade/proxy behavior, sensible request/timeouts, and deployment documentation for HTTPS/TLS termination.

### Runtime hardening
Validate guest/session token handling, nickname/input limits, WebSocket message validation/size limits, resource bounds, error handling, CORS/origin policy as applicable, and safe replay/debug filenames/paths.

### Observability and diagnostics
Provide structured enough logs to trace match/session lifecycle, engine/runtime failures, disconnects, replay file creation, and health state without leaking unnecessary secrets.

### Performance and soak testing
Measure representative concurrent matches for the intended single-process/single-VPS design, identify event-loop blocking or memory leaks, and document a supported operational envelope rather than prematurely introducing horizontal scaling.

### Regression/release gates
Run engine, protocol, backend, frontend, deterministic full-match replay, build, and deployment-configuration checks in CI before release.

### Operations documentation
Document installation, configuration, start/stop/update, health verification, replay/debug locations, log inspection, backup expectations for replay files, and rollback.

### v1 release checklist
Create a concise release checklist covering clean build, tests, deterministic regression fixtures, deployment smoke test, two-client match smoke test, and version/tag procedure.

## Parallelization

Container/Nginx work, runtime security review, observability, performance testing, and operations documentation can proceed in parallel against the stable Milestone 9 product. Release-gate integration follows once their commands/configuration are stable.

## Acceptance criteria

- Production Docker Compose configuration starts the complete application on a clean supported host.
- Nginx correctly serves/proxies HTTP and WebSocket traffic.
- Replay/debug files persist on the mounted filesystem across container restart.
- Health checks identify failed services.
- Production configuration contains no default development secrets or unsafe debug mode.
- Client/session input is bounded and protocol validated.
- Representative concurrent-match soak testing completes without known critical leak/deadlock/event-loop starvation.
- CI executes all release-critical tests, including deterministic full-match replay.
- Deployment and rollback procedures are documented and tested sufficiently for v1.
- No out-of-scope infrastructure is introduced to solve speculative scaling problems.

## Milestone integration scenario

On a clean deployment environment, build and start the production stack, connect two browser clients through Nginx, create and complete a representative match, verify WebSocket stability and persisted replay output, restart the application, verify health/log/replay behavior, and run the documented rollback or redeploy procedure.

## Out of scope

- Multi-node/high-availability architecture.
- Database-backed accounts or persistent player state.
- Redis or message brokers.
- Kubernetes.
- AI opponent.
- v1.5 gameplay/features.

## Open questions / blockers

No known gameplay question should be introduced in this milestone. If hardening exposes a behavior ambiguity, route it back to the authoritative gameplay spec rather than choosing a new rule operationally.

Human review is required for security findings that require scope/architecture changes, release blockers, destructive migration/operational steps, or conflicts between production requirements and the locked architecture.

## Definition of done

- All milestone issues are closed by merged PRs.
- Production deployment integration scenario passes.
- Release gates are green.
- Operational documentation and rollback procedure are present.
- v1 can be tagged/released without unresolved critical defects or hidden architecture changes.
