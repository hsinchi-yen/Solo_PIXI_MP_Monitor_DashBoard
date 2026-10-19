"""Dashboard data (public, read-only), plus planned quantities for Data Alignment."""
from typing import Dict, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import PlainTextResponse

import config
import db
from errors import bad_request, not_found
from schemas import Summary, Trend
from services import auth, measurements, stats
from services.filters import Scope, parse_scope

router = APIRouter(prefix="/api", tags=["analytics"])


def scope_params(
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD, inclusive"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD, inclusive"),
    work_order: Optional[str] = Query(None),
    year: Optional[int] = Query(None, ge=2000, le=2100, description="legacy"),
    month: Optional[int] = Query(None, ge=1, le=12, description="legacy, needs year"),
    week: Optional[int] = Query(None, ge=1, le=53, description="legacy ISO week, needs year"),
    day: Optional[str] = Query(None, description="legacy YYYY-MM-DD"),
    unit_date: Optional[str] = Query(None, description="legacy YYYY-MM-DD"),
) -> Scope:
    return parse_scope(date_from, date_to, work_order, year, month, week, day, unit_date)


def _scope_info(scope: Scope):
    return {"label": scope.describe(), "params": scope.as_params()}


@router.get("/filter-options")
def filter_options():
    """Work orders by most recent activity and the overall date range (for the filter bar)."""
    with db.db() as conn:
        return stats.cached(conn, ("filter_options",), lambda: stats.filter_options(conn))


@router.get("/overview")
def overview(scope: Scope = Depends(scope_params)):
    """Everything the 儀表板 page shows, in one request.

    Numbers, trend and PASS/FAIL split follow the selected scope. The two monthly
    charts show the whole year of that scope across all work orders, for context."""
    with db.db() as conn:
        return stats.cached(conn, ("overview", scope), lambda: _overview(conn, scope))


def _overview(conn, scope):
    summary = stats.summary(conn, scope)
    align = stats.alignment(conn, scope)
    year = stats.year_of_latest(conn, scope)
    monthly = stats.monthly(conn, Scope(), year) if year else {"year": None, "months": []}
    return {
        "scope": _scope_info(scope),
        "summary": summary,
        "aligned": {"yield_pct": align["totals"]["aligned_yield"], "gap": align["totals"]["gap"],
                    "has_target": any(r.get("target_total") is not None for r in align["items"])},
        "split": stats.pass_fail_split(conn, scope),
        "trend": stats.trend(conn, scope),
        "monthly": monthly,
        "top_causes": stats.top_fail_causes(conn, scope, 5),
        "data_as_of": stats.refreshed_at(conn),
    }


@router.get("/summary", response_model=Summary, response_model_by_alias=True)
def summary(scope: Scope = Depends(scope_params)):
    with db.db() as conn:
        return stats.cached(conn, ("summary", scope), lambda: stats.summary(conn, scope))


@router.get("/pass-fail-split")
def pass_fail_split(scope: Scope = Depends(scope_params)):
    with db.db() as conn:
        return stats.cached(conn, ("pass_fail_split", scope), lambda: stats.pass_fail_split(conn, scope))


@router.get("/trend", response_model=Trend)
def trend(scope: Scope = Depends(scope_params)):
    with db.db() as conn:
        return stats.cached(conn, ("trend", scope), lambda: stats.trend(conn, scope))


@router.get("/monthly-count")
def monthly_count(scope: Scope = Depends(scope_params), year: Optional[int] = Query(None, ge=2000, le=2100)):
    """Monthly unit counts for one year (the scope's latest year unless given)."""
    with db.db() as conn:
        y = year or stats.year_of_latest(conn, scope)
        if not y:
            return {"year": None, "months": []}
        return stats.monthly(conn, scope, y)


@router.get("/work-order-summary")
def work_order_summary(scope: Scope = Depends(scope_params)):
    with db.db() as conn:
        return stats.cached(conn, ("work_order_summary", scope), lambda: stats.work_order_summary(conn, scope))


@router.get("/retries")
def retries(scope: Scope = Depends(scope_params), limit: int = Query(200, ge=1, le=10000)):
    with db.db() as conn:
        return stats.cached(conn, ("retries", scope, limit), lambda: stats.retries(conn, scope, limit))


@router.get("/fail-analysis")
def fail_analysis(scope: Scope = Depends(scope_params), limit: int = Query(20, ge=1, le=100)):
    with db.db() as conn:
        return stats.fail_analysis(conn, scope, limit)


