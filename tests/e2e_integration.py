import pytest, httpx, json

@pytest.mark.asyncio
async def test_full_system_loop():
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        ws = await client.ws_connect("/ws/tasks/test")
        await ws.send_json({"action": "run_agent", "prompt": "Find the top project on https://example.com"})
        result = None
        async for msg in ws.iter_text():
            payload = json.loads(msg)
            if payload.get("type") == "result":
                result = payload["content"]
                break
        assert result is not None
        assert "timstittus" in result.lower()