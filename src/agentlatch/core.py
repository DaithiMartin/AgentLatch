"""The AgentLatch facade — the public entry point that wires the ingest path.

Constructs (or adopts) a Redis client, owns a HoldingTank, and exposes
``enqueue`` for the Receiver. ``silence_threshold`` is accepted now for a stable
signature and consumed by the Delivery Engine in a later slice.
"""

from redis.asyncio import Redis

from agentlatch.queue import HoldingTank
from agentlatch.schemas import ResponsePayload


class AgentLatch:
    """Wires the Receiver to the Holding Tank.

    Provide exactly one Redis source: a ``redis_url`` (a client is created and
    owned by this instance) or an existing ``redis_client`` (borrowed — the
    caller keeps ownership and is responsible for closing it).
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        redis_client: Redis | None = None,
        silence_threshold: float = 2.0,
        session_ttl: int = 3600,
    ) -> None:
        if (redis_url is None) == (redis_client is None):
            raise ValueError("provide exactly one of redis_url or redis_client")
        if silence_threshold <= 0:
            raise ValueError("silence_threshold must be positive")
        if session_ttl <= 0:
            raise ValueError("session_ttl must be positive")

        if redis_client is not None:
            self._redis = redis_client
            self._owns_client = False
        else:
            assert redis_url is not None  # guaranteed by the exactly-one check above
            self._redis = Redis.from_url(redis_url)
            self._owns_client = True

        self._tank = HoldingTank(self._redis, session_ttl)
        self.silence_threshold = silence_threshold
        self.session_ttl = session_ttl

    async def enqueue(self, payload: ResponsePayload) -> None:
        """Persist a payload to its session's holding queue."""
        await self._tank.push(payload.session_id, payload)

    async def aclose(self) -> None:
        """Close the Redis client, but only if this instance created it."""
        if self._owns_client:
            await self._redis.aclose()
