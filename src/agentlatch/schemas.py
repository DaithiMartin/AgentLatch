"""Public payload contract for AgentLatch.

``ResponsePayload`` is the single shape a background agent sends to the Receiver
and that the Delivery Engine later returns. It is a Hyrum's-Law surface: treat
its fields and validation as a stable contract.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

# Surrounding whitespace is trimmed; the value must be non-empty after trimming.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ResponsePayload(BaseModel):
    """A message queued for a session, optionally with silent context to inject.

    Unknown top-level keys are rejected (``extra="forbid"``); free-form data
    belongs inside ``silent_context_update``.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: NonEmptyStr
    text_to_speak: NonEmptyStr
    silent_context_update: dict[str, Any] | None = None
