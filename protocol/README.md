# Protocol

JSON Schema is the source of truth for frontend/backend transport contracts.

M0 provides only bootstrap envelopes. Gameplay messages are introduced in later milestones.

## Validate and generate TypeScript

From `frontend/` after `npm install`:

```bash
npm run protocol:validate
npm run protocol:generate
```

Generated TypeScript is written to `protocol/generated/types.ts` and must not be edited manually.

Python/Pydantic transport models should either be generated from, or mechanically validated against, these schemas when the gameplay protocol is introduced. Hand-maintained divergent message contracts are not allowed.
