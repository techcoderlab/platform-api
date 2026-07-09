# Business Web Scraping Service

A production-ready, asynchronous web scraping and SEO analysis microservice. Built to safely render JavaScript-heavy sites, bypass basic bot protections, and extract structured business intelligence from raw web pages.

## What Does This Project Do?

At a high level, this service acts as an intelligent proxy between your applications and the public internet. Instead of downloading raw HTML, it spins up a real browser to capture exactly what a human would see.

**Core Capabilities:**
1. **Deep Rendering**: Uses headless Chromium (via Playwright) to execute JavaScript, ensuring Single Page Applications (SPAs) and dynamic content are fully loaded.
2. **Stealth & Stateful Sessions**: Automatically strips automated browser signatures and allows reusing cookies, proxies, and context via a `session_id` for multi-step scraping.
3. **Async Orchestration**: Accepts requests instantly and processes the heavy browser rendering in a background worker queue to prevent API blocking.
4. **AI-Optimized Extraction**: Automatically parses the DOM to extract pure, actionable data (emails, phones, social links, SEO tags). Text extraction is intelligently truncated (15k limit) to prevent LLM/n8n pipeline crashes.
5. **Self-Healing & Optimized**: Wraps network requests in configurable Circuit Breakers and Exponential Backoff retries. Automatically blocks heavy media requests (images/fonts) to save bandwidth.

---

## Architectural Workflow

This project strictly follows **Clean Architecture (Domain-Driven Design)** principles, separated into four layers:

1. **Presentation Layer (FastAPI)**: Validates incoming HTTP requests and routes them to the application logic.
2. **Application Layer (Task Queue & Service)**: Manages the business use cases. It drops incoming URLs into a bounded in-memory queue, which a pool of async workers pull from to process.
3. **Infrastructure Layer (Playwright & Repository)**: Connects to the outside world. It manages a persistent pool of browser contexts to avoid the overhead of opening a new browser for every request.
4. **Domain Layer**: Holds the core business rules and types (`AnalysisResult`, `PageSnapshot`) with zero external dependencies.

---

## Usage Examples

Below are examples of how to interact with the API once it's running (`python main.py`).

### 1. Check Service Health
Verify that the service and its background workers are ready to accept traffic.
```bash
curl -X GET http://127.0.0.1:8000/healthz/live
```
**Response:** `{"status": "ok", "service": "web-analyst"}`

### 2. Submit a URL for Analysis
Submit a URL. Because scraping takes time, the API immediately returns a `job_id` rather than making you wait.

```bash
curl -X POST http://127.0.0.1:8000/analyze \
     -H "Content-Type: application/json" \
     -d '{"url": "https://news.ycombinator.com/"}'
```

**Optional:** Wait for a specific element to load or reuse an existing session:
```bash
curl -X POST http://127.0.0.1:8000/analyze \
     -H "Content-Type: application/json" \
     -d '{"url": "https://react.dev/", "wait_selector": ".nav-main", "session_id": "user_123"}'
```

**Response:**
```json
{
  "job_id": "a1b2c3d4e5f647339fadb09559aad284",
  "status": "pending",
  "poll_url": "/jobs/a1b2c3d4e5f647339fadb09559aad284"
}
```

### 3. Poll for Results
Use the `job_id` to check if your analysis is complete. You can poll this endpoint every few seconds.

```bash
curl -X GET http://127.0.0.1:8000/jobs/a1b2c3d4e5f647339fadb09559aad284
```

**Successful Response (Once Completed):**
```json
{
  "job_id": "a1b2c3d4e5f647339fadb09559aad284",
  "url": "https://react.dev/",
  "status": "completed",
  "created_at": "2026-05-14T20:55:24.697Z",
  "duration_ms": 4120.5,
  "insights": {
    "seo": {
      "title": "React – A JavaScript library for building user interfaces",
      "description": "The library for web and native user interfaces"
    },
    "content": {
      "text": "React is a library for web and native user interfaces... [Content Truncated]"
    },
    "leads": {
      "emails": ["hello@react.dev"],
      "phones": [],
      "social_links": [
        "https://twitter.com/reactjs",
        "https://github.com/facebook/react"
      ]
    }
  }
}
```

### 4. List Recent Jobs
See an overview of the last 50 scraping jobs processed by the system.
```bash
curl -X GET http://127.0.0.1:8000/jobs?limit=5
```
