"""Background loops, started once per uvicorn worker (advisory locks keep the work single).

maintenance (every 15 s): LLM probe, statistics view refresh when due, daily report batch,
                          stale upload temp files.
reports     (every 10 s): generate queued AI reports, one at a time.
"""
import logging
import threading
import time

import config
from services import ingest, llm, reports, stats

log = logging.getLogger("pixi.scheduler")

_stop = threading.Event()
_threads = []


def _maintenance():
    while not _stop.wait(15):
        for name, fn in (("llm probe", llm.probe_if_stale),
                         ("stats refresh", stats.refresh_if_due),
                         ("upload cleanup", ingest.cleanup_stale_uploads)):
            try:
                fn()
            except Exception:
                log.exception("%s failed", name)
        if config.REPORT_AUTO:
            try:
                if reports.daily_due():
                    reports.run_daily_batch()
            except Exception:
                log.exception("daily report batch failed")


def _report_worker():
    while not _stop.wait(10):
        try:
            reports.process_queue_once()
        except Exception:
            log.exception("report worker failed")


def start():
    if _threads:
        return
    _stop.clear()
    llm.probe()
    for target, name in ((_maintenance, "pixi-maintenance"), (_report_worker, "pixi-reports")):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        _threads.append(t)


def stop():
    _stop.set()
    for t in _threads:
        t.join(timeout=2)
    _threads.clear()
