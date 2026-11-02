"""Declared fields cover the serializer shapes in fixtures/responses.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from faxforagents import models

CASES = json.loads((Path(__file__).parent / "fixtures/responses.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["model"])
def test_response_fields_are_declared(case: dict[str, Any]) -> None:
    model = getattr(models, case["model"])
    declared = {field.alias or name for name, field in model.model_fields.items()}
    assert set(case["body"]) <= declared, case["route"]
    parsed = model.model_validate(case["body"])
    assert parsed.model_extra == {}
    assert parsed.model_dump(by_alias=True, exclude_unset=True) == case["body"]


def test_new_fields_do_not_require_changes_to_older_payloads() -> None:
    for case in CASES:
        model = getattr(models, case["model"])
        old = {
            name: value
            for name, value in case["body"].items()
            if (model.model_fields.get(name) and model.model_fields[name].is_required()) or name == "from"
        }
        model.model_validate(old)
