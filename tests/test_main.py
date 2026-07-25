import pytest
from httpx import AsyncClient, ASGITransport
from main import app
import redis.asyncio as redis

# Mock Redis for testing without an active instance
class MockRedis:
    def __init__(self):
        self.data = {}
    async def get(self, key):
        return self.data.get(key)
    async def setex(self, key, ttl, value):
        self.data[key] = value

@pytest.fixture
def mock_redis(monkeypatch):
    mock = MockRedis()
    monkeypatch.setattr("main.redis_client", mock)
    return mock

@pytest.mark.asyncio
async def test_root():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    assert "Built for Digital Heroes Training Task" in response.text
    assert 'href="https://digitalheroesco.com"' in response.text

@pytest.mark.asyncio
async def test_audit_invalid_url():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/audit", json={"url": "invalid-url"})
    assert response.status_code == 422 # Unprocessable Entity (Input Validation)

@pytest.mark.asyncio
async def test_audit_valid_url(mock_redis):
    # Ensure it's a URL that exists, using httpbin for reliable responses
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/audit", json={"url": "https://httpbin.org/status/200"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["url"] == "https://httpbin.org/status/200/"
    assert data["status_code"] == 200
    assert data["is_up"] is True
    assert data["cached"] is False
    
    # Test caching behavior: The second request should be served from cache
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response2 = await ac.post("/audit", json={"url": "https://httpbin.org/status/200"})
    
    data2 = response2.json()
    assert data2["cached"] is True
