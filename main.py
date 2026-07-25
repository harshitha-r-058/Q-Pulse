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
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Q-Pulse | URL Audit Service</title>
        <style>
            :root {
                --bg: #0f172a;
                --surface: #1e293b;
                --primary: #3b82f6;
                --primary-hover: #2563eb;
                --text: #f8fafc;
                --text-muted: #94a3b8;
            }
            body {
                margin: 0;
                font-family: 'Inter', -apple-system, sans-serif;
                background: var(--bg);
                color: var(--text);
                display: flex;
                flex-direction: column;
                min-height: 100vh;
                align-items: center;
                justify-content: center;
                background-image: radial-gradient(circle at 50% -20%, #3b82f640 0%, transparent 50%);
            }
            .container {
                background: rgba(30, 41, 59, 0.7);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                padding: 3rem;
                border-radius: 1rem;
                text-align: center;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                max-width: 500px;
                width: 90%;
                margin-bottom: 2rem;
                animation: float 6s ease-in-out infinite;
            }
            @keyframes float {
                0% { transform: translateY(0px); }
                50% { transform: translateY(-10px); }
                100% { transform: translateY(0px); }
            }
            h1 { margin: 0 0 0.5rem 0; font-size: 2.5rem; letter-spacing: -1px; }
            .subtitle { color: var(--text-muted); margin-bottom: 2rem; }
            
            .audit-form { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; }
            input {
                flex: 1;
                padding: 0.75rem 1rem;
                border-radius: 0.5rem;
                border: 1px solid rgba(255,255,255,0.2);
                background: rgba(0,0,0,0.2);
                color: white;
                font-size: 1rem;
                outline: none;
                transition: border-color 0.2s;
            }
            input:focus { border-color: var(--primary); }
            button {
                background: var(--primary);
                color: white;
                border: none;
                padding: 0.75rem 1.5rem;
                border-radius: 0.5rem;
                font-weight: 600;
                cursor: pointer;
                transition: background 0.2s, transform 0.1s;
            }
            button:hover { background: var(--primary-hover); }
            button:active { transform: scale(0.98); }

            #result {
                display: none;
                background: rgba(0,0,0,0.3);
                padding: 1rem;
                border-radius: 0.5rem;
                text-align: left;
                font-size: 0.9rem;
                border-left: 4px solid var(--primary);
            }
            .up { border-left-color: #22c55e !important; }
            .down { border-left-color: #ef4444 !important; }

            footer {
                text-align: center;
                padding: 20px;
                color: var(--text-muted);
                font-size: 0.9rem;
            }
            footer a { color: var(--primary); text-decoration: none; font-weight: 500; transition: color 0.2s; }
            footer a:hover { color: #60a5fa; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Q-Pulse</h1>
            <div class="subtitle">High-Performance URL Audit Service</div>
            
            <div class="audit-form">
                <input type="url" id="urlInput" placeholder="https://example.com" required>
                <button onclick="runAudit()">Audit</button>
            </div>
            
            <div id="result"></div>
        </div>

        <footer>
            <a href="https://digitalheroesco.com">Built for Digital Heroes Training Task</a>
        </footer>

        <script>
            async function runAudit() {
                const urlInput = document.getElementById('urlInput').value;
                const resultDiv = document.getElementById('result');
                const btn = document.querySelector('button');
                
                if(!urlInput) return;
                
                btn.innerText = 'Auditing...';
                btn.disabled = true;
                resultDiv.style.display = 'block';
                resultDiv.innerHTML = '<div style="text-align:center; color:#94a3b8;">Running high-speed audit...</div>';

                try {
                    const response = await fetch('/audit', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: urlInput })
                    });
                    
                    const data = await response.json();
                    
                    if(!response.ok) {
                        resultDiv.className = 'down';
                        resultDiv.innerHTML = `<strong>Error:</strong> ${data.error || 'Invalid Request (422)'}`;
                    } else {
                        resultDiv.className = data.is_up ? 'up' : 'down';
                        resultDiv.innerHTML = `
                            <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                                <strong>Status:</strong> 
                                <span style="color:${data.is_up ? '#22c55e' : '#ef4444'}">${data.is_up ? 'ONLINE' : 'OFFLINE'}</span>
                            </div>
                            <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                                <strong>Response Time:</strong> <span>${data.response_time_ms.toFixed(2)} ms</span>
                            </div>
                            <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                                <strong>Status Code:</strong> <span>${data.status_code || 'Timeout'}</span>
                            </div>
                            <div style="display:flex; justify-content:space-between;">
                                <strong>Cached Result:</strong> 
                                <span style="color:${data.cached ? '#a855f7' : '#94a3b8'}">${data.cached ? 'Yes' : 'No'}</span>
                            </div>
                        `;
                    }
                } catch (err) {
                    resultDiv.className = 'down';
                    resultDiv.innerHTML = `<strong>Error:</strong> Network issue connecting to API`;
                }
                
                btn.innerText = 'Audit';
                btn.disabled = false;
            }
        </script>
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
