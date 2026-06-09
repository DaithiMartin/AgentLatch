"""Pipecat edge pipeline — the live delivery edge for the Interruption Test (CP-E).

Wires the :class:`LatchDeliveryProcessor` (T13) into a Pipecat pipeline so a held
message surfaces as TTS **only after** the user pauses > 2.0s. It polls the SAME
``SESSION_ID`` the backend posts to (plan §4.10), over the same dev Redis.

This is the **CP-E artifact** (the human-run gate). Running it live needs an
operator-chosen **transport + TTS** and their Pipecat extras + API keys — the
base sandbox venv installs only the ``FrameProcessor`` stack, which is all the
unit test (``test_frame_processor.py``) needs. So this module top-level-imports
only what's always present (``Pipeline`` + the processor); the runner/transport
are imported lazily in :func:`run_edge`.

CP-E setup (operator)::

    pip install 'pipecat-ai[webrtc,cartesia,silero]'   # your transport/TTS/VAD
    export SESSION_ID=sandbox-demo REDIS_URL=redis://localhost:6379
    # + the transport/TTS API keys, then drive run_edge() with your services.
"""

import os

from pipecat.pipeline.pipeline import Pipeline

from agentlatch import AgentLatch
from frame_processor import LatchDeliveryProcessor

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
SESSION_ID = os.environ.get("SESSION_ID", "sandbox-demo")


def build_pipeline(latch: AgentLatch, transport, tts) -> Pipeline:
    """Assemble: transport-in (with VAD) → our processor → TTS → transport-out.

    The processor sits **after** the transport input (so it sees the VAD
    ``UserStarted/StoppedSpeakingFrame``s) and **before** the TTS (so a released
    ``TextFrame`` is spoken). Exactly one processor polls this session — the
    single poll loop SPEC §3.4 requires; the receiver runs in a separate process
    and never polls.
    """
    processor = LatchDeliveryProcessor(latch, SESSION_ID)
    return Pipeline([transport.input(), processor, tts, transport.output()])


async def run_edge(transport, tts) -> None:
    """Run the live edge for CP-E with an operator-provided ``transport`` + ``tts``.

    Lazily imports the runner stack (it pulls transport/TTS extras the base venv
    omits), constructs the shared ``AgentLatch``, and runs until the session ends.
    """
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask

    latch = AgentLatch(redis_url=REDIS_URL)
    try:
        await PipelineRunner().run(PipelineTask(build_pipeline(latch, transport, tts)))
    finally:
        await latch.aclose()


if __name__ == "__main__":
    raise SystemExit(
        "edge.py is the CP-E live artifact. Install a Pipecat transport + TTS "
        "(see the module docstring), construct them with your API keys, and call "
        "run_edge(transport, tts). The poll→TextFrame logic is unit-tested in "
        "test_frame_processor.py."
    )
