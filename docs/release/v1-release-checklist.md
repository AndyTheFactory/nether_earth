# v1 release checklist and procedure

## Versioning

- Semantic versions. The first release is **`1.0.0`**; the Git tag is **`v1.0.0`**
  (annotated, on a commit of `main` whose CI is green).
- One version everywhere: `engine/pyproject.toml`, `backend/pyproject.toml`,
  `frontend/package.json` (+ `package-lock.json`), which the backend reports as its
  FastAPI version and in the `process_started` log. `scripts/check-version.sh` (a CI gate)
  fails on any mismatch, and on tag builds when the tag is not `v<version>`.
- Docker images are tagged with the same version (`NETHER_EARTH_VERSION=1.0.0` in
  `deploy/.env`); the previous version's images are the rollback target.
- Patch releases (`1.0.x`): fixes only. Minor (`1.x.0`): compatible additions. Any change to
  the wire protocol version or replay `schema_version` is called out in the release notes.

## Release candidates

- Pre-releases use `1.0.0-rcN` in all three version files (PEP 440 accepts it; Python
  reports it normalized as `1.0.0rcN`) and the tag `v1.0.0-rcN`, published as a GitHub
  **pre-release**.
- `v1.0.0-rc1` (2026-09-21, owner decision): cut after M9, CR001 and M10 acceptance.
  Waived for rc1: item 11 (production human two-player match — postponed to a later story)
  and item 15 (first release, no rollback target exists; confirmed by the owner).
  CR002 (#185) continues on `main` towards the next candidate.

## Gate: every box ticked, with the evidence linked in the release PR/issue

Blocking items: a release **must not** be tagged with any of them open, or with any
unresolved critical correctness, security or operational defect.

| # | Check | How / evidence |
| --- | --- | --- |
| 1 | M9 final acceptance complete (#119 human two-browser match, #120 closed; `_specs/open-questions.md` items marked critical decided) | issue links |
| 2 | All M10 issues closed by merged PRs (#122–#131) | milestone view |
| 3 | CI green on the release commit: Python lint/types/tests (incl. M9 deterministic full-match regression), protocol validation + generated-artifact drift, frontend typecheck/tests/build, version consistency, Compose/Nginx config, production image build + deployment smoke | Actions run link |
| 4 | Clean production images build from an empty cache | `docker builder prune -af && make images VERSION=<version>` |
| 5 | Protocol generated artifacts current | CI "Generated artifacts are current" step |
| 6 | Deterministic full-match regression passes | CI Pytest step (`backend/tests/acceptance/test_m9_full_match.py`) |
| 7 | Performance/soak: no unresolved critical finding; envelope still valid for the release | [performance report](../milestone-10/performance-report.md); re-run `backend/scripts/soak.py` if engine/runtime hot paths changed |
| 8 | Security review: no unresolved critical finding | [security review](../milestone-10/security-review.md); `pip-audit -r backend/requirements.lock`, `npm audit --omit=dev` clean |
| 9 | Production deployment smoke passes against the release images | `NETHER_EARTH_VERSION=<version> SMOKE_NO_BUILD=1 deploy/smoke.sh` → `SMOKE OK` |
| 10 | Replay persistence verified (host mount, survives restart) | part of #9 |
| 11 | Two-client smoke match passes: scripted (`#9`) **and** two human browsers through the production URL | note in release issue |
| 12 | Operations/rollback runbook present and current | [runbook](../operations/runbook.md) |
| 13 | Release version selected consistently | `scripts/check-version.sh` |
| 14 | Tag/release procedure followed (below) | tag + release links |
| 15 | Rollback target known: previous version and its images present on the host | `docker image ls 'nether-earth-*'` output in the release issue |

## Release sequence

1. **Freeze**: open a release issue listing the gate table; merge only release-blocking fixes.
2. **Version bump PR** (skip for the very first release, already at `1.0.0`):
   ```bash
   NEW=1.0.1
   sed -i "s/^version = \".*\"/version = \"$NEW\"/" engine/pyproject.toml backend/pyproject.toml
   (cd frontend && npm version --no-git-tag-version "$NEW")
   scripts/check-version.sh
   ```
   Merge after CI is green.
3. **Verify** on the merge commit of `main`: items 3–13 (CI run, clean image build, smoke with
   the built images, soak/security re-check if relevant). Record evidence in the release issue.
4. **Tag**:
   ```bash
   git checkout main && git pull --ff-only
   git tag -a v1.0.0 -m "Nether Earth v1.0.0"
   git push origin v1.0.0
   ```
   The tag push runs CI again, including the tag/version match.
5. **GitHub release**: `gh release create v1.0.0 --verify-tag --title "Nether Earth v1.0.0" --notes-file <notes>`
   with: highlights, protocol/replay schema versions, known limitations (below), upgrade and
   rollback notes (previous version).
6. **Deploy** per [runbook §9](../operations/runbook.md#9-update--redeploy); run runbook §6
   checks and one human two-browser match on the production URL.
7. **Close** the release issue with links to the CI run, smoke output, and deployment check.

If step 6 fails: roll back (runbook §10), then fix forward with a patch release. Never move
or delete a published tag.

## Known limitations accepted for v1 (non-critical)

- Guest-only play: no accounts; anyone with a join code takes the free seat of that lobby.
- All match state is in memory in one backend process: a backend restart/update ends live
  matches (their replays stay `in_progress`). No horizontal scaling (by design, v1 scope).
- Capacity is bounded by one CPU core: see the performance report for the measured envelope;
  `NETHER_EARTH_MAX_MATCHES` caps memory, not CPU.
- Per-IP gateway limits assume clients connect directly (or `real_ip` is configured).
- Replay artifacts are never pruned automatically (runbook §8).
- Open gameplay-fidelity research items in `_specs/open-questions.md` that are not marked
  critical ship with their documented interim policies.
