import json
import httpx
from typing import List, Dict, Any

class LocalAgentPlanner:
    """
    Communicates with a locally‑running Ollama LLM to generate a
    deterministic execution DAG from a free‑form user request.
    """
    def __init__(self, endpoint: str = "http://localhost:11434"):
        # Re‑use a single AsyncClient for the lifetime of the instance
        self.client = httpx.AsyncClient(base_url=endpoint, timeout=30.0)

    async def generate_dag(self, prompt: str, manifests: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Send a prompt to Ollama and receive a JSON‑encoded DAG.

        Parameters
        ----------
        prompt:
            The raw user instruction (e.g. “Find cybersecurity jobs”).
        manifests:
            List of plugin manifests (as dicts) that the model can reference.

        Returns
        -------
        dict:
            The parsed JSON response containing the execution DAG.
        """
        system_ctx = f"Given these plugins: {json.dumps(manifests)}. Emit a valid JSON array showing task paths."
        payload = {
            "model": "qwen2.5:7b",
            "prompt": f"{system_ctx}\nUser Request: {prompt}",
            "format": "json",
            "stream": False,                      # synchronous response
        }

        response = await self.client.post("/api/generate", json=payload)
        response.raise_for_status()
        # Ollama returns { "response": "<json string>" }
        raw = response.json()["response"]
        return json.loads(raw)