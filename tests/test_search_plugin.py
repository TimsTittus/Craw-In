import pytest
import asyncio
import httpx
from unittest.mock import patch, AsyncMock, MagicMock
from backend.plugins import load_plugins
from backend.plugins.search_api_plugin.plugin import SearchApiPlugin, SimpleTTLCache
from backend.plugins.base import PluginManifest

@pytest.mark.asyncio
async def test_plugin_loading():
    plugins = load_plugins("backend/plugins")
    assert "search_api" in plugins
    assert "error" not in plugins["search_api"]
    plugin_entry = plugins["search_api"]
    assert isinstance(plugin_entry["manifest"], PluginManifest)
    assert plugin_entry["instance"].__class__.__name__ == "SearchApiPlugin"
    assert plugin_entry["manifest"].name == "search_api"

@pytest.mark.asyncio
async def test_simple_ttl_cache():
    cache = SimpleTTLCache(ttl_seconds=0.1)
    cache.set("key1", "val1")
    assert cache.get("key1") == "val1"
    
    # Wait for expiration
    await asyncio.sleep(0.15)
    assert cache.get("key1") is None

@pytest.mark.asyncio
async def test_search_api_execute_success():
    manifest = PluginManifest(
        name="search_api",
        description="test plugin",
        version="1.0"
    )
    # Pass jina_api_key to force Jina path
    plugin = SearchApiPlugin(manifest=manifest, config={"cache_ttl": 60, "jina_api_key": "test-key"})
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"title": "Success", "url": "https://ok.com", "content": "Clean md"}]
    }
    
    with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        
        # First execution
        res = await plugin.execute(query="cybersecurity jobs", context={"site": "reddit.com"})
        assert len(res) == 1
        assert res[0]["title"] == "Success"
        mock_get.assert_called_once()
        
        # Second execution (should hit cache)
        mock_get.reset_mock()
        res_cached = await plugin.execute(query="cybersecurity jobs", context={"site": "reddit.com"})
        assert res_cached == res
        mock_get.assert_not_called()
        
    await plugin.close()

@pytest.mark.asyncio
async def test_search_api_execute_retry_and_success():
    manifest = PluginManifest(
        name="search_api",
        description="test plugin",
        version="1.0"
    )
    # Pass jina_api_key to force Jina path
    plugin = SearchApiPlugin(manifest=manifest, config={"cache_ttl": 60, "jina_api_key": "test-key"})
    
    # Mocking httpx to return a 500 error twice and then 200 success
    mock_500 = MagicMock()
    mock_500.status_code = 500
    
    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.json.return_value = {"data": [{"title": "Recovered"}]}
    
    with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get, \
         patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
        # Mocking multiple return values for attempts
        mock_get.side_effect = [mock_500, mock_500, mock_200]
        
        res = await plugin.execute(query="test retry")
        assert len(res) == 1
        assert res[0]["title"] == "Recovered"
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2
        
    await plugin.close()

@pytest.mark.asyncio
async def test_search_api_auth_failure():
    manifest = PluginManifest(
        name="search_api",
        description="test plugin",
        version="1.0"
    )
    # Pass jina_api_key to force Jina path and verify auth errors are returned directly
    plugin = SearchApiPlugin(manifest=manifest, config={"jina_api_key": "invalid-key"})
    
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.json.return_value = {
        "message": "Authentication is required to use this endpoint."
    }
    
    with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_401
        
        res = await plugin.execute(query="test auth")
        assert len(res) == 1
        assert "Authentication required" in res[0]["error"]
        
    await plugin.close()

@pytest.mark.asyncio
async def test_search_api_duckduckgo_fallback_on_network_error():
    manifest = PluginManifest(
        name="search_api",
        description="test plugin",
        version="1.0"
    )
    # Jina is configured with API key but will fail due to network exception
    plugin = SearchApiPlugin(manifest=manifest, config={"jina_api_key": "test-key"})
    
    mock_ddg_response = MagicMock()
    mock_ddg_response.status_code = 200
    mock_ddg_response.text = """
    <div class="web-result">
        <a class="result__a" href="https://fallback.com">Fallback Title</a>
        <a class="result__snippet" href="https://fallback.com">Fallback Snippet</a>
    </div>
    """
    
    with patch('httpx.AsyncClient.get', side_effect=httpx.RequestError("DNS connection failed")), \
         patch('httpx.AsyncClient.post', return_callable=AsyncMock) as mock_post, \
         patch('asyncio.sleep', new_callable=AsyncMock):
        
        mock_post.return_value = mock_ddg_response
        res = await plugin.execute(query="test fallback")
        
        assert len(res) == 1
        assert res[0]["title"] == "Fallback Title"
        assert res[0]["url"] == "https://fallback.com"
        mock_post.assert_called_once()
        
    await plugin.close()

@pytest.mark.asyncio
async def test_search_api_duckduckgo_direct_no_key():
    manifest = PluginManifest(
        name="search_api",
        description="test plugin",
        version="1.0"
    )
    # No Jina API key configured: should go directly to DDG
    plugin = SearchApiPlugin(manifest=manifest)
    
    mock_ddg_response = MagicMock()
    mock_ddg_response.status_code = 200
    mock_ddg_response.text = """
    <div class="web-result">
        <a class="result__a" href="https://direct-fallback.com">Direct Title</a>
        <a class="result__snippet" href="https://direct-fallback.com">Direct Snippet</a>
    </div>
    """
    
    with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get, \
         patch('httpx.AsyncClient.post', return_callable=AsyncMock) as mock_post:
        
        mock_post.return_value = mock_ddg_response
        
        res = await plugin.execute(query="test direct ddg")
        
        assert len(res) == 1
        assert res[0]["title"] == "Direct Title"
        assert res[0]["url"] == "https://direct-fallback.com"
        
        # Jina client get should NOT be called
        mock_get.assert_not_called()
        mock_post.assert_called_once()
        
    await plugin.close()