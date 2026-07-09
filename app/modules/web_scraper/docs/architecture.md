# Architecture & Design

The Web Analyst Microservice adheres to **Domain-Driven Design (DDD)** principles and utilizes an **Event-Driven, Asynchronous Pipeline** to handle CPU-bound (text extraction) and I/O-bound (network scraping) workloads.

## System Layers

### 1. Presentation (`/presentation`)
The REST API boundary, built using **FastAPI**.
- **Role**: Validates incoming HTTP requests via Pydantic schemas, handles API routing, and maps Domain exceptions (e.g., `NotFoundError`) to standard HTTP status codes (e.g., 404).
- **Components**: `routes.py` exposes endpoints like `POST /analyze` and `GET /jobs/{id}`. The Presentation layer is completely unaware of the underlying scraping mechanics; it merely passes validated DTOs down to the Application layer.

### 2. Application (`/application`)
The orchestrator. It dictates *what* happens and *when*, but delegates the *how* to the Infrastructure layer.
- **`AnalysisService`**: The primary coordinator. It handles job submission, manages state transitions (PENDING → RUNNING → COMPLETED/FAILED), triggers the scraper, and persists results.
- **`TaskQueue`**: A bounded, `asyncio.Queue`-based worker pool. It absorbs traffic spikes and processes scraping jobs asynchronously across `N` background workers, preventing the FastAPI event loop from stalling.
- **`extractor_utils.py`**: Contains pure, CPU-light algorithms. Responsibilities include regex-based lead generation (emails, phones) and smart DOM truncation to prevent LLM payload overflow (hard limit of 15,000 chars).

### 3. Infrastructure (`/infrastructure`)
The outer boundary. This layer implements the interfaces defined by the Domain.
- **`BrowserPool`**: A bounded, thread-safe pool of Playwright browser contexts. It maintains a sophisticated **LRU (Least Recently Used) cache** (`_active_contexts`) for `session_id` reuse. This allows stateful scraping across requests while preventing memory bloat by safely evicting and closing old contexts.
- **`PlaywrightScraper`**: Executes headless Chromium. It applies stealth plugins to bypass basic bot protection, intelligently intercepts and aborts heavy media requests (images, fonts) to save bandwidth, and extracts DOM snapshots.
- **`resilience.py`**: A wrapper module utilizing `tenacity` (exponential backoff) and `circuitbreaker`. It ensures the service handles HTTP 429s (Rate Limits), timeouts, and network instability gracefully.
- **Data Stores**: Implements `InMemorySessionStore` and `InMemoryAnalysisRepository` for caching and persisting state locally.

### 4. Domain (`/domain`)
The core business logic.
- **Models**: Defines pure data structures (`PageSnapshot`, `AnalysisResult`, `AnalysisStatus`).
- **Ports**: Defines abstract interfaces (`BrowserPort`, `AnalysisRepository`, `SessionStorePort`). Following the Dependency Inversion Principle, inner layers only depend on these abstractions, never concrete implementations.

---

## Concurrency Model

The application leverages Python's `asyncio` event loop with strict boundaries:
1. **HTTP Request Phase**: When a client requests an analysis, the route handler instantly generates a `job_id`, saves a `PENDING` state, and pushes the task onto the `TaskQueue`. This is entirely non-blocking and returns `HTTP 202` in milliseconds.
2. **Background Processing Phase**: `WORKER_COUNT` background tasks constantly pull from the queue. 
3. **Semaphore Locking**: To prevent Playwright from consuming all available RAM, `BrowserPool` uses an `asyncio.Semaphore` limited to `POOL_SIZE`. Even if the queue has 100 jobs, only `POOL_SIZE` browsers will be active concurrently.
4. **Thread-Safe State**: Access to the LRU session cache is protected by an `asyncio.Lock` to prevent race conditions during rapid state transitions.

---

## Current Limitations

While production-ready for single-instance deployments, the current architecture has a few known limitations:

1. **Single-Node Scaling Bottleneck**: 
   - The `TaskQueue`, `InMemoryAnalysisRepository`, and `InMemorySessionStore` are entirely localized to the process memory.
   - **Impact**: The service cannot be horizontally scaled (e.g., spinning up multiple Docker containers behind a load balancer) without losing state and splitting the queue.
2. **Volatile Storage**:
   - Because all persistence is in-memory, restarting the microservice results in a total loss of job history and active sessions.
3. **CPU-Bound Extraction on the Event Loop**:
   - Heavy regex matching (`extractor_utils.py`) and large string manipulations are executed directly on the `asyncio` event loop. While fast enough for standard pages, processing massive HTML payloads could temporarily block the loop, affecting API latency.

---

## Future Upgrades & Roadmap

To evolve this service into a massive-scale, globally distributed scraping pipeline, the following upgrades are planned:

1. **Distributed Task Queue (Message Broker)**:
   - **Upgrade**: Replace the in-memory `asyncio.Queue` with **Redis Streams**, **RabbitMQ**, or **AWS SQS**. 
   - **Benefit**: Enables horizontal scaling. Multiple worker nodes can consume jobs from a single centralized queue.
2. **Persistent Databases**:
   - **Upgrade**: Implement a PostgreSQL adapter for `AnalysisRepository` and a Redis adapter for `SessionStorePort`.
   - **Benefit**: Zero data loss across restarts and shared state access across distributed workers.
3. **ProcessPoolExecutor for CPU Tasks**:
   - **Upgrade**: Offload `extractor_utils` operations to a separate process pool.
   - **Benefit**: Prevents complex regex operations from blocking the async network I/O loop.
4. **Advanced Proxy & CAPTCHA Integration**:
   - **Upgrade**: Integrate dynamic residential proxy rotation and third-party CAPTCHA solving APIs natively into the `PlaywrightScraper` lifecycle.
