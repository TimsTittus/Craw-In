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