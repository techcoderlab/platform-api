# ── Pillar 3: Async browser pool — N contexts, non-blocking lifecycle ────────
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import random
from collections import OrderedDict

from app.modules.web_scraper.domain.ports import SessionStorePort

from playwright.async_api import (
    async_playwright, Browser, BrowserContext, Playwright,
)
from playwright_stealth import Stealth

from app.modules.web_scraper.infrastructure.logging_config import get_logger
from app.core.config import settings
from app.modules.web_scraper.infrastructure.proxy_manager import ProxyManager

log = get_logger(__name__)


@dataclass
class _PoolSlot:
    context: BrowserContext
    in_use: bool = False
    session_id: str | None = None


class BrowserPool:
    """Fixed-size pool of stealth Playwright contexts."""

    def __init__(self, settings: Settings, session_store: SessionStorePort) -> None:
        self._settings   = settings
        self._proxy_manager = ProxyManager(settings)
        self._session_store = session_store
        self._pw: Playwright | None    = None
        self._browser: Browser | None = None
        self._slots: list[_PoolSlot]   = []
        self._lock = asyncio.Lock()
        self._semaphore: asyncio.Semaphore  # set in start()
        self._active_contexts: OrderedDict[str, _PoolSlot] = OrderedDict()
        self._max_active_contexts = self._settings.MAX_ACTIVE_CONTEXTS
        self._background_tasks: set[asyncio.Task] = set()

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    
    async def start(self) -> None:
        self._pw      = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        self._semaphore = asyncio.Semaphore(self._settings.POOL_SIZE)
        for _ in range(self._settings.POOL_SIZE):
            ctx, _, _ = await self._make_context()
            self._slots.append(_PoolSlot(context=ctx))
        log.info("browser_pool_started", size=self._settings.POOL_SIZE)

    async def stop(self) -> None:
        """Gracefully close all contexts, the browser, and the Playwright instance.
        
        Uses safe closing patterns to prevent RuntimeError if the transport is already closed.
        """
        log.info("browser_pool_stopping")

        # 1. Wait for any pending background closures (e.g. from LRU eviction)
        if self._background_tasks:
            log.debug("awaiting_background_tasks", count=len(self._background_tasks))
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

        # 2. Collect all unique contexts across slots and LRU cache
        unique_contexts = set()
        for slot in self._slots:
            unique_contexts.add(slot.context)
        for slot in self._active_contexts.values():
            unique_contexts.add(slot.context)

        # 3. Close contexts concurrently
        async def _safe_close_ctx(ctx: BrowserContext):
            try:
                await ctx.close()
            except Exception:
                pass  # Ignore errors during shutdown cleanup

        if unique_contexts:
            await asyncio.gather(*(_safe_close_ctx(c) for c in unique_contexts), return_exceptions=True)

        # 4. Close browser and Playwright transport
        try:
            if self._browser:
                await self._browser.close()
        except Exception as e:
            log.warning("browser_close_failed", error=str(e))

        try:
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            log.warning("playwright_stop_failed", error=str(e))

        log.info("browser_pool_stopped")

    def _run_background(self, coro) -> None:
        """Utility to fire-and-forget a coroutine while tracking it for shutdown."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ── Context factory ───────────────────────────────────────────────────────

    async def _make_context(
        self,
        user_agent: str | None = None,
        proxy_settings: dict | None = None,
        storage_state: dict | None = None,
    ) -> tuple[BrowserContext, str, dict | None]:
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
        ]
        viewports = [
            {"width": 1920, "height": 1080},
            {"width": 1366, "height": 768},
            {"width": 1536, "height": 864},
            {"width": 1440, "height": 900},
            {"width": 2560, "height": 1440},
        ]
        
        if not user_agent:
            user_agent = getattr(self._settings, 'USER_AGENT', None) or random.choice(user_agents)

        if not proxy_settings:
            proxy_settings = self._proxy_manager.get_next_proxy()

        ctx = await self._browser.new_context(  # type: ignore[union-attr]
            viewport=random.choice(viewports),
            locale="en-US",
            timezone_id="America/New_York",
            user_agent=user_agent,
            proxy=proxy_settings,
            storage_state=storage_state,
            java_script_enabled=True,
            ignore_https_errors=False,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
        )
        # Apply stealth patches (removes navigator.webdriver, fixes HeadlessChrome, etc.)
        await Stealth().apply_stealth_async(ctx)
        return ctx, user_agent, proxy_settings

    # ── Acquire / release ─────────────────────────────────────────────────────

    @asynccontextmanager
    async def acquire_session(self, session_id: Optional[str] = None):
        # 1. Global Limit: Ensure we don't exceed POOL_SIZE concurrent operations
        await self._semaphore.acquire()
    
        ctx = None
        slot: _PoolSlot | None = None
        is_dynamic = False
        user_agent = None
        proxy_config = None

        try:
            async with self._lock:
                if not session_id:
                    # Anonymous request: use pre-allocated pool
                    slot = next((s for s in self._slots if not s.in_use), None)
                    if slot:
                        slot.in_use = True
                        ctx = slot.context
                else:
                    # Session request: check LRU cache
                    if session_id in self._active_contexts:
                        active_slot = self._active_contexts[session_id]
                        if not active_slot.in_use:
                            active_slot.in_use = True
                            slot = active_slot
                            ctx = active_slot.context
                            # Move to end to mark as recently used
                            self._active_contexts.move_to_end(session_id)
                            log.info("session_cache_hit", session_id=session_id)
                        else:
                            log.warning("session_busy_creating_temp_context", session_id=session_id)
                    else:
                        log.info("session_cache_miss", session_id=session_id)

            # 3. If context is not found in memory (or busy), create a new one
            if ctx is None:
                is_dynamic = True
                storage_state = None

                # Retrieve old state from Redis / In-Memory Store
                if session_id:
                    saved_state = await self._session_store.get_state(session_id)
                    if saved_state:
                        log.info("session_state_loaded", session_id=session_id)
                        proxy_config = saved_state.get('proxy')
                        user_agent = saved_state.get('user_agent')
                        storage_state = saved_state.get('storage_state')
                
                ctx, user_agent, proxy_config = await self._make_context(
                    user_agent=user_agent,
                    proxy_settings=proxy_config,
                    storage_state=storage_state,
                )

                # Save the new context to LRU cache in a Thread-Safe manner
                if session_id and slot is None:
                    async with self._lock:
                        # Evict oldest if full
                        if len(self._active_contexts) >= self._max_active_contexts:
                            oldest_session, oldest_slot = self._active_contexts.popitem(last=False)
                            # Close the evicted context asynchronously and track it
                            self._run_background(oldest_slot.context.close())
                        
                        slot = _PoolSlot(context=ctx, in_use=True, session_id=session_id)
                        self._active_contexts[session_id] = slot

            # 4. Scraper will do its job
            log.debug("context_acquired", session_id=session_id)
            yield ctx

        finally:
            # 5. Thread-Safe Release and Cleanup
            if slot:
                async with self._lock:
                    slot.in_use = False
            elif is_dynamic and ctx:
                # Temporary context (e.g. session was busy), close it to prevent memory leak
                self._run_background(ctx.close())
            
            # 6. Save State (Cookies etc.) for future
            if session_id and ctx:
                try:
                    current_state = await ctx.storage_state()
                    await self._session_store.save_state(session_id, {
                        'proxy': proxy_config,
                        'user_agent': user_agent,
                        'storage_state': current_state
                    }, ttl_seconds=self._settings.BROWSER_SESSION_TTL)
                    log.info("session_state_saved", session_id=session_id)
                except Exception as e:
                    log.error("failed_to_save_state", error=str(e))

            # 7. Release Global Concurrency Token
            self._semaphore.release()
            log.debug("context_released", session_id=session_id)
        