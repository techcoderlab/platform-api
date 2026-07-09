# Web Analyst Microservice

Welcome to the technical documentation for the **Web Analyst Microservice**.

This system is designed as an asynchronous, highly concurrent web scraping and analysis pipeline. It is built to securely access public web data, render JavaScript-heavy applications using headless Chromium, and extract structured business intelligence for downstream LLM and data workflows.

## Documentation Index

- [Architecture & Design](architecture.md): Overview of the Domain-Driven Design (DDD) layout, concurrency models, and the internal request lifecycle.
- [API Reference](api_reference.md): Detailed schemas and examples for the HTTP REST interface.
- [Configuration Guide](configuration.md): Environment variables, stealth parameters, and resilience tuning.

## Core Philosophies

1. **Clean Architecture**: The system enforces strict separation of concerns. The domain layer has zero dependencies on the infrastructure layer.
2. **Resilience by Default**: Transient network failures and rate limits are handled gracefully via exponential backoffs and circuit breakers.
3. **Optimized for AI**: Content extraction is tailored for Large Language Models (LLMs), stripping out noise and enforcing token-safe character limits to prevent pipeline crashes.
4. **Stateful Scraping**: Support for session-based scraping across multiple steps by preserving cookies, user-agents, and proxies via an LRU cache.
