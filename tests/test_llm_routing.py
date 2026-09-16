import pytest
from pydantic import ValidationError
from backend.core.llm import LLMConfig, get_llm_client, OllamaClient, OpenAIChatClient
from unittest.mock import patch, AsyncMock

def test_validation_missing_api_key():
    with pytest.raises(ValidationError) as exc_info:
        LLMConfig(
            provider="cloud_openai",
            model="gpt-4o"
        )
    assert "api_key is required for provider cloud_openai" in str(exc_info.value)

@pytest.mark.asyncio
async def test_local_ollama_routing():
    config = LLMConfig(
        provider="local_ollama",
        model="qwen2.5:7b"
    )
    client = get_llm_client(config)
    assert isinstance(client, OllamaClient)
    assert client.base_url == "http://localhost:11434"

    from unittest.mock import MagicMock
    mock_response = MagicMock()
    mock_response.json.return_value = {"response": "mocked local response"}
    mock_response.raise_for_status = lambda: None

    mock_get_response = MagicMock()
    mock_get_response.json.return_value = {"version": "0.0.0"}
    mock_get_response.raise_for_status = lambda: None

    with patch('httpx.AsyncClient.post', return_value=mock_response) as mock_post, \
         patch('httpx.AsyncClient.get', return_value=mock_get_response) as mock_get:
        result = await client.generate(system_prompt="sys", user_prompt="user")
        assert result == "mocked local response"
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:11434/api/generate"
        assert kwargs["json"]["model"] == "qwen2.5:7b"

def test_config_provider_deepseek():
    # Verify deepseek validation too
    with pytest.raises(ValidationError):
        LLMConfig(provider="cloud_deepseek", model="deepseek-chat")

    config = LLMConfig(provider="cloud_deepseek", model="deepseek-chat", api_key="test-key")
    client = get_llm_client(config)
    assert isinstance(client, OpenAIChatClient)

# --- Ollama residency reporting -------------------------------------------------
# A reachable Ollama is not the same as a loaded model; ping_status must say which.

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import httpx
from backend.core.llm import LLMPingStatus


def _ollama_responder(ps_models, tag_models):
    """Fake httpx.AsyncClient.get dispatching on the requested Ollama endpoint."""
    def _get(url, **kwargs):
        res = MagicMock()
        res.raise_for_status = lambda: None
        if "/api/version" in url:
            res.json.return_value = {"version": "0.0.0"}
        elif "/api/ps" in url:
            res.json.return_value = {"models": ps_models}
        elif "/api/tags" in url:
            res.json.return_value = {"models": tag_models}
        else:
            res.json.return_value = {}
        return res
    return _get


def _in(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


@pytest.mark.asyncio
async def test_ping_status_reports_standby_when_model_not_loaded():
    client = get_llm_client(LLMConfig(provider="local_ollama", model="qwen2.5:7b"))
    responder = _ollama_responder(ps_models=[], tag_models=[{"name": "qwen2.5:7b"}])
    with patch('httpx.AsyncClient.get', side_effect=responder):
        status = await client.ping_status()
    assert status.state == "standby"
    assert "NOT running" in status.detail


@pytest.mark.asyncio
async def test_ping_status_reports_active_when_model_loaded():
    client = get_llm_client(LLMConfig(provider="local_ollama", model="qwen2.5:7b"))
    responder = _ollama_responder(
        ps_models=[{"name": "qwen2.5:7b", "expires_at": _in(5)}],
        tag_models=[{"name": "qwen2.5:7b"}],
    )
    with patch('httpx.AsyncClient.get', side_effect=responder):
        status = await client.ping_status()
    assert status.state == "active"
    assert status.detail == "qwen2.5:7b (Active in Memory)"


@pytest.mark.asyncio
async def test_ping_status_ignores_expired_keepalive_entry():
    """Ollama lists a model briefly after eviction; an elapsed keep-alive is not 'active'."""
    client = get_llm_client(LLMConfig(provider="local_ollama", model="qwen2.5:7b"))
    responder = _ollama_responder(
        ps_models=[{"name": "qwen2.5:7b", "expires_at": _in(-5)}],
        tag_models=[{"name": "qwen2.5:7b"}],
    )
    with patch('httpx.AsyncClient.get', side_effect=responder):
        status = await client.ping_status()
    assert status.state == "standby"


@pytest.mark.asyncio
async def test_ping_status_raises_when_ollama_unreachable():
    client = get_llm_client(LLMConfig(provider="local_ollama", model="qwen2.5:7b"))
    with patch('httpx.AsyncClient.get', side_effect=httpx.ConnectError("refused")):
        with pytest.raises(ValueError) as exc_info:
            await client.ping_status()
    assert "Failed to connect to Ollama" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ping_status_raises_when_model_not_downloaded():
    client = get_llm_client(LLMConfig(provider="local_ollama", model="llama3:70b"))
    responder = _ollama_responder(ps_models=[], tag_models=[{"name": "qwen2.5:7b"}])
    with patch('httpx.AsyncClient.get', side_effect=responder):
        with pytest.raises(ValueError) as exc_info:
            await client.ping_status()
    assert "not downloaded" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ping_string_still_matches_status_detail():
    """ping() keeps its original str contract for existing callers."""
    client = get_llm_client(LLMConfig(provider="local_ollama", model="qwen2.5:7b"))
    responder = _ollama_responder(
        ps_models=[{"name": "qwen2.5:7b", "expires_at": _in(5)}],
        tag_models=[{"name": "qwen2.5:7b"}],
    )
    with patch('httpx.AsyncClient.get', side_effect=responder):
        assert await client.ping() == "qwen2.5:7b (Active in Memory)"


@pytest.mark.asyncio
async def test_cloud_provider_ping_status_defaults_to_ready():
    client = get_llm_client(LLMConfig(provider="cloud_openai", model="gpt-4o", api_key="k"))
    res = MagicMock()
    res.status_code = 200
    res.raise_for_status = lambda: None
    with patch('httpx.AsyncClient.get', return_value=res):
        status = await client.ping_status()
    assert isinstance(status, LLMPingStatus)
    assert status.state == "ready"
    assert status.model == "gpt-4o"
