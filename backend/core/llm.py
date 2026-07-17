import abc
from typing import Optional, Dict, Any, AsyncGenerator
from pydantic import BaseModel, Field, model_validator
import httpx

class LLMConfig(BaseModel):
    provider: str = Field(..., description="local_ollama, cloud_openai, cloud_anthropic, cloud_deepseek")
    model: str = Field(..., description="The explicit model identifier string")
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.2

    @model_validator(mode='after')
    def check_api_key(self):
        if self.provider in ["cloud_openai", "cloud_deepseek"] and not self.api_key:
            raise ValueError(f"api_key is required for provider {self.provider}")
        return self

class BaseLLMClient(abc.ABC):
    @abc.abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str, response_format: Optional[str] = None) -> str:
        pass

    @abc.abstractmethod
    async def stream(self, system_prompt: str, user_prompt: str) -> AsyncGenerator[str, None]:
        pass

    @abc.abstractmethod
    async def ping(self) -> str:
        pass

class OllamaClient(BaseLLMClient):
    def __init__(self, config: LLMConfig):
        self.base_url = config.base_url or "http://localhost:11434"
        self.model = config.model
        self.temperature = config.temperature

    async def _resolve_endpoint_and_model(self, client: httpx.AsyncClient) -> tuple[str, str]:
        base_url = self.base_url
        if "localhost" in base_url or "127.0.0.1" in base_url:
            try:
                await client.get(f"{base_url}/api/version", timeout=1.0)
            except httpx.ConnectError:
                base_url = base_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
        
        if self.model not in ["auto", ""]:
            return base_url, self.model
            
        res = await client.get(f"{base_url}/api/ps")
        res.raise_for_status()
        models = res.json().get("models", [])
        if models:
            return base_url, models[0].get("name")
            
        tags = (await client.get(f"{base_url}/api/tags")).json().get("models", [])
        if tags:
            return base_url, tags[0].get("name")
            
        raise ValueError("No models available in Ollama.")

    async def generate(self, system_prompt: str, user_prompt: str, response_format: Optional[str] = None) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            base_url, actual_model = await self._resolve_endpoint_and_model(client)
            payload = {
                "model": actual_model,
                "prompt": f"{system_prompt}\n\nUser: {user_prompt}",
                "stream": False,
                "options": {"temperature": self.temperature}
            }
            if response_format == "json":
                payload["format"] = "json"
                
            res = await client.post(f"{base_url}/api/generate", json=payload)
            res.raise_for_status()
            return res.json().get("response", "")

    async def stream(self, system_prompt: str, user_prompt: str) -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            base_url, actual_model = await self._resolve_endpoint_and_model(client)
            payload = {
                "model": actual_model,
                "prompt": f"{system_prompt}\n\nUser: {user_prompt}",
                "stream": True,
                "options": {"temperature": self.temperature}
            }
            async with client.stream("POST", f"{base_url}/api/generate", json=payload) as response:
                async for line in response.aiter_lines():
                    if line:
                        yield httpx.json.loads(line).get("response", "")

    async def ping(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                _, actual_model = await self._resolve_endpoint_and_model(client)
                return actual_model
        except httpx.ConnectError:
            raise ValueError(f"Failed to connect to Ollama at {self.base_url}. Ensure Ollama is running and accessible.")

class OpenAIChatClient(BaseLLMClient):
    def __init__(self, config: LLMConfig):
        self.api_key = config.api_key
        self.model = config.model
        self.base_url = config.base_url or "https://api.openai.com/v1"
        self.temperature = config.temperature

    async def generate(self, system_prompt: str, user_prompt: str, response_format: Optional[str] = None) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": self.temperature
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]

    async def stream(self, system_prompt: str, user_prompt: str) -> AsyncGenerator[str, None]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": True,
            "temperature": self.temperature
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload, headers=headers) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = httpx.json.loads(data_str)
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta:
                                yield delta
                        except Exception:
                            continue

    async def ping(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                res = await client.get(f"{self.base_url}/models", headers=headers)
                if res.status_code == 401:
                    raise ValueError(f"Unauthorized. Check your API key for {self.base_url}")
                res.raise_for_status()
                return self.model
        except httpx.ConnectError:
            raise ValueError(f"Failed to connect to API at {self.base_url}.")

def get_llm_client(config: LLMConfig) -> BaseLLMClient:
    if config.provider == "local_ollama":
        return OllamaClient(config)
    elif config.provider in ["cloud_openai", "cloud_deepseek"]:
        return OpenAIChatClient(config)
    else:
        raise ValueError(f"Unsupported LLM provider: {config.provider}")
