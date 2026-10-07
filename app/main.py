"""
PCK Pack Manager — FastAPI Application Entry Point
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.routes import router as api_router
from app.config import get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Pack Manager",
    description="CUBE Buildathon — AI Packing Verification Agent",
    version="1.0.0",
)

# API routes
app.include_router(api_router)

# Templates for operator UI
TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_DIR.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Durable storage (DB + photos + JSONL mirror) — must live on a mounted volume
# in production so restarts / code updates / redeploys never wipe inspections.
settings = get_settings()
Path(settings.storage_root).mkdir(parents=True, exist_ok=True)
Path(settings.image_storage_path).mkdir(parents=True, exist_ok=True)
Path(settings.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

from app.storage.database import init_database, count_inspections  # noqa: E402
from app.storage.durable import recover_missing_into_db, write_storage_marker  # noqa: E402

init_database()
write_storage_marker()
restored = recover_missing_into_db()
logging.getLogger(__name__).info(
    "Durable storage ready at %s (inspections=%s, recovered_from_mirror=%s)",
    Path(settings.storage_root).resolve(),
    count_inspections(),
    restored,
)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Operator UI — main page."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/inspect", response_class=HTMLResponse)
async def inspect_page(request: Request):
    """Operator UI — new inspection page."""
    return templates.TemplateResponse(request=request, name="inspect.html")


@app.get("/results/{inspection_id}", response_class=HTMLResponse)
async def results_page(request: Request, inspection_id: str):
    """Operator UI — inspection results page."""
    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={"inspection_id": inspection_id},
    )
