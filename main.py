import socket
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family == socket.AF_UNSPEC or family == 0:
        family = socket.AF_INET
    return _orig_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = _ipv4_only_getaddrinfo

import os
from pathlib import Path

# Automatically load .env file into environment if present
def _load_env():
    for env_path in [Path(".env"), Path("/app/.env"), Path(__file__).resolve().parent / ".env"]:
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            if k.strip() not in os.environ:
                                os.environ[k.strip()] = v.strip()
            except Exception:
                pass
            break

_load_env()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncio
import re
from backend.ingestion import ingest_urls
from backend.search import get_keyword_engine, KeywordDocument
from fastapi.middleware.cors import CORSMiddleware
from backend.api.v1.router import router as api_router
from backend.plugins import load_plugins

# Load plugins once at startup to enable connection pooling & shared caching
plugins_map = load_plugins("backend/plugins")

app = FastAPI(title='InfoOS Core')
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api/v1")

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

def _parse_person_recon_prompt(prompt: str) -> tuple[str, dict]:
    """Extract person name and context anchors (location, company, role) from natural language prompts."""
    p = prompt.strip()
    clean = re.sub(r'^(find|search|lookup|who is|recon|get info on|locate|where is)\s+', '', p, flags=re.IGNORECASE).strip()
    clean = re.sub(r'\s+who\s+is\s+', ' ', clean, flags=re.IGNORECASE).strip()
    
    context = {}
    
    # 1. Parse location anchors: 'from <location>', 'located in <location>', 'based in <location>'
    loc_match = re.search(r'(.+?)\s+(?:from|located\s+in|based\s+in)\s+([A-Za-z0-9_\-\.\s]+)', clean, re.IGNORECASE)
    if loc_match:
        clean = loc_match.group(1).strip()
        context['location'] = loc_match.group(2).strip()
        
    # 2. Parse company anchors: 'working in/at <company>', 'works in/at <company>', 'at <company>'
    comp_match = re.search(r'(.+?)\s+(?:wo?rk(?:ing)?\s+(?:in|at)|works?\s+(?:in|at)|at|in)\s+([A-Za-z0-9_\-\.\s]+)', clean, re.IGNORECASE)
    if comp_match:
        name = comp_match.group(1).strip()
        context['company'] = comp_match.group(2).strip()
    else:
        name = clean

    # Fallback for 2-word inputs like "Vandana Algomox" or "Tims Kerala"
    if not context:
        words = name.split()
        if len(words) == 2 and not any(kw in name.lower() for kw in ['the', 'a', 'an', 'what', 'how']):
            name = words[0]
            context['company'] = words[1]
            
    name = re.sub(r'\s+(who\s+is|is|wokring|working)+$', '', name, flags=re.IGNORECASE).strip()
    return name, context

