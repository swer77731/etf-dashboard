"""HTML page routes — server-rendered Jinja2 + HTMX."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings, PROJECT_ROOT

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "app_env": settings.app_env,
        },
    )
