"""Spec for ResponsePayload — the public webhook/queue payload contract.

DAMP: each case is spelled out so the contract is readable here, not inferred.
"""

import pytest
from pydantic import ValidationError

from agentlatch.schemas import ResponsePayload


def test_valid_full_payload_keeps_all_fields() -> None:
    payload = ResponsePayload(
        session_id="s1",
        text_to_speak="your order shipped",
        silent_context_update={"order_id": 42, "status": "shipped"},
    )
    assert payload.session_id == "s1"
    assert payload.text_to_speak == "your order shipped"
    assert payload.silent_context_update == {"order_id": 42, "status": "shipped"}


def test_valid_minimal_payload_defaults_optional_to_none() -> None:
    payload = ResponsePayload(session_id="s1", text_to_speak="hi")
    assert payload.silent_context_update is None


def test_surrounding_whitespace_is_stripped() -> None:
    payload = ResponsePayload(session_id="  s1  ", text_to_speak="  hi  ")
    assert payload.session_id == "s1"
    assert payload.text_to_speak == "hi"


def test_empty_session_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResponsePayload(session_id="", text_to_speak="hi")


def test_whitespace_only_session_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResponsePayload(session_id="   ", text_to_speak="hi")


def test_empty_text_to_speak_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResponsePayload(session_id="s1", text_to_speak="")


def test_whitespace_only_text_to_speak_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResponsePayload(session_id="s1", text_to_speak="   ")


def test_missing_session_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResponsePayload(text_to_speak="hi")  # type: ignore[call-arg]


def test_missing_text_to_speak_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResponsePayload(session_id="s1")  # type: ignore[call-arg]


def test_non_string_text_is_rejected() -> None:
    # pydantic v2 does not coerce int -> str.
    with pytest.raises(ValidationError):
        ResponsePayload(session_id="s1", text_to_speak=123)  # type: ignore[arg-type]


def test_unknown_field_is_rejected() -> None:
    # extra="forbid": stray top-level keys are a contract violation.
    with pytest.raises(ValidationError):
        ResponsePayload(session_id="s1", text_to_speak="hi", priority="high")  # type: ignore[call-arg]


def test_silent_context_update_must_be_a_mapping() -> None:
    with pytest.raises(ValidationError):
        ResponsePayload(
            session_id="s1",
            text_to_speak="hi",
            silent_context_update=["not", "a", "dict"],  # type: ignore[arg-type]
        )


def test_json_round_trip_is_lossless() -> None:
    original = ResponsePayload(
        session_id="s1",
        text_to_speak="hi",
        silent_context_update={"nested": {"a": [1, 2, 3]}, "flag": True},
    )
    restored = ResponsePayload.model_validate_json(original.model_dump_json())
    assert restored == original
