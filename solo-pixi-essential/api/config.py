"""Settings, read once from the environment (docker-compose passes them from .env)."""
import os

APP_VERSION = os.environ.get("APP_VERSION", "3.2.0")
APP_TZ = os.environ.get("APP_TZ", "Asia/Taipei")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://pixi:pixipass@localhost:5433/pixi_test")
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "8"))          # per uvicorn worker
SLOW_QUERY_MS = int(os.environ.get("SLOW_QUERY_MS", "500"))

# Administrator account (DB Tweak + accounts); uploader accounts live in app_users
TWEAK_USER = os.environ.get("TWEAK_USER", "pixi")
TWEAK_PASS = os.environ.get("TWEAK_PASS", "pixipass")
SESSION_TTL_HOURS = float(os.environ.get("SESSION_TTL_HOURS", "8"))

# Local LLM (llama.cpp, OpenAI-compatible). Thinking off: ~15x faster on the dense 27B model.
LLM_API_BASE = os.environ.get("LLM_API_BASE", "http://10.20.30.23:8000/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "tn8227")
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen3.8-27B-UD-Q6_K_L")
LLM_ENABLE_THINKING = os.environ.get("LLM_ENABLE_THINKING", "false").lower() == "true"
LLM_REPORT_MAX_TOKENS = int(os.environ.get("LLM_REPORT_MAX_TOKENS", "1536"))
LLM_CHAT_TIMEOUT = 60      # seconds per call; the uploader falls back to the form on timeout
LLM_REPORT_TIMEOUT = 180
LLM_STATUS_PROBE_SEC = 60

# Statistics materialized view (mv_unit_day): refreshed on this interval, and right after
# web uploads / undo / DB Tweak deletes. Desktop uploads show up within one interval.
STATS_REFRESH_SEC = int(os.environ.get("STATS_REFRESH_SEC", "300"))

# Line work hours [start, end) for the dashboard's hourly output chart (local wall-clock hours).
WORK_HOURS = (7, 19)

# AI reports: generated after each web upload and in a daily batch at this local time.
REPORT_DAILY_AT = os.environ.get("REPORT_DAILY_AT", "18:00")
REPORT_AUTO = os.environ.get("REPORT_AUTO", "true").lower() == "true"

# Shared quality thresholds — the dashboard colours and the AI report use the same values.
YIELD_GOOD = 98.0          # ≥ green
YIELD_WARN = 95.0          # ≥ amber, below → red
RETRY_GOOD = 3.0           # ≤ green
RETRY_WARN = 5.0           # ≤ amber, above → red

# Log Uploader
UPLOAD_TMP_DIR = os.environ.get("UPLOAD_TMP_DIR", "/tmp/pixi_uploads")
UPLOAD_TTL_SEC = 3600
UPLOAD_MAX_LOG_ALL_BYTES = 500 * 1024 * 1024

# Postgres advisory lock keys (shared by both uvicorn workers)
UPLOAD_LOCK_KEY = 7360001      # one web upload at a time
MIGRATION_LOCK_KEY = 7360002
STATS_REFRESH_LOCK_KEY = 7360003
REPORT_WORKER_LOCK_KEY = 7360004
DAILY_BATCH_LOCK_KEY = 7360005
REPARSE_LOCK_KEY = 7360006

WEB_DIR = os.environ.get("WEB_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
