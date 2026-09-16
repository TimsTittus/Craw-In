import abc
import json
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, AsyncGenerator, List
from pydantic import BaseModel, Field, model_validator
import httpx

# Ollama stamps /api/ps timestamps with nanosecond precision, which
# datetime.fromisoformat rejects; trim the fraction to microseconds.
_OVERLONG_FRACTION = re.compile(r"(\.\d{6})\d+")


def _parse_ollama_timestamp(value: Any) -> Optional[datetime]:
    """Parse an Ollama timestamp, returning None if it cannot be understood."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = _OVERLONG_FRACTION.sub(r"\1", value.strip())
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_resident(entry: Dict[str, Any]) -> bool:
    """True while an /api/ps entry is still held in memory.

    Ollama keeps listing a model for a short while after its keep-alive window
    closes, so an entry with an expiry in the past is not actually loaded.
    """
    expires_at = _parse_ollama_timestamp(entry.get("expires_at"))
    if expires_at is None:
        return True
    return expires_at > datetime.now(timezone.utc)


def _matches_model(target: str, candidates: List[Optional[str]]) -> bool:
    """Match a configured model against Ollama names, tolerating a missing tag."""
    return any(
        name == target or (isinstance(name, str) and name.startswith(f"{target}:"))
        for name in candidates
    )


class LLMPingStatus(BaseModel):
    """Structured outcome of a connectivity check.

    ``ping()`` only ever returned a string, so a reachable-but-idle backend was
    indistinguishable from a live one. ``state`` carries that difference:
    ``active`` (model loaded), ``standby`` (reachable, nothing loaded) or
    ``ready`` (provider reachable, residency not applicable).
    """
    ok: bool = True
    state: str = "ready"
    model: str = ""
    detail: str = ""

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

    async def ping_status(self) -> LLMPingStatus:
        """Structured form of ping(). Providers without a residency concept are 'ready'."""
        detail = await self.ping()
        return LLMPingStatus(ok=True, state="ready", model=getattr(self, "model", ""), detail=detail)

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
            except (httpx.ConnectError, httpx.TimeoutException):
                base_url = base_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
        
        if self.model not in ["auto", ""]:
            return base_url, self.model
            
        res = await client.get(f"{base_url}/api/ps")
        res.raise_for_status()
        models = [m for m in res.json().get("models", []) if _is_resident(m)]
        if models:
            return base_url, models[0].get("name")
            
        tags = (await client.get(f"{base_url}/api/tags")).json().get("models", [])
        if tags:
            return base_url, tags[0].get("name")
            
        raise ValueError(f"No models are downloaded in Ollama at {self.base_url}. Pull one first, e.g. `ollama pull qwen2.5:7b`.")

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
                        yield json.loads(line).get("response", "")

    async def ping(self) -> str:
        return (await self.ping_status()).detail

    async def ping_status(self) -> LLMPingStatus:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                base_url, actual_model = await self._resolve_endpoint_and_model(client)
                
                # Verify the model is actually downloaded/available
                res = await client.get(f"{base_url}/api/tags")
                res.raise_for_status()
                available_models = [m.get("name") for m in res.json().get("models", [])]
                
                if not _matches_model(actual_model, available_models):
                    raise ValueError(f"Model '{actual_model}' is not downloaded in Ollama. Please run `ollama run {actual_model}` first.")
                
                # Check if it's dynamically running in memory right now. Entries whose
                # keep-alive has lapsed are still listed briefly, so they don't count.
                res_ps = await client.get(f"{base_url}/api/ps")
                res_ps.raise_for_status()
                running_models = [
                    m.get("name") for m in res_ps.json().get("models", []) if _is_resident(m)
                ]
                
                if _matches_model(actual_model, running_models):
                    return LLMPingStatus(
                        ok=True,
                        state="active",
                        model=actual_model,
                        detail=f"{actual_model} (Active in Memory)",
                    )
                
                return LLMPingStatus(
                    ok=True,
                    state="standby",
                    model=actual_model,
                    detail=f"{actual_model} is NOT running (downloaded, loads on first request)",
                )
                    
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise ValueError(f"Failed to connect to Ollama at {self.base_url}. Ensure Ollama is running and accessible.")
        except httpx.TimeoutException:
            raise ValueError(f"Ollama at {self.base_url} did not respond within 5s. It may be busy loading a model.")
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"Ollama at {self.base_url} returned HTTP {exc.response.status_code} for {exc.request.url.path}.")
        except httpx.HTTPError as exc:
            raise ValueError(f"Ollama request to {self.base_url} failed ({type(exc).__name__}: {exc}).")

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
                            chunk = json.loads(data_str)
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
        except httpx.TimeoutException:
            raise ValueError(f"API at {self.base_url} did not respond within 5s.")

def get_llm_client(config: LLMConfig) -> BaseLLMClient:
    if config.provider == "local_ollama":
        return OllamaClient(config)
    elif config.provider in ["cloud_openai", "cloud_deepseek"]:
        return OpenAIChatClient(config)
    else:
        raise ValueError(f"Unsupported LLM provider: {config.provider}")
