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

import asyncio
import time
import re
import random
import base64
from collections import Counter
from urllib.parse import urlparse
from uuid import uuid4


from app.modules.web_scraper.domain.models import (
    AnalysisResult, AnalysisStatus, BatchResult, BatchStatus, PageSnapshot,
)
from app.modules.web_scraper.domain.ports import AnalysisRepository, BatchRepository, BrowserPort
from app.modules.web_scraper.application.task_queue import TaskQueue
from app.modules.web_scraper.application.extractor_utils import DataExtractor, LinkExtractor, FormExtractor, CTAExtractor

from app.core.logging import get_logger
log = get_logger(__name__)

# SLO: p99 latency < 30s per analysis | error rate < 5% | availability > 99.9%


class AnalysisService:
    """Orchestrates web-page analysis: submit -> enqueue -> scrape -> persist.

    Depends on abstractions only (BrowserPort, AnalysisRepository, BatchRepository)
    per Pillar 1 Dependency Inversion. Never instantiates infrastructure directly.

    Args:
        browser: BrowserPort implementation for page fetching.
        repository: AnalysisRepository implementation for single-job persistence.
        batch_repository: BatchRepository implementation for batch-job persistence.
        queue: TaskQueue for async background processing.
    """

    def __init__(
        self,
        browser: BrowserPort,
        repository: AnalysisRepository,
        batch_repository: BatchRepository,
        queue: TaskQueue,
    ) -> None:
        self._browser = browser
        self._repo = repository
        self._batch_repo = batch_repository
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
                extra={"error_class": type(exc).__name__, "error": str(exc)},
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

        # Raw safety cap — for storage/debugging only, NOT sent to the LLM
        MAX_RAW_CHARS = 15000
        final_text = (
            cleaned_text[:MAX_RAW_CHARS] + '.. [Content Truncated]'
            if len(cleaned_text) > MAX_RAW_CHARS else cleaned_text
        )
        
        base_insights = {
            "seo": { 
                "title": snapshot.title,
                "description": snapshot.meta.get("description") or snapshot.meta.get("og:description") or "" 
            },
            "content": { 
                "text": final_text,
                # Convert each byte string to a clean base64 string
                "screenshots": [base64.b64encode(s).decode("utf-8") for s in snapshot.screenshots]
            }
        }
        
        base_insights["leads"] = {
            "forms": FormExtractor.extract_forms(snapshot.html),
            "ctas": CTAExtractor.extract_ctas(snapshot.html),
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

    # ── Batch analysis methods ─────────────────────────────────────────────────

    async def submit_batch(
        self,
        urls: list[str],
        wait_selector: str | None = None,
        session_id: str | None = None,
        page_delay_min: float = 1.0,
        page_delay_max: float = 3.0,
    ) -> str:
        """Create a pending batch job and enqueue for background processing.

        All URLs are scraped sequentially within a single browser session to
        maintain cookie/state continuity (real-user behavior simulation).

        Args:
            urls: List of target URLs (1-5, validated at presentation layer).
            wait_selector: Optional CSS selector to await on each page.
            session_id: Optional session identifier. Auto-generated if omitted.
            page_delay_min: Minimum seconds to wait between page navigations.
            page_delay_max: Maximum seconds to wait between page navigations.

        Returns:
            Unique batch_id string for status polling.

        Raises:
            QueueFullError: Propagated from TaskQueue when at capacity.
        """
        batch_id = uuid4().hex
        effective_session_id = session_id or f"batch_{batch_id}"

        # MUTATION: create initial pending batch result
        batch = BatchResult(
            batch_id=batch_id,
            urls=urls,
            status=BatchStatus.PENDING,
            session_id=effective_session_id,
        )
        await self._batch_repo.save(batch)

        # Enqueue background work — raises QueueFullError if full (Pillar 3)
        await self._queue.enqueue(
            self._process_batch,
            batch_id,
            urls,
            wait_selector,
            effective_session_id,
            page_delay_min,
            page_delay_max,
        )

        log.info(
            "batch_submitted",
            extra={"batch_id": batch_id, "url_count": len(urls), "session_id": effective_session_id},
        )
        return batch_id

    async def get_batch(self, batch_id: str) -> BatchResult | None:
        """Retrieve a single batch result by batch_id.

        Args:
            batch_id: Unique batch identifier.

        Returns:
            BatchResult or None if not found.
        """
        return await self._batch_repo.get(batch_id)

    async def list_batches(self, limit: int = 50) -> list[BatchResult]:
        """List recent batch results ordered by creation time descending.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of BatchResult, newest first.
        """
        return await self._batch_repo.list_recent(limit=limit)

    # ── Batch background worker callback ───────────────────────────────────────

    async def _process_batch(
        self,
        batch_id: str,
        urls: list[str],
        wait_selector: str | None,
        session_id: str,
        page_delay_min: float,
        page_delay_max: float,
    ) -> None:
        """Execute the scraping + analysis pipeline for a batch of URLs.

        Scrapes each URL sequentially within the same browser session to
        maintain cookie/state continuity. Individual page failures do not
        abort the batch — partial results are captured (Pillar 6).

        Args:
            batch_id: Unique batch identifier.
            urls: Target URLs to scrape.
            wait_selector: Optional CSS selector to await on each page.
            session_id: Shared browser session identifier.
            page_delay_min: Minimum inter-page delay seconds.
            page_delay_max: Maximum inter-page delay seconds.
        """
        # MUTATION: transition to RUNNING
        batch = await self._batch_repo.get(batch_id)
        if batch is None:
            log.error("batch_not_found_for_processing", extra={"batch_id": batch_id})
            return

        batch.status = BatchStatus.RUNNING
        await self._batch_repo.save(batch)
        log.info("batch_running", extra={"batch_id": batch_id, "url_count": len(urls)})

        t0 = time.monotonic()
        page_results: list[AnalysisResult] = []
        success_count = 0
        fail_count = 0

        for idx, url in enumerate(urls):
            page_job_id = f"{batch_id}_page_{idx}"
            log.info(
                "batch_page_start",
                extra={"batch_id": batch_id, "page_index": idx, "url": url, "page_job_id": page_job_id},
            )

            page_result = AnalysisResult(
                job_id=page_job_id,
                url=url,
                status=AnalysisStatus.RUNNING,
            )

            try:
                page_t0 = time.monotonic()
                # DRY reuse: same BrowserPort.fetch() with shared session_id
                snapshot = await self._browser.fetch(
                    url, wait_selector=wait_selector, session_id=session_id,
                )
                page_elapsed_ms = (time.monotonic() - page_t0) * 1000

                # DRY reuse: same _extract_insights() as single-page pipeline
                page_result.snapshot = snapshot
                page_result.insights = self._extract_insights(snapshot)
                page_result.duration_ms = round(page_elapsed_ms, 2)
                page_result.status = AnalysisStatus.COMPLETED
                success_count += 1

                log.info(
                    "batch_page_completed",
                    extra={"batch_id": batch_id, "page_index": idx, "duration_ms": page_result.duration_ms},
                )

            except Exception as exc:
                page_result.status = AnalysisStatus.FAILED
                page_result.error = f"{type(exc).__name__}: {exc}"
                fail_count += 1

                log.error(
                    "batch_page_failed",
                    extra={
                        "batch_id": batch_id,
                        "page_index": idx,
                        "error_class": type(exc).__name__,
                        "error": str(exc),
                    },
                    exc_info=True,
                )

            page_results.append(page_result)
            # Also persist each page individually so /jobs/{job_id} works
            await self._repo.save(page_result)

            # Human behavior: delay between page navigations (skip after last page)
            if idx < len(urls) - 1:
                delay = random.uniform(page_delay_min, page_delay_max)
                log.debug(
                    "batch_inter_page_delay",
                    extra={"batch_id": batch_id, "delay_seconds": round(delay, 2)},
                )
                await asyncio.sleep(delay)

        total_elapsed_ms = (time.monotonic() - t0) * 1000

        # Determine batch status: COMPLETED | PARTIAL | FAILED
        if fail_count == 0:
            batch_status = BatchStatus.COMPLETED
        elif success_count > 0:
            batch_status = BatchStatus.PARTIAL
        else:
            batch_status = BatchStatus.FAILED

        # MUTATION: finalize batch result
        batch.results = page_results
        batch.compiled_insights = self._compile_batch_insights(page_results)
        batch.total_duration_ms = round(total_elapsed_ms, 2)
        batch.status = batch_status

        await self._batch_repo.save(batch)

        log.info(
            "batch_completed",
            extra={
                "batch_id": batch_id,
                "status": batch_status.value,
                "success_count": success_count,
                "fail_count": fail_count,
                "total_duration_ms": batch.total_duration_ms,
            },
        )

    # ── Batch insight compilation (pure, CPU-light) ────────────────────────────

    @staticmethod
    def _compile_batch_insights(results: list[AnalysisResult]) -> dict:
        """Merge per-page insights into a consolidated batch summary.

        Deduplicates leads (emails, phones, social links) across all pages.
        Each page's insights are preserved individually in the 'pages' list.

        Args:
            results: List of per-page AnalysisResult objects.

        Returns:
            Dictionary with 'pages' (per-page detail) and 'summary' (aggregated).
        """
        # O(n * m) where n = pages, m = max items per page
        pages: list[dict] = []
        # all_emails: set[str] = set()
        # all_phones: set[str] = set()
        # all_social_links: set[str] = set()
        successful = 0
        failed = 0

        for result in results:
            page_entry: dict = {
                "url": result.url,
                "status": result.status.value,
                "duration_ms": result.duration_ms,
                "job_id": result.job_id,
            }

            if result.status == AnalysisStatus.COMPLETED and result.insights:
                page_entry["seo"] = result.insights.get("seo", {})
                page_entry["content"] = result.insights.get("content", {})
                page_entry["leads"] = result.insights.get("leads", {})

                # Accumulate leads for deduplication
                # leads = result.insights.get("leads", {})
                # all_emails.update(leads.get("emails", []))
                # all_phones.update(leads.get("phones", []))
                # all_social_links.update(leads.get("social_links", []))
                successful += 1
            else:
                page_entry["error"] = result.error
                failed += 1

            pages.append(page_entry)

        return {
            "pages": pages,
            "summary": {
                "total_pages": len(results),
                "successful_pages": successful,
                "failed_pages": failed,
                # "all_emails": sorted(all_emails),
                # "all_phones": sorted(all_phones),
                # "all_social_links": sorted(all_social_links),
            },
        }

