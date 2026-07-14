import os
import json
from typing import Dict, Any

try:
    import redis
except ImportError as exc:
    raise ImportError(
        "The 'redis' package is required for MemorySystemController. "
        "Add 'redis>=5.0.0' to requirements.txt and install it."
    ) from exc

# ---------------------------------------------------------------------------
# Memory System Controller – short‑term session state stored in Redis
# ---------------------------------------------------------------------------

class MemorySystemController:
    """Utility for caching and retrieving per‑session state in Redis.

    This class is intentionally lightweight: it creates a Redis client on
    construction and provides two primary methods – ``cache_session_state``
    and ``get_session_state`` – with an optional TTL (time‑to‑live).

    All payloads are JSON‑encoded to keep the stored values portable across
    languages and to guarantee deterministic deserialization.
    """

    def __init__(self, host: str = os.getenv("REDIS_HOST", "localhost"), port: int = int(os.getenv("REDIS_PORT", "6379")), db: int = int(os.getenv("REDIS_DB", "0"))):
        # Initialise the Redis client; ``decode_responses`` ensures we get strings.
        self._client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        # Optional ping to raise early if the server is unavailable.
        try:
            self._client.ping()
        except redis.exceptions.ConnectionError as err:
            raise ConnectionError(f"Unable to connect to Redis at {host}:{port}/{db}: {err}")

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def cache_session_state(self, session_id: str, payload: Dict[str, Any], ttl: int = 3600) -> None:
        """Store ``payload`` under ``session:<session_id>`` with an expiration.

        Args:
            session_id: Unique identifier for the conversational thread.
            payload: Arbitrary JSON‑serialisable mapping containing any state you
                wish to persist for the duration of the session.
            ttl: Time‑to‑live in seconds (default 1 hour). ``0`` disables expiry.
        """
        key = f"session:{session_id}"
        value = json.dumps(payload)
        if ttl > 0:
            self._client.setex(name=key, time=ttl, value=value)
        else:
            self._client.set(name=key, value=value)

    def get_session_state(self, session_id: str) -> Dict[str, Any]:
        """Retrieve the stored session payload.

        Returns an empty dict if the key does not exist or cannot be decoded.
        """
        key = f"session:{session_id}"
        raw = self._client.get(key)
        if raw is None:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Corrupted data – treat as missing.
            return {}

    # ---------------------------------------------------------------------
    # Utility helpers (optional, not required for core functionality)
    # ---------------------------------------------------------------------
    def delete_session_state(self, session_id: str) -> None:
        """Explicitly delete a session entry from Redis."""
        self._client.delete(f"session:{session_id}")

    def ttl_remaining(self, session_id: str) -> int:
        """Return remaining TTL in seconds; ``-2`` if key does not exist, ``-1`` if it has no expiry."""
        return self._client.ttl(f"session:{session_id}")

__all__ = ["MemorySystemController"]
