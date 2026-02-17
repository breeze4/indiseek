"""FastAPI server with POST /query endpoint and dashboard."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from indiseek import config
from indiseek.agent.loop import AgentResult, create_agent_loop
from indiseek.api.dashboard import router as dashboard_router

# Configure logging on import — before anything else logs
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Indiseek", description="Codebase research service")

# CORS for dashboard dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dashboard API
app.include_router(dashboard_router, prefix="/dashboard/api")

# Lazy-initialized agent loop (created on first request or startup)
_agent_loop = None


def _get_agent_loop():
    global _agent_loop
    if _agent_loop is None:
        logger.info("Initializing agent loop...")
        t0 = time.perf_counter()
        _agent_loop = create_agent_loop()
        logger.info("Agent loop ready (%.2fs)", time.perf_counter() - t0)
    return _agent_loop


class QueryRequest(BaseModel):
    prompt: str


class EvidenceStepResponse(BaseModel):
    step: str
    detail: str


class QueryResponse(BaseModel):
    answer: str
    evidence: list[EvidenceStepResponse]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    logger.info("POST /query prompt=%r", req.prompt[:120])
    t0 = time.perf_counter()
    try:
        agent = _get_agent_loop()
        result: AgentResult = agent.run(req.prompt)
        elapsed = time.perf_counter() - t0
        logger.info(
            "Query complete: %d evidence steps, %d char answer, %.2fs total",
            len(result.evidence),
            len(result.answer),
            elapsed,
        )
        return QueryResponse(
            answer=result.answer,
            evidence=[
                EvidenceStepResponse(
                    step=f"{e.tool}({', '.join(f'{k}={v!r}' for k, v in e.args.items())})",
                    detail=e.summary,
                )
                for e in result.evidence
            ],
        )
    except Exception as e:
        logger.exception("Agent error after %.2fs", time.perf_counter() - t0)
        raise HTTPException(status_code=500, detail=str(e))


# ── Dashboard SPA static files ──
# Must be mounted after all API routes. Serves the built React app.
_dashboard_dist = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
if _dashboard_dist.is_dir():
    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dist), html=True), name="dashboard")
