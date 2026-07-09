# infrastructure/session_store.py
# For now it will use a dictionary, later we can use RedisSessionStore
import time
from typing import Dict, Any, Optional
import asyncio

from app.core.logging import get_logger
log = get_logger(__name__)

class InMemorySessionStore:
    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    async def get_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        if session_id not in self._store:
            return None
            
        data = self._store[session_id]
        if time.time() > data['expires_at']:
            # Graceful cleanup agar session expire ho gaya ho
            del self._store[session_id]
            log.info("session_expired", extra={"session_id": session_id})
            return None
            
        return data['state']

    async def save_state(self, session_id: str, state: Dict[str, Any], ttl_seconds: int = 300) -> None:
        self._store[session_id] = {
            'state': state,
            'expires_at': time.time() + ttl_seconds
        }
        log.debug("session_saved", extra={"session_id": session_id, "ttl": ttl_seconds})

    # Future-proof: Background cleanup task (No memory leaks)
    async def start_cleanup_task(self):
        while True:
            await asyncio.sleep(60) # Har 1 minute baad check kare
            now = time.time()
            expired_keys = [k for k, v in self._store.items() if now > v['expires_at']]
            for k in expired_keys:
                del self._store[k]