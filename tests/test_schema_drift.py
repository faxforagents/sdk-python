"""Every property in the saved live OpenAPI schemas needs a declared model field."""

from pathlib import Path

import yaml

from faxforagents import models


def test_every_schema_property_is_declared() -> None:
    spec = yaml.safe_load((Path(__file__).resolve().parents[1] / "spec/openapi.yaml").read_text("utf-8"))
    missing = []
    for name, schema in spec["components"]["schemas"].items():
        model = getattr(models, name, None)
        if model is None:
            missing.append(name)
            continue
        declared = {field.alias or name for name, field in model.model_fields.items()}
        missing.extend(f"{name}.{prop}" for prop in schema.get("properties", {}) if prop not in declared)
    assert not missing, "Undeclared schema properties or models: " + ", ".join(missing)


def test_extraction_capability_is_a_declared_boolean() -> None:
    field = models.Capabilities.model_fields["extraction"]
    assert field.annotation == bool | None
    assert not field.is_required()
    for value in (False, True):
        capabilities = models.Capabilities.model_construct(extraction=value)
        assert capabilities.extraction is value
        assert "extraction" not in (capabilities.model_extra or {})
