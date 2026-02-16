"""FastAPI server with POST /query endpoint."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from indiseek.agent.loop import AgentResult, create_agent_loop

logger = logging.getLogger(__name__)

app = FastAPI(title="Indiseek", description="Codebase research service")

# Lazy-initialized agent loop (created on first request or startup)
_agent_loop = None


def _get_agent_loop():
    global _agent_loop
    if _agent_loop is None:
        _agent_loop = create_agent_loop()
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
    try:
        agent = _get_agent_loop()
        result: AgentResult = agent.run(req.prompt)
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
        logger.exception("Agent error")
        raise HTTPException(status_code=500, detail=str(e))
