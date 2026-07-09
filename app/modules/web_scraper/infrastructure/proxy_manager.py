import random
from typing import Optional, Dict
from app.core.config import settings, Settings

class ProxyManager:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._proxy_pool = self._settings.PROXY_LIST.split(",") if self._settings.PROXY_LIST else []

    def get_next_proxy(self) -> Optional[Dict[str, str]]:
        """
        Dynamically returns proxy credentials. 
        Decoupled from browser logic.
        """
        # Priority 1: Rotating Endpoint
        if self._settings.PROXY_ROTATING_URL:
            return self._parse_proxy_string(self._settings.PROXY_ROTATING_URL)

        # Priority 2: Manual Rotation from List
        if self._proxy_pool:
            selected = random.choice(self._proxy_pool)
            return self._parse_proxy_string(selected)

        return None

    def _parse_proxy_string(self, proxy_str: str) -> Dict[str, str]:
        # Example: http://user:pass@ip:port
        # convert for Playwright browser context
        from urllib.parse import urlparse
        parsed = urlparse(proxy_str)
        return {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username or "",
            "password": parsed.password or ""
        }