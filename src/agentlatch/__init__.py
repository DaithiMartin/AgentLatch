"""AgentLatch — stateful queueing middleware for voice agents.

Holds a background agent's message in Redis until voice-activity-detection
reports the user has paused, then releases it into the live voice session.
"""

from agentlatch.core import AgentLatch
from agentlatch.memory import ContextInjector
from agentlatch.schemas import ResponsePayload

__version__ = "0.1.0"

__all__ = ["AgentLatch", "ContextInjector", "ResponsePayload", "__version__"]
