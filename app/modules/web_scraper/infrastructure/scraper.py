# ── Infrastructure Scraper: BrowserPort impl with resilience decorators ──────
from __future__ import annotations
import asyncio
import time
import random
from typing import Any

from playwright.async_api import BrowserContext, TimeoutError as PWTimeout

from app.modules.web_scraper.domain.models import PageSnapshot
from app.modules.web_scraper.domain.ports import BrowserPort
from app.modules.web_scraper.infrastructure.browser_pool import BrowserPool
from app.modules.web_scraper.infrastructure.resilience import (
    BlockedError, RateLimitError, TransientNetworkError, NotFoundError,
    with_backoff, with_circuit_breaker,
)

from app.core.config import settings

from app.core.logging import get_logger
log = get_logger(__name__)

# ── Status-code → exception mapping ──────────────────────────────────────────
_STATUS_MAP: dict[int, type[Exception]] = {
    403: BlockedError,
    404: NotFoundError,
    429: RateLimitError,
}


class PlaywrightScraper(BrowserPort):

    def __init__(self, pool: BrowserPool) -> None:
        self._pool = pool

    # Pillar 6: circuit breaker wraps backoff
    @with_circuit_breaker(failure_threshold=settings.CB_FAILURE_THRESHOLD, recovery_timeout=settings.CB_RECOVERY_TIMEOUT)
    @with_backoff(max_attempts=settings.BACKOFF_MAX_ATTEMPTS, base_wait=settings.BACKOFF_BASE_WAIT, max_wait=settings.BACKOFF_MAX_WAIT)
    async def fetch(
        self,
        url: str,
        *,
        wait_selector: str | None = None,
        timeout_ms: int = 60_000,
        session_id: str | None = None,
    ) -> PageSnapshot:
        t0 = time.monotonic()

        # Pillar 7: bind trace fields to all log calls in this span

        async with self._pool.acquire_session(session_id) as ctx:
            page = await ctx.new_page()
            
            # Pillar 4: Proxy Cost & Bandwidth Optimization
            # Block expensive resources that aren't needed for text analysis
            await page.route("**/*", lambda route: 
                route.abort() if route.request.resource_type in ["image", "media", "font", "imageset"] 
                else route.continue_()
            )

            try:
                log.info("fetch_start")
                # Use "commit" to get headers as fast as possible (saves bandwidth if we abort early)
                response = await page.goto(
                    url,
                    wait_until="commit",
                    timeout=timeout_ms,
                )
                if response is None:
                    raise TransientNetworkError("No response object returned")

                status = response.status
                log.info("fetch_response", status=status)

                if status in _STATUS_MAP:
                    raise _STATUS_MAP[status](f"HTTP {status} from {url}")

                # If status is OK, we then wait for the full page to be ready for extraction
                await page.wait_for_load_state("load", timeout=timeout_ms)

                if wait_selector:
                    await page.wait_for_selector(wait_selector, timeout=timeout_ms)

                await self._simulate_human_behavior(page)
                snapshot = await self._build_snapshot(page, url, status)
                elapsed = (time.monotonic() - t0) * 1000
                log.info("fetch_complete", duration_ms=round(elapsed, 2))
                return snapshot

            except PWTimeout as exc:
                log.warning("fetch_timeout", error=str(exc))
                raise TransientNetworkError(str(exc)) from exc
            except (BlockedError, RateLimitError, NotFoundError):
                raise  # let resilience decorators handle (NotFoundError will not be retried)
            except Exception as exc:
                log.error("fetch_error", error=str(exc), exc_info=True)
                raise TransientNetworkError(str(exc)) from exc
            finally:
                await page.close()

    async def _simulate_human_behavior(self, page) -> None:
        """Simulate realistic human interactions (scrolling, mouse movement) to bypass bot detection."""
        viewport = page.viewport_size
        height = viewport["height"] if viewport else 1080
        
        for _ in range(random.randint(2, 4)):
            # Scroll down
            scroll_y = random.randint(100, height // 2)
            await page.mouse.wheel(delta_x=0, delta_y=scroll_y)
            await asyncio.sleep(random.uniform(0.3, 1.0))
            
            # Mouse move
            x = random.randint(100, 800)
            y = random.randint(100, height - 100)
            await page.mouse.move(x, y, steps=random.randint(5, 10))
            await asyncio.sleep(random.uniform(0.1, 0.4))

    async def _build_snapshot(self, page, url: str, status: int) -> PageSnapshot:
        """Extract structured data from live page DOM."""
        
        # Pillar 6: Handle transient "navigating" state if site redirects after 'load' event
        # We try to get content up to 3 times with a small delay before erroring to the retry decorator
        for attempt in range(3):
            try:
                title = await page.title()
                html  = await page.content()
                break
            except Exception as exc:
                err_msg = str(exc).lower()
                if "navigating" in err_msg and attempt < 2:
                    log.warning("snapshot_navigation_retry", attempt=attempt + 1, url=url)
                    await asyncio.sleep(2.0)
                    continue
                raise
        
        # Remove junk elements using JavaScript
        await page.evaluate("""() => {
            const selectorsToRemove = ['nav', 'footer', 'script', 'style', 'header', 'iframe', 'noscript', 'aside', '.ads', '.sidebar', '.footer', '.header', '.nav'];
            selectorsToRemove.forEach(s => {
                document.querySelectorAll(s).forEach(el => el.remove());
            });
        }""")
        
        # Priority order: Article > Main > Body
        # We will use evaluate to find the best element through JavaScript
        text = await page.evaluate("""() => {
            const priorityTags = ['article', 'main', '#content', '.main-content', 'body'];
            for (const selector of priorityTags) {
                const el = document.querySelector(selector);
                if (el && el.innerText.length > 200){
                    return el.innerText;
                }
            }
            return document.body.innerText;
        }""")
        
        final = page.url

        # meta tags
        meta: dict[str, str] = await page.evaluate("""() => {
            const m = {};
            document.querySelectorAll('meta[name], meta[property]').forEach(el => {
                const k = el.getAttribute('name') || el.getAttribute('property');
                const v = el.getAttribute('content');
                if (k && v) m[k] = v;
            });
            return m;
        }""")

        # unique absolute links
        links: list[str] = await page.evaluate("""() =>
            [...new Set(
                [...document.querySelectorAll('a[href]')]
                .map(a => a.href)
                .filter(h => h.startsWith('http'))
            )]
        """)

        screenshot = await page.screenshot(full_page=True, type="png")

        return PageSnapshot(
            url=url,
            final_url=final,
            status_code=status,
            html=html,
            text=text,
            title=title,
            meta=meta,
            links=links,
            screenshots=[screenshot],
        )

    async def close(self) -> None:
        pass  # pool manages lifecycle