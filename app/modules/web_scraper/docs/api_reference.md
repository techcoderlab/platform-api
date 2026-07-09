# API Reference

## Submit Analysis Job

`POST /analyze`

Submits a URL to the asynchronous processing queue.

### Request Body

```json
{
  "url": "https://example.com",
  "wait_selector": ".main-content",
  "session_id": "optional_user_session_id"
}
```

- **`url`** *(string, required)*: The target URL to scrape.
- **`wait_selector`** *(string, optional)*: A CSS selector to wait for before extracting data. Useful for SPAs.
- **`session_id`** *(string, optional)*: An identifier to persist cookies, proxies, and user-agents across multiple requests.

### Response

Returns `HTTP 202 Accepted` immediately.

```json
{
  "job_id": "a1b2c3d4e5f647339fadb09559aad284",
  "status": "pending",
  "poll_url": "/jobs/a1b2c3d4e5f647339fadb09559aad284"
}
```

---

## Poll Job Status

`GET /jobs/{job_id}`

Retrieves the status and (if completed) the results of an analysis job.

### Response

Returns `HTTP 200 OK`.

**Pending State:**
```json
{
  "job_id": "a1b2c3d4e5f647339fadb09559aad284",
  "url": "https://example.com",
  "status": "pending",
  "created_at": "2026-05-15T12:00:00.000Z"
}
```

**Completed State:**
```json
{
  "job_id": "a1b2c3d4e5f647339fadb09559aad284",
  "url": "https://example.com",
  "status": "completed",
  "created_at": "2026-05-15T12:00:00.000Z",
  "duration_ms": 4250.5,
  "insights": {
    "seo": {
      "title": "Example Domain",
      "description": "Example description tag content."
    },
    "content": {
      "text": "This domain is for use in illustrative examples in documents. You may use this domain in literature without prior coordination or asking for permission."
    },
    "leads": {
      "emails": ["contact@example.com"],
      "phones": ["+1-555-555-5555"],
      "social_links": ["https://twitter.com/example"]
    }
  }
}
```

**Failed State:**
```json
{
  "job_id": "a1b2c3d4e5f647339fadb09559aad284",
  "url": "https://example.com",
  "status": "failed",
  "error": "NotFoundError: HTTP 404 from https://example.com",
  "created_at": "2026-05-15T12:00:00.000Z"
}
```

---

## List Recent Jobs

`GET /jobs?limit={limit}`

Returns a list of the most recent jobs processed by the system.

- **`limit`** *(integer, optional)*: Number of jobs to return. Default `50`.

### Response

Returns `HTTP 200 OK`.

```json
[
  {
    "job_id": "...",
    "url": "...",
    "status": "completed",
    ...
  }
]
```

---

## Health Checks

`GET /healthz/live`

Used by load balancers and orchestrators to determine if the process is alive.

### Response

```json
{
  "status": "ok",
  "service": "web-analyst"
}
```
