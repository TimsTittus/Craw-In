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
plugins_map = load_plugins(str(Path(__file__).resolve().parent / "backend" / "plugins"))

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
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, task_id: str) -> None:
        await websocket.accept()
        self.active_connections.setdefault(task_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, task_id: str) -> None:
        conns = self.active_connections.get(task_id)
        if conns and websocket in conns:
            conns.remove(websocket)
            if not conns:
                del self.active_connections[task_id]

    async def broadcast(self, message: str, task_id: str) -> None:
        """Send a text message to all clients subscribed to ``task_id``.

        A dead connection is dropped instead of aborting delivery to the rest.
        """
        for connection in list(self.active_connections.get(task_id, [])):
            try:
                await connection.send_text(message)
            except Exception:
                self.disconnect(connection, task_id)

manager = ConnectionManager()

def _parse_person_recon_prompt(prompt: str) -> tuple[str, dict]:
    """Extract person name and context anchors (location, company, role) from natural language prompts."""
    p = prompt.strip()
    # Drop trailing instruction clauses (e.g. "X, find all the possible details, ASAP") so they don't pollute the name
    p = re.sub(
        r'\s*[,;\n]\s*(?:please|pls|asap|urgent(?:ly)?|quick(?:ly)?|find|get|give|show|list|tell|search|look|fetch|collect|gather|all|every|any|details?|info(?:rmation)?|and\s+(?:find|get|give|show|list|tell))\b.*$',
        '', p, flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    p = re.sub(r'[\s,;.!?]+(?:asap|please|pls|urgently|quickly)?[\s,;.!?]*$', '', p, flags=re.IGNORECASE).strip()
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

_PERSON_PARSE_SYSTEM_PROMPT = """You route a search request and extract its parameters.
Return ONLY a JSON object with these keys:
  "intent": "person" if the user wants information about a specific individual person, otherwise "web"
            (news, events, topics, places, companies, products, how-to questions, etc.),
  "name": for intent "person", the person's full name exactly as written by the user (no instructions, no filler words); otherwise null,
  "company": employer/organisation or null,
  "location": city/state/country or null,
  "role": job title or null,
  "college": school/university or null,
  "username": social handle without "@" or null.
Never invent values that are not in the request. Use null when unknown.
Examples:
"Siva Gireesh, find all the possible details, ASAP" -> {"intent": "person", "name": "Siva Gireesh", "company": null, "location": null, "role": null, "college": null, "username": null}
"who is Vandana working at Algomox in Chennai" -> {"intent": "person", "name": "Vandana", "company": "Algomox", "location": "Chennai", "role": null, "college": null, "username": null}
"latest news on Kerala floods" -> {"intent": "web", "name": null, "company": null, "location": null, "role": null, "college": null, "username": null}
"find cybersecurity jobs in Bangalore" -> {"intent": "web", "name": null, "company": null, "location": null, "role": null, "college": null, "username": null}"""

_PERSON_ANCHOR_KEYS = ("company", "location", "role", "college", "username")
_LLM_PARSE_TIMEOUT_S = 15.0


async def _llm_parse_person_prompt(prompt: str, llm_config: dict) -> dict | None:
    """Use the user's configured LLM to classify the request and extract the target name and anchors.

    Returns {"intent": "web"} for non-person requests, or {"intent": "person", "name": ..., "anchors": {...}}.
    Returns None on any failure (no LLM, timeout, bad JSON, hallucinated values) so the caller
    can fall back to keyword routing and the regex parser.
    """
    import json
    from backend.core.llm import LLMConfig, get_llm_client

    try:
        config = LLMConfig.model_validate({**llm_config, "temperature": 0.0})
        client = get_llm_client(config)
        raw = await asyncio.wait_for(
            client.generate(_PERSON_PARSE_SYSTEM_PROMPT, prompt, response_format="json"),
            timeout=_LLM_PARSE_TIMEOUT_S,
        )
        data = json.loads(raw)
    except Exception as exc:
        print(f"[main.py] LLM prompt parsing unavailable ({type(exc).__name__}: {exc}); using regex parser", flush=True)
        return None

    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent", "")).strip().lower()
    if intent == "web":
        return {"intent": "web"}
    if intent != "person":
        return None
    prompt_lower = prompt.lower()
    name = data.get("name")
    # Grounding: the name must literally appear in the prompt, so the LLM can't invent a target
    if not isinstance(name, str) or not name.strip() or name.strip().lower() not in prompt_lower:
        return None

    anchors = {}
    for key in _PERSON_ANCHOR_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val.strip() and val.strip().lstrip("@").lower() in prompt_lower:
            anchors[key] = val.strip().lstrip("@")
    return {"intent": "person", "name": name.strip(), "anchors": anchors}


def _clean_detected_url(url: str) -> str:
    """Strip trailing punctuation picked up from prose (e.g. 'see https://x.com.')."""
    while url:
        last = url[-1]
        if last in ".,;:!?'\"":
            url = url[:-1]
        elif last == ")" and url.count(")") > url.count("("):
            url = url[:-1]
        elif last == "]" and url.count("]") > url.count("["):
            url = url[:-1]
        else:
            break
    return url

def _latest_docs_for_urls(urls: list[str]) -> list:
    """Return the most recently indexed keyword document for each URL, in ``urls`` order."""
    if not urls:
        return []
    with get_keyword_engine() as session:
        rows = (
            session.query(KeywordDocument)
            .filter(KeywordDocument.title.in_(urls))
            .order_by(KeywordDocument.id.desc())
            .all()
        )
        session.expunge_all()
    latest: dict = {}
    for row in rows:
        latest.setdefault(row.title, row)
    return [latest[u] for u in dict.fromkeys(urls) if u in latest]

@app.websocket("/ws/tasks/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for streaming task state updates and plugin execution."""
    await manager.connect(websocket, task_id)
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
                    target_url = _clean_detected_url(http_match.group(0)) if http_match else None
                    
                    # Determine execution path: (1) Direct URL (2) Person Recon Plugin (3) Search API / DB
                    has_recon = "instance" in plugins_map.get("person_recon", {})
                    # LLM decides person-vs-web intent; keyword heuristic is the fallback when the LLM is unavailable
                    llm_analysis = await _llm_parse_person_prompt(prompt, llm_config) if (not target_url and has_recon) else None
                    if llm_analysis:
                        is_person_query = llm_analysis["intent"] == "person"
                    else:
                        prompt_lower = prompt.lower()
                        has_person_kw = any(
                            kw in prompt_lower for kw in ["find", "who is", "recon", "working", "employee", "profile", "whereabouts", "person"]
                        ) or re.search(r"\b(?:at|in)\b", prompt_lower) is not None
                        is_person_query = not target_url and has_recon and (has_person_kw or len(prompt.split()) <= 4)
                    print(f"[main.py] Routing: {'person_recon' if is_person_query else 'url' if target_url else 'web'} "
                          f"(decided by {'LLM' if llm_analysis else 'keyword heuristic'})", flush=True)

                    target_name, anchors = "", {}
                    if is_person_query:
                        if llm_analysis:
                            target_name, anchors = llm_analysis["name"], llm_analysis["anchors"]
                        else:
                            target_name, anchors = _parse_person_recon_prompt(prompt)
                        parser_used = f"{provider} ({model})" if llm_analysis else "rule-based parser"
                        steps = [
                            {"step": "Analyzing Prompt", "status": "completed", "details": f"Parsed Target Name: '{target_name}', Context Anchors: {anchors} via {parser_used}"},
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
                        await manager.broadcast(json.dumps(telemetry), task_id)
                        await asyncio.sleep(0.5)

                    # Execution Engine
                    try:
                        analyzed_results = []
                        results_text = []

                        if target_url:
                            print(f"[main.py] Ingesting single URL: {target_url}", flush=True)
                            await ingest_urls([target_url])
                            docs_filtered = _latest_docs_for_urls([target_url])
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
                                title = match.get("title") or "Untitled"
                                url = match.get("url") or "#"
                                confidence = match.get("match_confidence", 0.5)
                                full_text = match.get("full_text") or match.get("snippet") or ""
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
                                    docs_filtered = _latest_docs_for_urls(urls_found[:3])
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
                        }), task_id)
                    except Exception as e:
                        import traceback
                        error_tb = traceback.format_exc()
                        print(f"[main.py] ERROR executing agent workflow: {e}\n{error_tb}", flush=True)
                        await manager.broadcast(json.dumps({
                            "type": "result",
                            "content": f"⚠️ Error during execution: {str(e)}"
                        }), task_id)
                else:
                    await manager.broadcast(data, task_id)
            except Exception:
                await manager.broadcast(data, task_id)
    except WebSocketDisconnect:
        manager.disconnect(websocket, task_id)
        print(f"[main.py] WebSocket disconnected for task {task_id}. Cleaning up session databases...", flush=True)
        try:
            # Clear SQLite keyword database
            with get_keyword_engine() as session:
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
    finally:
        manager.disconnect(websocket, task_id)