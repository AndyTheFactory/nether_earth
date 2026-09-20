"""Schema-conformance tests (issue #91).

Walks every fixture in `protocol/fixtures/**/{valid,invalid}/*.json` --
Task 1's canonical corpus, also consumed by
`frontend/scripts/validate-protocol.mjs` -- through both the actual JSON
Schema files (via `jsonschema`) and the Pydantic models in
`app.protocol`, and asserts the two tools agree on every fixture:

- a `valid` fixture must parse under both tools;
- an `invalid` fixture must be rejected by both tools.

This is the test that would fail if the hand-written Pydantic models here
ever drifted from the canonical JSON Schema (e.g. a field renamed, a
`required` list edited, a `type` variant added) in only one place.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.protocol.client_messages import ClientMessageAdapter
from app.protocol.common import ProtocolEnvelope
from app.protocol.reconnect import ReconnectMessageAdapter
from app.protocol.server_messages import ServerMessageAdapter
from app.protocol.snapshot import SnapshotMessage

from .schema_registry import fixture_files, schema_base_names, validator_for

#: One Pydantic parse function per `protocol/fixtures/<name>/` directory,
#: matching `schema_registry`'s JSON Schema file per directory. Keys must
#: cover every schema file exactly once -- `test_fixture_directories_are_covered`
#: enforces that so a newly added schema file/fixture directory cannot be
#: silently skipped here.
_PYDANTIC_VALIDATORS: dict[str, Callable[[bytes], Any]] = {
    "common": ProtocolEnvelope.model_validate_json,
    "client_messages": ClientMessageAdapter.validate_json,
    "server_messages": ServerMessageAdapter.validate_json,
    "snapshot": SnapshotMessage.model_validate_json,
    "reconnect": ReconnectMessageAdapter.validate_json,
}


def _collect_cases() -> list[tuple[str, str, Path]]:
    cases: list[tuple[str, str, Path]] = []
    for base_name in schema_base_names():
        for expectation in ("valid", "invalid"):
            cases.extend(
                (base_name, expectation, path) for path in fixture_files(base_name, expectation)
            )
    return cases


_CASES = _collect_cases()
_CASE_IDS = [f"{base}/{expectation}/{path.name}" for base, expectation, path in _CASES]


def test_fixture_directories_are_covered() -> None:
    assert set(_PYDANTIC_VALIDATORS) == set(schema_base_names())


def test_fixture_corpus_is_nonempty() -> None:
    valid = [c for c in _CASES if c[1] == "valid"]
    invalid = [c for c in _CASES if c[1] == "invalid"]
    assert valid, "expected at least one valid fixture"
    assert invalid, "expected at least one invalid fixture"


@pytest.mark.parametrize(("base_name", "expectation", "path"), _CASES, ids=_CASE_IDS)
def test_fixture_matches_json_schema(base_name: str, expectation: str, path: Path) -> None:
    data = json.loads(path.read_text())
    errors = list(validator_for(base_name).iter_errors(data))
    if expectation == "valid":
        assert errors == [], f"{path} should be schema-valid but failed: {errors}"
    else:
        assert errors, f"{path} should be schema-invalid but passed JSON Schema validation"


@pytest.mark.parametrize(("base_name", "expectation", "path"), _CASES, ids=_CASE_IDS)
def test_fixture_matches_pydantic(base_name: str, expectation: str, path: Path) -> None:
    raw = path.read_bytes()
    validate = _PYDANTIC_VALIDATORS[base_name]
    if expectation == "valid":
        validate(raw)  # must not raise
    else:
        with pytest.raises(PydanticValidationError):
            validate(raw)
