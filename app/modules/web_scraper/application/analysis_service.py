# ─────────────────────────────────────────────────────
# Module   : analysis_service
# Layer    : Application
# Pillar   : P1 Architecture (use-case orchestration, DI),
#            P3 Concurrency (async processing),
#            P6 Resilience (error capture per job),
#            P7 Observability (structured logging per job)
# Complexity: submit O(1), process O(n) where n = page content size
# ─────────────────────────────────────────────────────
from __future__ import annotations

import time
import re
from collections import Counter
from urllib.parse import urlparse
from uuid import uuid4


from app.modules.web_scraper.domain.models import AnalysisResult, AnalysisStatus, PageSnapshot
from app.modules.web_scraper.domain.ports import AnalysisRepository, BrowserPort
from app.modules.web_scraper.application.task_queue import TaskQueue
from app.modules.web_scraper.application.extractor_utils import DataExtractor, LinkExtractor

from app.core.logging import get_logger
log = get_logger(__name__)

# SLO: p99 latency < 30s per analysis | error rate < 5% | availability > 99.9%


class AnalysisService:
    """Orchestrates web-page analysis: submit -> enqueue -> scrape -> persist.

    Depends on abstractions only (BrowserPort, AnalysisRepository) per Pillar 1
    Dependency Inversion. Never instantiates infrastructure directly.

    Args:
        browser: BrowserPort implementation for page fetching.
        repository: AnalysisRepository implementation for persistence.
        queue: TaskQueue for async background processing.
    """

    def __init__(
        self,
        browser: BrowserPort,
        repository: AnalysisRepository,
        queue: TaskQueue,
    ) -> None:
        self._browser = browser
        self._repo = repository
        self._queue = queue

    async def submit_analysis(
        self,
        url: str,
        wait_selector: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Create a pending analysis job and enqueue for background processing.

        Args:
            url: Target URL to analyze.
            wait_selector: Optional CSS selector to await before extraction.

        Returns:
            Unique job_id string for status polling.

        Raises:
            QueueFullError: Propagated from TaskQueue when at capacity.
        """
        job_id = uuid4().hex

        # MUTATION: create initial pending result
        result = AnalysisResult(
            job_id=job_id,
            url=url,
            status=AnalysisStatus.PENDING,
        )
        await self._repo.save(result)

        # Enqueue background work — raises QueueFullError if full (Pillar 3)
        await self._queue.enqueue(self._process_job, job_id, url, wait_selector, session_id)

        log.info("analysis_submitted", extra={"job_id": job_id, "url": url})
        return job_id

    async def get_job(self, job_id: str) -> AnalysisResult | None:
        """Retrieve a single analysis result by job_id.

        Args:
            job_id: Unique job identifier.

        Returns:
            AnalysisResult or None if not found.
        """
        return await self._repo.get(job_id)

    async def list_jobs(self, limit: int = 50) -> list[AnalysisResult]:
        """List recent analysis results ordered by creation time descending.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of AnalysisResult, newest first.
        """
        return await self._repo.list_recent(limit=limit)

    # ── Background worker callback ────────────────────────────────────────────

    async def _process_job(
        self,
        job_id: str,
        url: str,
        wait_selector: str | None,
        session_id: str | None = None,
    ) -> None:
        """Execute the scraping + analysis pipeline for a single job.

        Called by TaskQueue workers. Updates repository state through the
        PENDING -> RUNNING -> COMPLETED|FAILED lifecycle.

        Args:
            job_id: Unique job identifier.
            url: Target URL to scrape.
            wait_selector: Optional CSS selector to await.
            session_id: Optional session identifier.
        """

        # MUTATION: transition to RUNNING
        result = await self._repo.get(job_id)
        if result is None:
            log.error("job_not_found_for_processing", extra={"job_id": job_id})
            return

        result.status = AnalysisStatus.RUNNING
        await self._repo.save(result)
        log.info("analysis_running")

        try:
            t0 = time.monotonic()
            snapshot = await self._browser.fetch(url, wait_selector=wait_selector, session_id=session_id)
            elapsed_ms = (time.monotonic() - t0) * 1000

            # MUTATION: populate result with successful outcome
            result.snapshot = snapshot
            result.insights = self._extract_insights(snapshot)
            result.duration_ms = round(elapsed_ms, 2)
            result.status = AnalysisStatus.COMPLETED

            log.info("analysis_completed", extra={"duration_ms": result.duration_ms})

        except Exception as exc:
            # MUTATION: record failure
            result.status = AnalysisStatus.FAILED
            result.error = f"{type(exc).__name__}: {exc}"
            log.error(
                "analysis_failed",
                error_class=type(exc).__name__,
                error=str(exc),
                exc_info=True,
            )

        await self._repo.save(result)

    # ── Insight extraction (pure, CPU-light) ──────────────────────────────────
    @staticmethod
    def _extract_insights(snapshot: PageSnapshot) -> dict:
        """
        Optimized for AI/LLM pipelines. 
        Strips unnecessary SEO metrics and returns pure actionable data.
        """
        
        cleaned_text = re.sub(r'\n\s*\n', '\n\n', snapshot.text.strip())
        
        # Hard limit of 15,000 characters (approx 3k-4k tokens)
        # Ensures the n8n AI node does not crash
        final_text = (cleaned_text[:15000] + '.. [Content Truncated]') if len(cleaned_text) > 15000 else cleaned_text
        
        base_insights = {
            "seo": { 
                "title": snapshot.title,
                "description": snapshot.meta.get("description") or snapshot.meta.get("og:description") or "" 
            },
            "content": { 
                "text": final_text
            }
        }
        
        base_insights["leads"] = {
            "emails": DataExtractor.find_emails(snapshot.html),
            "phones": DataExtractor.find_contacts(snapshot.html),
            "social_links": LinkExtractor.find_social_links(snapshot.links),
        }
        
        return base_insights
        
    # @staticmethod
    # def _extract_insights(snapshot: PageSnapshot) -> dict:
    #     """Derive structured insights from a scraped PageSnapshot.

    #     Produces SEO signals, content metrics, and link topology.
    #     Pure function — no side effects.

    #     Args:
    #         snapshot: The scraped page data.

    #     Returns:
    #         Dictionary of computed insights.
    #     """
    #     # O(n) where n = len(text) + len(links)
    #     words = snapshot.text.split()
    #     word_count = len(words)

    #     # Classify links as internal vs external relative to target domain
    #     parsed_origin = urlparse(snapshot.url)
    #     origin_domain = parsed_origin.netloc.lower()

    #     internal_links: list[str] = []
    #     external_links: list[str] = []
    #     link_domains: list[str] = []

    #     for link in snapshot.links:
    #         parsed = urlparse(link)
    #         link_domain = parsed.netloc.lower()
    #         link_domains.append(link_domain)
    #         if link_domain == origin_domain:
    #             internal_links.append(link)
    #         else:
    #             external_links.append(link)

    #     # Top external domains — O(n) count + O(k log k) sort where k = unique domains
    #     domain_counts = Counter(link_domains)
    #     top_linked_domains = [
    #         {"domain": d, "count": c}
    #         for d, c in domain_counts.most_common(10)
    #         if d != origin_domain
    #     ]

    #     # SEO meta-tag signals
    #     meta = snapshot.meta
    #     has_description = "description" in meta or "og:description" in meta
    #     has_og_tags = any(k.startswith("og:") for k in meta)
    #     has_twitter_tags = any(k.startswith("twitter:") for k in meta)

    #     return {
    #         "content": {
    #             "title": snapshot.title,
    #             "word_count": word_count,
    #             "text_length": len(snapshot.text),
    #             "html_length": len(snapshot.html),
    #         },
    #         "seo": {
    #             "has_title": bool(snapshot.title),
    #             "has_meta_description": has_description,
    #             "has_open_graph": has_og_tags,
    #             "has_twitter_cards": has_twitter_tags,
    #             "meta_tag_count": len(meta),
    #         },
    #         "links": {
    #             "total": len(snapshot.links),
    #             "internal": len(internal_links),
    #             "external": len(external_links),
    #             "top_external_domains": top_linked_domains,
    #         },
    #         "performance": {
    #             "final_url": snapshot.final_url,
    #             "status_code": snapshot.status_code,
    #             "is_redirect": snapshot.url != snapshot.final_url,
    #             "has_screenshot": len(snapshot.screenshots) > 0,
    #         },
    #     }

