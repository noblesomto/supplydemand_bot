"""Live paper scanner service: FastAPI /health + APScheduler scan job every
15 minutes, 30s after each M15 bar closes. Paper only -- never trades."""
import logging
import time
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from api.routes import make_router

from config import settings
from live.notify import Telegram
from live.scanner import Scanner
from live.store import get_meta, make_session_factory
from live.systems import LIVE_SYSTEMS
from version import __version__

logging.Formatter.converter = time.gmtime
logging.basicConfig(level=logging.INFO, format="%(asctime)s UTC %(levelname)s %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)
state: dict = {}


def _scan():
    try:
        state["scanner"].cycle()
    except Exception:  # noqa: BLE001
        log.exception("scan cycle failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core.feeds.ctrader import from_env
    sf = _sf
    state["sf"] = sf
    state["scanner"] = Scanner(sf, Telegram(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID),
                               lambda: from_env().start(),
                               workers=settings.SCAN_WORKERS)
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(_scan, "cron", minute="0,15,30,45", second=30, max_instances=1, coalesce=True)
    sched.add_job(_scan)  # one immediate cycle on startup
    sched.start()
    log.info("supplydemand_bot %s live (paper): %s", __version__, ", ".join(s.label for s in LIVE_SYSTEMS))
    yield
    sched.shutdown()
    if state["scanner"].feed is not None:
        state["scanner"].feed.stop()


app = FastAPI(title="Supply/Demand paper scanner", version=__version__, lifespan=lifespan)
_sf = make_session_factory(settings.DB_URL)
app.include_router(make_router(_sf))


@app.get("/health")
def health() -> dict:
    sc = state.get("scanner")
    last = sc.last_scan_at.isoformat() if sc and sc.last_scan_at else None
    with state["sf"]() as s:
        go_live = get_meta(s, "go_live_at")
    return {"status": "ok", "version": __version__, "last_scan_at": last, "go_live_at": go_live}


if __name__ == "__main__":
    import uvicorn
    if settings.HOST not in ("127.0.0.1", "localhost") and not (settings.DASHBOARD_USERNAME and settings.DASHBOARD_PASSWORD):
        raise SystemExit("refusing to bind a public HOST without DASHBOARD_USERNAME/DASHBOARD_PASSWORD")
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=False)
