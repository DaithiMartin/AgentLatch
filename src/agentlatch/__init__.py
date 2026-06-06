"""AgentLatch — stateful queueing middleware for voice agents.

Holds a background agent's message in Redis until voice-activity-detection
reports the user has paused, then releases it into the live voice session.

Public exports (``AgentLatch``, ``ResponsePayload``, ``ContextInjector``) are
added as their modules land; see ``tasks/todo.md`` for progress.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
