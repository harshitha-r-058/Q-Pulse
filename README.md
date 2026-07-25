# Q-Pulse URL-Audit Service

Q-Pulse is a high-performance URL-audit service built with FastAPI. It performs audits on provided URLs, capturing uptime status, response times, and caching results to prevent redundant checks.

## API Contract

### `GET /`
Returns a lightweight HTML page verifying the service is running. Includes the Digital Heroes Training Task footer.

**Response:** HTML Document (200 OK)

### `POST /audit`
Audits a specified URL. The service verifies if the URL is reachable, logs the response time, and provides standard validation.

**Request Body (JSON):**
```json
{
  "url": "https://example.com"
}
```

**Success Response (200 OK):**
```json
{
  "url": "https://example.com/",
  "status_code": 200,
  "response_time_ms": 124.5,
  "is_up": true,
  "cached": false
}
```

**Error Responses:**
- `422 Unprocessable Entity`: Invalid URL format.
- `429 Too Many Requests`: Client exceeded the rate limit.
- `500 Internal Server Error`: An unexpected server error occurred.

## Notes & Assumptions
- **Payload Limits**: Requests are assumed to have a single URL. Large batch processing is not supported on this endpoint. A single audit request is expected per payload.
- **Timeout Durations**: The HTTP client auditing URLs has a strict timeout of 5 seconds. If a URL takes longer, it is considered down.
- **Caching & Configurable Window**: The cache window is configurable via the `CACHE_WINDOW_SECONDS` environment variable (defaults to 60 seconds). We assume that within this window, the status of the target URL does not significantly change.
- **Rate Limiting**: Rate limits are set to 10 requests per minute per client IP, tracked in-memory using `slowapi`.
- **Mock Data structures**: For testing purposes, Redis caching falls back gracefully or uses mocked instances.
- **Concurrency Limiting**: A maximum of 500 concurrent audit requests are allowed at one time to prevent resource exhaustion during traffic spikes.

## AI Usage Disclosure
I used AI (Gemini) to scaffold the initial FastAPI boilerplate, generate the GitHub Actions YAML, and outline the test structure. However, I manually rewrote the rate-limiting logic to be more efficient, hand-tuned the Redis caching TTL strategies, and manually refined the error-handling responses to ensure they meet production standards.
