"""
Solo PIXI MP Monitor Dashboard — FastAPI entry point.

    routers/   HTTP endpoints, one module per area (analytics, auth, admin, tweak, upload, reports, system)
    services/  queries and business logic (stats view, measurements, AI reports, ingest, reparse, scheduler)
    db.py      connection pool;  config.py  settings;  errors.py  {"error", "code"} responses

Shared with the desktop tools (one directory up): module_log_parser.py, log_splitter.py.
The dashboard itself (web/) is served by nginx; StaticFiles below only covers running
uvicorn without nginx.
"""
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

_HERE = os.path.dirname(os.path.abspath(__file__))
for _path in (_HERE, os.path.dirname(_HERE)):   # api/ and the shared modules beside it
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
import errors  # noqa: E402
from routers import admin, analytics, auth, reports, system, tweak, upload  # noqa: E402
from services import migrations, scheduler  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pixi.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrations.run()
    if os.environ.get("PIXI_DISABLE_SCHEDULER") != "1":
        scheduler.start()
    log.info("Solo PIXI API %s ready", config.APP_VERSION)
    yield
    scheduler.stop()
    db.close_pool()


app = FastAPI(
    title="Solo PIXI MP Monitor Dashboard API",
    version=config.APP_VERSION,
    description="QCA9377 BT + WiFi module test analytics. Errors are `{\"error\": …, \"code\": …}`.",
    lifespan=lifespan,
)
errors.install(app)


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Response-Time-Ms"] = f"{ms:.0f}"
    path = request.url.path
    if path != "/health" and path.startswith(("/api", "/health")):
        level = logging.WARNING if ms >= config.SLOW_QUERY_MS * 2 else logging.INFO
        log.log(level, "%s %s → %s in %.0f ms", request.method, path, response.status_code, ms)
    return response


for module in (system, analytics, auth, admin, tweak, upload, reports):
    app.include_router(module.router)
app.include_router(auth.legacy)
app.include_router(tweak.jobs)

if os.path.isdir(config.WEB_DIR):
    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
