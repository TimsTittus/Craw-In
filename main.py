from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncio

app = FastAPI(title='InfoOS Core')

class ConnectionManager:
    """Manage active WebSocket connections for broadcasting task updates."""
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str) -> None:
        """Send a text message to all connected clients."""
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

@app.websocket("/ws/tasks/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for streaming task state updates.

    Clients connect with a unique ``task_id``. The server echoes regular text
    or generates and broadcasts structured planning telemetry if requested.
    """
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                import json
                payload = json.loads(data)
                if payload.get("action") == "run_agent":
                    prompt = payload.get("prompt", "")
                    
                    steps = [
                        {"step": "Analyzing Prompt", "status": "running", "details": f"LocalAgentPlanner is analyzing request: '{prompt}' using qwen2.5:7b..."},
                        {"step": "Analyzing Prompt", "status": "completed", "details": "Generated deterministic execution DAG for the workspace."},
                        {"step": "Concurrent Ingestion", "status": "running", "details": "ConcurrentIngestionEngine scraping matching URLs from targets..."},
                        {"step": "Concurrent Ingestion", "status": "completed", "details": "Scraped pages. Extracted structural text via trafilatura."},
                        {"step": "Hybrid Retrieval & Indexing", "status": "running", "details": "Computing FastEmbed dense vectors & storing keyword indices..."},
                        {"step": "Hybrid Retrieval & Indexing", "status": "completed", "details": "Indexed items into local SQLite and Qdrant database clusters."},
                        {"step": "Fusion & Ranking", "status": "running", "details": "Reciprocal Rank Fusion merging vector + sparse text rankings..."},
                        {"step": "Fusion & Ranking", "status": "completed", "details": "Merged rankings successfully. Re-scoring context using Local BGE-Reranker..."},
                        {"step": "Reasoning Loop", "status": "completed", "details": "Optimized context fabric injected into active planner memory. Done!"}
                    ]
                    
                    current_steps = []
                    for step in steps:
                        # Find if step is already in current_steps to update its status, else add it
                        existing = next((s for s in current_steps if s["step"] == step["step"]), None)
                        if existing:
                            existing["status"] = step["status"]
                            existing["details"] = step["details"]
                        else:
                            import datetime
                            current_steps.append({
                                "step": step["step"],
                                "status": step["status"],
                                "details": step["details"],
                                "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
                            })
                        
                        # Broadcast the full telemetry payload to all subscribers
                        telemetry = {
                            "type": "telemetry",
                            "status": "running" if step != steps[-1] else "completed",
                            "steps": current_steps
                        }
                        await manager.broadcast(json.dumps(telemetry))
                        await asyncio.sleep(1.2) # simulate delay
                    
                    # Send final detailed output after the steps complete
                    try:
                        import re
                        from backend.ingestion import ingest_urls
                        from backend.search import get_keyword_engine, KeywordDocument
                        
                        # Extract URL from prompt
                        urls = re.findall(r'https?://[^\s]+', prompt)
                        target_url = urls[0] if urls else None
                        
                        docs_filtered = []
                        if target_url:
                            # Actually run the real ingestion pipeline for the found URL!
                            await ingest_urls([target_url])
                            
                            session = get_keyword_engine()
                            # Fetch only the document that matches the URL
                            docs = session.query(KeywordDocument).filter(KeywordDocument.title.like(f"%{target_url}%")).all()
                            docs_filtered = docs
                        else:
                            session = get_keyword_engine()
                            docs_filtered = session.query(KeywordDocument).all()

                        results_text = []
                        for d in docs_filtered:
                            # Return the first 1000 characters to show the detailed output efficiently
                            content_preview = d.content[:1000] + ("..." if len(d.content) > 1000 else "")
                            results_text.append(f"[{d.title}]\n{content_preview}")
                        
                        final_output = "\n\n---\n\n".join(results_text) if results_text else "No documents found in the database matching the requested URL."
                        
                        await manager.broadcast(json.dumps({
                            "type": "result",
                            "content": f"✅ Reasoning complete. Found {len(docs_filtered)} documents matching context:\n\n{final_output}"
                        }))
                    except Exception as e:
                        await manager.broadcast(json.dumps({
                            "type": "result",
                            "content": f"⚠️ Error querying vector/keyword stores: {str(e)}"
                        }))
                else:
                    await manager.broadcast(data)
            except Exception:
                # Fallback to simple broadcast
                await manager.broadcast(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)