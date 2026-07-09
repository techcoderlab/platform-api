# Configuration Guide

The service is configured entirely via Environment Variables. On startup, these are parsed into a global `Settings` object using Pydantic.

## Environment Variables

### Application Settings
- `HOST` (default: `0.0.0.0`): The bind address for the API.
- `PORT` (default: `8000`): The port for the API.
- `LOG_LEVEL` (default: `INFO`): The logging verbosity (`DEBUG`, `INFO`, `WARN`, `ERROR`).

### Concurrency & Tuning
- `POOL_SIZE` (default: `4`): The maximum number of concurrent Playwright browser contexts allowed to run simultaneously.
- `WORKER_COUNT` (default: `4`): The number of background `asyncio` workers pulling from the task queue.
- `MAX_QUEUE_SIZE` (default: `100`): The maximum number of pending jobs allowed in the queue. If exceeded, the API will return HTTP 503.

### Resilience Configuration
- `CB_FAILURE_THRESHOLD` (default: `5`): Consecutive failures before the Circuit Breaker opens.
- `CB_RECOVERY_TIMEOUT` (default: `30`): Seconds the Circuit Breaker remains open before attempting a probe request.
- `BACKOFF_MAX_ATTEMPTS` (default: `5`): Maximum retry attempts for transient network failures.
- `BACKOFF_BASE_WAIT` (default: `2.0`): Base wait time (seconds) for exponential backoff.
- `BACKOFF_MAX_WAIT` (default: `60.0`): Maximum wait time (seconds) between retries.

### Playwright Stealth
- `USER_AGENT` (optional): If provided, overrides the randomized User-Agent pool.

## .env Example

```env
# Networking
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO

# Scaling
POOL_SIZE=8
WORKER_COUNT=8
MAX_QUEUE_SIZE=200

# Resilience
CB_FAILURE_THRESHOLD=3
CB_RECOVERY_TIMEOUT=60
BACKOFF_MAX_ATTEMPTS=3
```