@router.get("/config-analysis")
def config_analysis(scope: Scope = Depends(scope_params)):
    with db.db() as conn:
        return stats.config_analysis(conn, scope)


@router.get("/fails")
def fails(scope: Scope = Depends(scope_params), page: int = Query(1, ge=1),
          page_size: int = Query(50, ge=1, le=200), search: Optional[str] = Query(None, max_length=64)):
    """FAIL / STOP test records, newest first, paged on the server."""
    with db.db() as conn:
        return stats.fails(conn, scope, page, page_size, (search or "").strip() or None)


@router.get("/measurements")
def measurement_data(scope: Scope = Depends(scope_params)):
    """量測分析 page: BT, WiFi and other distributions, already binned (a few KB)."""
    with db.db() as conn:
        return stats.cached(conn, ("measurements", scope), lambda: _measurements(conn, scope))


def _measurements(conn, scope):
    retry = stats.retries(conn, scope, limit=20)
    return {
        "scope": _scope_info(scope),
        "bt": measurements.bt(conn, scope),
        "wifi": measurements.wifi(conn, scope),
        "other": measurements.other(conn, scope, {k: retry[k] for k in ("units", "high", "medium", "low")}),
        "top_retries": retry["items"],
    }


@router.get("/alignment")
def alignment(scope: Scope = Depends(scope_params)):
    """Data Alignment: units vs planned quantity per work order, and the aligned yield."""
    with db.db() as conn:
        return stats.cached(conn, ("alignment", scope), lambda: stats.alignment(conn, scope))


@router.get("/alignment-targets")
def get_alignment_targets() -> Dict[str, int]:
    with db.db() as conn:
        rows = db.fetch_all(conn, "SELECT work_order, target_total FROM alignment_targets")
    return {r["work_order"]: r["target_total"] for r in rows}


@router.post("/alignment-targets")
def set_alignment_targets(targets: Dict[str, Optional[int]] = Body(..., examples=[{"5101-260630006": 4400}]),
                          _user=Depends(auth.current_user)):
    """Set planned quantities ({work order: number}; null clears). Requires sign-in."""
    if len(targets) > 500:
        raise bad_request("一次最多 500 張工單", "too_many")
    with db.db() as conn:
        for wo, target in targets.items():
            wo = (wo or "").strip()
            if not wo or len(wo) > 64:
                raise bad_request("工單號不正確", "bad_work_order")
            if target is None:
                db.execute(conn, "DELETE FROM alignment_targets WHERE work_order = %s", (wo,))
            elif not 0 < target <= 1_000_000:
                raise bad_request(f"{wo} 的計畫數必須是 1 到 1,000,000 的整數", "bad_target")
            else:
                db.execute(conn, """
                    INSERT INTO alignment_targets (work_order, target_total, updated_at) VALUES (%s, %s, NOW())
                    ON CONFLICT (work_order) DO UPDATE SET target_total = EXCLUDED.target_total, updated_at = NOW()
                """, (wo, target))
    return {"ok": True, "saved": len(targets)}


@router.get("/raw-log/{record_id}", response_class=PlainTextResponse)
def raw_log(record_id: int):
    """Download one test record's original log (read-only, public like the rest of the dashboard)."""
    with db.db() as conn:
        row = db.fetch_one(conn, "SELECT raw_log, source_file FROM module_test WHERE id = %s", (record_id,))
    if not row or not row["raw_log"]:
        raise not_found("這筆紀錄沒有原始 log", "no_raw_log")
    filename = (row["source_file"] or f"record_{record_id}.txt").replace('"', "")
    return PlainTextResponse(row["raw_log"], headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/meta")
def meta():
    """Version, shared thresholds and data freshness for the page header."""
    from services import llm
    with db.db() as conn:
        refreshed = stats.refreshed_at(conn)
    return {
        "version": config.APP_VERSION,
        "thresholds": {"yield_good": config.YIELD_GOOD, "yield_warn": config.YIELD_WARN,
                       "retry_good": config.RETRY_GOOD, "retry_warn": config.RETRY_WARN},
        "stats_refreshed_at": refreshed,
        "stats_refresh_sec": config.STATS_REFRESH_SEC,
        "report_daily_at": config.REPORT_DAILY_AT,
        "llm": llm.status(),
    }
