"""Health, LLM status, statistics refresh."""
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import config
import db
from errors import ApiError
from services import llm, stats

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    """DB round-trip, LLM status, view freshness, version. 503 when the DB is unreachable."""
    t0 = time.perf_counter()
    try:
        with db.db() as conn:
            db.fetch_one(conn, "SELECT 1 AS ok")
            refreshed = stats.refreshed_at(conn)
    except Exception:
        return JSONResponse({"status": "error", "db": "unreachable", "version": config.APP_VERSION}, status_code=503)
    return {
        "status": "ok",
        "version": config.APP_VERSION,
        "db_ms": round((time.perf_counter() - t0) * 1000, 1),
        "stats_refreshed_at": refreshed,
        "llm": {k: v for k, v in llm.status().items() if k in ("connected", "model")},
    }


@router.get("/api/llm-status")
def llm_status():
    s = llm.status()
    return {"connected": s["connected"], "status": "ok" if s["connected"] else "error",
            "model": s["model"], "last_ok_at": s["last_ok_at"]}


_last_manual_refresh = {"at": 0.0}


@router.post("/api/stats/refresh")
def refresh_stats():
    """「立即更新」: refresh the statistics view now (at most once every 10 seconds)."""
    if time.time() - _last_manual_refresh["at"] < 10:
        raise ApiError(429, "剛剛才更新過，請稍候再試", "too_soon")
    _last_manual_refresh["at"] = time.time()
    at = stats.refresh(wait=True)
    return {"stats_refreshed_at": at}
