# Protocol

JSON Schema is the source of truth for frontend/backend transport contracts.

## Schema files

- `schemas/common.schema.json` — shared identifier/value `$defs` (`protocolVersion`, `matchId`, `playerId`, `sessionToken`, `nickname`, etc.) referenced by every other schema via `$ref`. Also the real, fully enumerated gameplay command/state shapes (issue #98) — `commandPayload` (a discriminated union, one variant per concrete `nether_earth` `Command` subclass a v1 player can trigger: `commander_move`, `commander_set_vertical_intent`, `direct_robot_move`, `robot_fire`, `set_robot_order`, `select_module`, `deselect_module`, `cancel_construction`, `launch_robot`), `robotOrder` (the nested five-variant union `set_robot_order`'s `order` field carries: `stop_and_defend`/`advance`/`retreat`/`search_capture`/`search_destroy`, mirroring `nether_earth.orders.Order`), and `snapshotState` (a field-shape mirror of `nether_earth.snapshot.to_snapshot`'s exact return shape). No gameplay legality lives here or anywhere else in this directory — a structurally valid but illegal command (e.g. a move into a wall) is accepted by these schemas and rejected only by the engine itself, never by the schema or its Pydantic mirror.

  `snapshotState`'s keys are intentionally left snake_case (not the camelCase every other protocol field uses) because it is engine data (`to_snapshot`'s own output) passed through the transport boundary verbatim, so a client can diff it byte-for-byte against engine-side snapshot fixtures. A few of its deeply-nested/highly-polymorphic leaves (`build`, `buffer`/`entry_snapshot`, `movement`, `order`, `horizontal_transition`/`vertical_transition`) are left as loosely-typed objects rather than re-deriving the engine's own full nested schema a second time — see `nether_earth.snapshot`'s own per-entity helper functions for their authoritative shape.

  `commandPayload`'s numeric fields (e.g. `robotOrder`'s `advance`/`retreat` `distanceMiles`) deliberately carry no schema-level range bound even where the engine has one (`orders.MAX_ORDER_DISTANCE_MILES = 50`): the locked functional-spec response to an out-of-range order is not "reject the frame" but "accept it and fall back to `StopAndDefend`" (a real, observable engine outcome), so baking the bound into the transport layer would make that documented fallback unreachable. Schema/Pydantic validation here is structural only (right shape, right type) — see `app/protocol/common.py`'s `AdvanceOrderPayload` docstring (backend) for the same rule stated from the Pydantic side.
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
