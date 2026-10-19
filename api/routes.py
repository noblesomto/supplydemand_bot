from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from api.auth import require_dashboard_auth
from live import stats
from live.store import get_meta
from live.systems import LIVE_SYSTEMS

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"


def make_router(session_factory) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_dashboard_auth)])

    @router.get("/", response_class=HTMLResponse)
    @router.get("/dashboard", response_class=HTMLResponse)
    def dashboard():
        return TEMPLATE.read_text()

    @router.get("/api/stats")
    def api_stats(system: str = "ALL"):
        if system != "ALL" and system not in {s.label for s in LIVE_SYSTEMS}:
            raise HTTPException(404, f"unknown system {system!r}")
        with session_factory() as s:
            out = stats.build(s, system)
            out["go_live_at"] = get_meta(s, "go_live_at")
            out["last_scan_at"] = get_meta(s, "last_scan_at")
        return out

    return router
