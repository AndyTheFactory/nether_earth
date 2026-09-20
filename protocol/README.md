# Protocol

JSON Schema is the source of truth for frontend/backend transport contracts.

## Schema files

- `schemas/common.schema.json` — shared identifier/value `$defs` (`protocolVersion`, `matchId`, `playerId`, `sessionToken`, `nickname`, etc.) referenced by every other schema via `$ref`. Also placeholders (`commandPayload`, `snapshotState`) that issue #98 extends once the M5/M6 engine command/state surface is enumerated — no gameplay legality lives here or anywhere else in this directory.
- `schemas/client_messages.schema.json` — client lifecycle commands (`create`/`join`/`ready`/`leave`) plus the generic in-match gameplay command envelope (`command`).
- `schemas/server_messages.schema.json` — server lifecycle/events (`created`/`joined`/`ready_state`/`started`/`paused`/`resumed`/`forfeit`/`no_contest`/`finished`/`error`).
- `schemas/snapshot.schema.json` — authoritative snapshot envelope.
- `schemas/reconnect.schema.json` — client `reconnect` request and server `resync` response (embeds the snapshot envelope).

Every top-level message type requires `protocolVersion` as an explicit `const`, and every strict envelope sets `additionalProperties: false`.

## Validate and generate TypeScript

From `frontend/` after `npm install`:

```bash
npm run protocol:validate
npm run protocol:generate
```

`protocol:validate` compiles every schema file (catching unresolved `$ref`s) and then runs the fixtures under `protocol/fixtures/<schema-base-name>/{valid,invalid}/*.json` against the matching schema, asserting each `valid/` fixture is accepted and each `invalid/` fixture is rejected (unknown `type`, extra properties, missing/incorrect `protocolVersion`).

`protocol:generate` compiles all schema files together in a single pass (so a definition shared across files, such as `reconnect.schema.json`'s embedded snapshot envelope, is declared once) and writes the result to `protocol/generated/types.ts`, which must not be edited manually.

Python/Pydantic transport models should either be generated from, or mechanically validated against, these schemas when the gameplay protocol is introduced. Hand-maintained divergent message contracts are not allowed.
