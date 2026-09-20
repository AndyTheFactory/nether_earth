"""Loads `protocol/schemas/*.schema.json` for use as the JSON Schema side of
the conformance tests.

This is test-only plumbing (jsonschema is a backend dev dependency, not a
runtime one): it gives the conformance tests an independent JSON Schema
validator per schema file, registered together so `$ref`s that cross files
(e.g. `client_messages.schema.json` -> `common.schema.json`,
`reconnect.schema.json` -> `snapshot.schema.json`) resolve correctly --
mirroring how `frontend/scripts/validate-protocol.mjs` registers every
schema file before compiling any of them.
"""

from __future__ import annotations

import json
from functools import cache, lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

#: `backend/tests/protocol/schema_registry.py` -> repo root -> `protocol/`.
_PROTOCOL_ROOT = Path(__file__).resolve().parents[3] / "protocol"
SCHEMAS_DIR = _PROTOCOL_ROOT / "schemas"
FIXTURES_DIR = _PROTOCOL_ROOT / "fixtures"


def _schema_base_name(path: Path) -> str:
    """`common.schema.json` -> `common` (matches a `protocol/fixtures/` dir name)."""
    return path.name.removesuffix(".schema.json")


@lru_cache(maxsize=1)
def _load_schemas() -> dict[str, dict[str, object]]:
    schemas: dict[str, dict[str, object]] = {}
    for schema_path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        schemas[_schema_base_name(schema_path)] = json.loads(schema_path.read_text())
    return schemas


@lru_cache(maxsize=1)
def _registry() -> Registry:
    registry = Registry()
    for schema in _load_schemas().values():
        resource = Resource.from_contents(schema)
        uri = resource.id()
        assert uri is not None, "every protocol schema must declare $id"
        registry = registry.with_resource(uri=uri, resource=resource)
    return registry


@cache
def validator_for(base_name: str) -> Draft202012Validator:
    """Return a validator for the schema file matching a fixture directory name."""
    schema = _load_schemas()[base_name]
    return Draft202012Validator(schema, registry=_registry())


def fixture_files(base_name: str, expectation: str) -> list[Path]:
    """List `protocol/fixtures/<base_name>/<expectation>/*.json` fixtures."""
    directory = FIXTURES_DIR / base_name / expectation
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


def schema_base_names() -> list[str]:
    return sorted(_load_schemas())
