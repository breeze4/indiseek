"""FastAPI server — app setup, CORS, router mount, SPA static files."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from indiseek import config
from indiseek.api.dashboard import router as dashboard_router

# Configure logging on import — before anything else logs
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title="Indiseek", description="Codebase research service")

# CORS for dashboard dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# All API routes under /api
app.include_router(dashboard_router, prefix="/api")

# ── Dashboard SPA static files ──
# Must be mounted after all API routes. Serves the built React app.
_dashboard_dist = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
if _dashboard_dist.is_dir():
    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dist), html=True), name="dashboard")
