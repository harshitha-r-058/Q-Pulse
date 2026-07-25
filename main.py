import uuid
import logging
import time
import os
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, HttpUrl
import httpx
import redis.asyncio as redis
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import asyncio

# Configure structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] request_id=%(request_id)s - %(message)s")
logger = logging.getLogger("qpulse")

# Configurable settings
CACHE_WINDOW_SECONDS = int(os.getenv("CACHE_WINDOW_SECONDS", 60))

# Redis caching setup (Defaulting to localhost for local dev/testing)
redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), encoding="utf-8", decode_responses=True)

# Rate Limiter setup (10 requests per minute per IP)
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Q-Pulse URL-Audit Service")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Concurrency limit (Semaphore) for heavy audit tasks to prevent overwhelming the service
MAX_CONCURRENT_AUDITS = 500
audit_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AUDITS)

class AuditRequest(BaseModel):
    url: HttpUrl

class AuditResponse(BaseModel):
    url: str
    status_code: Optional[int]
    response_time_ms: float
    is_up: bool
    cached: bool = False

@app.middleware("http")
async def add_request_id_and_log(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    
    # Inject request_id into logs
    old_factory = logging.getLogRecordFactory()
    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.request_id = request_id
        return record
    logging.setLogRecordFactory(record_factory)
    
    start_time = time.time()
    logger.info(f"Incoming request: {request.method} {request.url.path}")
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.info(f"Completed request: {request.method} {request.url.path} with status {response.status_code} in {process_time:.3f}s")
        return response
    except Exception as e:
        logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "Internal Server Error", "request_id": request_id})
    finally:
        logging.setLogRecordFactory(old_factory)

@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <html>
        <head>
            <title>Q-Pulse URL-Audit Service</title>
        </head>
        <body style="display: flex; flex-direction: column; min-height: 100vh; margin: 0; font-family: sans-serif;">
            <div style="flex: 1; padding: 20px; display: flex; align-items: center; justify-content: center;">
                <h1>Q-Pulse URL-Audit Service is Running</h1>
            </div>
            <footer style="text-align: center; padding: 20px;">
                <a href="https://digitalheroesco.com">Built for Digital Heroes Training Task</a>
            </footer>
        </body>
    </html>
    """

@app.post("/audit", response_model=AuditResponse)
@limiter.limit("10/minute") 
async def audit_url(request: Request, payload: AuditRequest):
    url_str = str(payload.url)
    
    # Check cache
    try:
        cached_result = await redis_client.get(url_str)
        if cached_result:
            logger.info(f"Cache hit for {url_str}")
            status_code, response_time, is_up = cached_result.split('|')
            return AuditResponse(
                url=url_str,
                status_code=int(status_code) if status_code != "None" else None,
                response_time_ms=float(response_time),
                is_up=is_up == "True",
                cached=True
            )
    except Exception as e:
        logger.warning(f"Redis cache error during GET: {str(e)}")

    logger.info(f"Auditing URL: {url_str}")
    
    # Perform Audit with timeout and concurrency limits
    async with audit_semaphore:
        start_time = time.time()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url_str)
                response_time_ms = (time.time() - start_time) * 1000
                result = AuditResponse(
                    url=url_str,
                    status_code=resp.status_code,
                    response_time_ms=response_time_ms,
                    is_up=resp.status_code < 400
                )
        except httpx.TimeoutException:
            logger.warning(f"Timeout while auditing {url_str}")
            result = AuditResponse(url=url_str, status_code=None, response_time_ms=5000.0, is_up=False)
        except httpx.RequestError as e:
            logger.warning(f"Request error for {url_str}: {str(e)}")
            result = AuditResponse(url=url_str, status_code=None, response_time_ms=(time.time() - start_time)*1000, is_up=False)

    # Cache result using the configurable CACHE_WINDOW_SECONDS
    try:
        cache_value = f"{result.status_code}|{result.response_time_ms}|{result.is_up}"
        await redis_client.setex(url_str, CACHE_WINDOW_SECONDS, cache_value)
    except Exception as e:
        logger.warning(f"Redis cache error during SET: {str(e)}")

    return result
