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
        if payload.query == "ping":
            ping_msg = await llm_engine.ping()
            status_msg = f"{payload.llm_config.provider} - {ping_msg}"
        
        # Injected step calling Phase 4 DAG Engine
        # plan = await planner.generate_dag(payload.query, engine=llm_engine)
        
        return {"status": "ACCEPTED", "config_active": status_msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))