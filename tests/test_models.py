"""The generated models: aliases, extra fields, request strictness, and the generator's drift check."""

from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from faxforagents import models as m
from tests.conftest import fax_job


def test_fax_job_from_the_api_shape() -> None:
    job = m.FaxJob.model_validate(fax_job(**{"from": "+12025550100"}, state="delivered", new_field="later"))
    assert job.from_ == "+12025550100"
    assert job.state == "delivered"
    assert job.model_dump(by_alias=True)["from"] == "+12025550100"
    assert job.model_dump()["new_field"] == "later"


def test_state_is_the_closed_vocabulary() -> None:
    with pytest.raises(ValidationError):
        m.FaxJob.model_validate(fax_job(state="sent"))


def test_request_models_refuse_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        m.FaxCreate(quote_id="quote_1", priority="high")  # type: ignore[call-arg]
    body = m.QuoteCreate.model_validate({"document_id": "doc_1", "to": "+1", "from": "+2"})
    assert body.model_dump(by_alias=True, exclude_none=True) == {
        "document_id": "doc_1",
        "to": "+1",
        "from": "+2",
    }


def test_money_fields_are_integers() -> None:
    q = m.Balance(environment="test", currency="USD", available_micro=39000, held_micro=0)
    assert isinstance(q.available_micro, int)
    with pytest.raises(ValidationError):
        m.Balance(environment="test", currency="USD", available_micro=0.039, held_micro=0)  # type: ignore[arg-type]


def test_models_match_the_spec() -> None:
    out = subprocess.run([sys.executable, "scripts/gen_models.py", "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
