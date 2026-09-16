from fastapi import APIRouter, HTTPException, Depends
from backend.core.llm import LLMConfig, get_llm_client
from pydantic import BaseModel

router = APIRouter()

class GoalExecutionRequest(BaseModel):
    query: str
    llm_config: LLMConfig

@router.post("/dispatch")
async def dispatch_goal_workflow(payload: GoalExecutionRequest):
    try:
        # Resolve runtime client target binding dynamically
        llm_engine = get_llm_client(payload.llm_config)
        
        status_msg = payload.llm_config.provider
        ping_status = None
        if payload.query == "ping":
            ping = await llm_engine.ping_status()
            status_msg = f"{payload.llm_config.provider} - {ping.detail}"
            ping_status = ping.model_dump()
        
        # Injected step calling Phase 4 DAG Engine
        # plan = await planner.generate_dag(payload.query, engine=llm_engine)
        
        # `ping` carries the residency state so the UI can tell a loaded model apart
        # from one that is merely downloaded; `config_active` is unchanged.
        return {"status": "ACCEPTED", "config_active": status_msg, "ping": ping_status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))