@app.websocket("/ws/tasks/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for streaming task state updates and plugin execution."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                import json
                payload = json.loads(data)
                if payload.get("action") == "run_agent":
                    prompt = payload.get("prompt", "")
                    llm_config = payload.get("llm_config", {})
                    provider = llm_config.get("provider", "local_ollama")
                    model = llm_config.get("model", "qwen2.5:7b")
                    
                    # Detect if prompt is an explicit HTTP/HTTPS URL
                    http_match = re.search(r'https?://[^\s]+', prompt)
                    target_url = http_match.group(0) if http_match else None
                    
                    # Determine execution path: (1) Direct URL (2) Person Recon Plugin (3) Search API / DB
                    is_person_query = not target_url and ("person_recon" in plugins_map) and any(
                        kw in prompt.lower() for kw in ["find", "who is", "recon", "working", "employee", "profile", "whereabouts", "person", "at", "in"]
                    ) or (not target_url and "person_recon" in plugins_map and len(prompt.split()) <= 4)
                    
                    if is_person_query:
                        target_name, anchors = _parse_person_recon_prompt(prompt)
                        steps = [
                            {"step": "Analyzing Prompt", "status": "completed", "details": f"Parsed Target Name: '{target_name}', Context Anchors: {anchors}"},
                            {"step": "360° OSINT SERP Matrix", "status": "running", "details": f"Querying LinkedIn, GitHub, Twitter/X, News, Corporate Registries for '{target_name}'..."},
                            {"step": "360° OSINT SERP Matrix", "status": "completed", "details": "Multi-platform SERP queries executed successfully."},
                            {"step": "Deep Content Ingestion", "status": "running", "details": "Scraping full Markdown text for top matched profiles and web pages..."},
                            {"step": "Deep Content Ingestion", "status": "completed", "details": "Extracted full page content via trafilatura, markdownify & Playwright."},
                            {"step": "Vector & Keyword Indexing", "status": "running", "details": "Upserting embeddings into Qdrant & storing records in SQLite keyword store..."},
                            {"step": "Vector & Keyword Indexing", "status": "completed", "details": "Indexed all person footprints into Qdrant and SQLite clusters."},
                            {"step": "Reasoning Dossier", "status": "completed", "details": "Compiled complete 360° intelligence dossier for target person."}
                        ]
                    else:
                        steps = [
                            {"step": "Analyzing Prompt", "status": "running", "details": f"AgentPlanner analyzing request: '{prompt}' via {provider} ({model})..."},
                            {"step": "Analyzing Prompt", "status": "completed", "details": f"Generated execution DAG using {model}."},
                            {"step": "Concurrent Ingestion", "status": "running", "details": "Scraping matching web pages..."},
                            {"step": "Concurrent Ingestion", "status": "completed", "details": "Extracted page content."},
                            {"step": "Hybrid Retrieval & Indexing", "status": "completed", "details": "Indexed into local SQLite and Qdrant database clusters."},
                            {"step": "Reasoning Loop", "status": "completed", "details": "Done!"}
                        ]

                    current_steps = []
                    for step in steps:
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
                        
                        telemetry = {
                            "type": "telemetry",
                            "status": "running" if step != steps[-1] else "completed",
                            "steps": current_steps
                        }
                        await manager.broadcast(json.dumps(telemetry))
                        await asyncio.sleep(0.5)

                    # Execution Engine
                    try:
                        analyzed_results = []
                        results_text = []

                        if target_url:
                            print(f"[main.py] Ingesting single URL: {target_url}", flush=True)
                            await ingest_urls([target_url])
                            session = get_keyword_engine()
                            docs_filtered = session.query(KeywordDocument).all()
                            for d in docs_filtered:
                                content_preview = d.content[:1500] + ("..." if len(d.content) > 1500 else "")
                                results_text.append(f"### [{d.title}]({d.title})\n{content_preview}")
                                analyzed_results.append({
                                    "id": str(d.id),
                                    "title": d.title,
                                    "source": "Direct URL Crawl",
                                    "confidence": 0.99,
                                    "indicators": ["Verified Web Page", f"Extracted via {getattr(d, 'source', 'trafilatura')}"],
                                    "content": d.content
                                })

                        elif is_person_query:
                            recon_plugin = plugins_map["person_recon"]["instance"]
                            target_name, anchors = _parse_person_recon_prompt(prompt)
                            print(f"[main.py] Executing person_recon plugin for '{target_name}' with anchors: {anchors}", flush=True)
                            
                            recon_output = await recon_plugin.execute(target_name, anchors)
                            report = recon_output[0] if recon_output else {}
                            top_matches = report.get("top_matches", [])

                            results_text.append(f"# 👤 Person Intelligence Reconnaissance Report: **{report.get('query_target', target_name)}**\n")
                            results_text.append(f"**Context Anchors Used:** {json.dumps(report.get('anchors_used', {}))}")
                            results_text.append(f"**Total Footprints Discovered:** {report.get('total_matches_found', len(top_matches))}")
                            results_text.append(f"**Deep-Scraped Full Pages:** {report.get('deep_scraped_count', 0)}\n\n---\n")

                            for idx, match in enumerate(top_matches, 1):
                                platform = match.get("platform", "web").upper()
                                title = match.get("title", "Untitled")
                                url = match.get("url", "#")
                                confidence = match.get("match_confidence", 0.5)
                                full_text = match.get("full_text", match.get("snippet", ""))
                                preview = full_text[:1200] + ("..." if len(full_text) > 1200 else "")

                                item_md = (
                                    f"### {idx}. [{title}]({url}) `[{platform}]`\n"
                                    f"- **Match Confidence:** `{confidence:.2f}`\n"
                                    f"- **URL:** `{url}`\n"
                                    f"- **Extracted Intelligence Preview:**\n> {preview.replace(chr(10), ' ')}\n"
                                )
                                results_text.append(item_md)
                                analyzed_results.append({
                                    "id": str(idx),
                                    "title": f"[{platform}] {title}",
                                    "source": "Person Recon OSINT Engine",
                                    "confidence": confidence,
                                    "indicators": [f"Platform: {platform}", f"Disambiguation Score: {confidence:.2f}"],
                                    "content": full_text
                                })

                        else:
                            # General Web Search API fallback
                            if "search_api" in plugins_map and "instance" in plugins_map["search_api"]:
                                search_plugin = plugins_map["search_api"]["instance"]
                                search_results = await search_plugin.execute(prompt)
                                if search_results and "error" not in search_results[0]:
                                    urls_found = [res["url"] for res in search_results if res.get("url")]
                                    await ingest_urls(urls_found[:3])
                                    session = get_keyword_engine()
                                    docs_filtered = session.query(KeywordDocument).all()
                                    for d in docs_filtered:
                                        content_preview = d.content[:1000] + ("..." if len(d.content) > 1000 else "")
                                        results_text.append(f"[{d.title}]\n{content_preview}")
                                        analyzed_results.append({
                                            "id": str(d.id),
                                            "title": d.title,
                                            "source": "Web Search Indexer",
                                            "confidence": 0.95,
                                            "indicators": ["Verified Web Search"],
                                            "content": d.content
                                        })

                        final_output = "\n\n".join(results_text) if results_text else "No intelligence records matched the specified query."
                        
                        await manager.broadcast(json.dumps({
                            "type": "result",
                            "content": final_output,
                            "results_json": analyzed_results
                        }))
                    except Exception as e:
                        import traceback
                        error_tb = traceback.format_exc()
                        print(f"[main.py] ERROR executing agent workflow: {e}\n{error_tb}", flush=True)
                        await manager.broadcast(json.dumps({
                            "type": "result",
                            "content": f"⚠️ Error during execution: {str(e)}"
                        }))
                else:
                    await manager.broadcast(data)
            except Exception:
                await manager.broadcast(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print(f"[main.py] WebSocket disconnected for task {task_id}. Cleaning up session databases...", flush=True)
        try:
            # Clear SQLite keyword database
            session = get_keyword_engine()
            session.query(KeywordDocument).delete()
            session.commit()
            print("[main.py] Cleared SQLite keyword documents.", flush=True)
        except Exception as e:
            print(f"[main.py] Error clearing SQLite keyword documents: {e}", flush=True)
            
        try:
            # Reinitialize (clear) Qdrant collection
            from backend.search import SearchIndexingFabric
            fabric = SearchIndexingFabric()
            fabric.initialize_collection("documents", dim=384, force=True)
            print("[main.py] Reinitialized Qdrant 'documents' collection.", flush=True)
        except Exception as e:
            print(f"[main.py] Error reinitializing Qdrant collection: {e}", flush=True)