"""The Holding Tank — a Redis-backed, per-session FIFO queue of payloads.

Payloads wait here until the Delivery Engine releases them during a pause.
Backed by a real ``redis.asyncio`` list, never an in-process dict: this is a
distributed-systems primitive that must survive across workers (SPEC §6, §9).
"""

from typing import cast

from redis.asyncio import Redis

from agentlatch.schemas import ResponsePayload


class HoldingTank:
    """Stores and retrieves queued payloads for a session.

    One Redis list per session at ``agentlatch:queue:{session_id}``: ``push``
    appends (RPUSH) and refreshes the key's TTL; ``pop`` removes the oldest
    (LPOP), giving FIFO delivery. The TTL lets abandoned sessions self-clean.
    """

    def __init__(self, redis: Redis, session_ttl: int) -> None:
        self._redis = redis
        self._session_ttl = session_ttl

    @staticmethod
    def _key(session_id: str) -> str:
        return f"agentlatch:queue:{session_id}"

    async def push(self, session_id: str, payload: ResponsePayload) -> None:
        """Append a payload and (re)set the session key's TTL in one round trip."""
        key = self._key(session_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.rpush(key, payload.model_dump_json())
            pipe.expire(key, self._session_ttl)
            await pipe.execute()

    async def pop(self, session_id: str) -> ResponsePayload | None:
        """Remove and return the oldest payload, or ``None`` if the queue is empty."""
        # redis-py types lpop broadly (the with-count overload returns a list);
        # we never pass a count, so it is a single element or None.
        raw = cast(bytes | str | None, await self._redis.lpop(self._key(session_id)))
        if raw is None:
            return None
        return ResponsePayload.model_validate_json(raw)

    async def length(self, session_id: str) -> int:
        """Number of payloads currently queued for the session."""
        return int(await self._redis.llen(self._key(session_id)))
