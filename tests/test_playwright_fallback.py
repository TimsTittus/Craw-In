import pytest
import asyncio
from backend.crawler import ConcurrentIngestionEngine

@pytest.mark.asyncio
async def test_playwright_fallback(monkeypatch):
    import trafilatura
    # Mock trafilatura to return None so it falls back to Playwright
    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: None)
    
    engine = ConcurrentIngestionEngine()
    # https://example.com is a simple site we can use to verify Playwright works
    result = await engine.process_url("https://example.com")
    
    assert result["status"] == 200
    assert result["content"] is not None
    assert len(result["content"]) > 0
    assert "Example Domain" in result["content"]