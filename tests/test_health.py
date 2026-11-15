import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_msg_root_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service_name"] == "msg-incoming-ingestor"
        assert "health_score" in data
        assert 0 <= data["health_score"] <= 100
        assert "memory_functions" in data
        assert data["memory_functions"]["loaded_count"] >= 4
        assert data["validation_test"]["passed"] is True


@pytest.mark.asyncio
async def test_msg_api_v1_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service_name"] == "msg-incoming-ingestor"
        assert "system" in data
        assert "container" in data
        assert data["validation_test"]["passed"] is True
