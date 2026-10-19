# Solo PIXI MP Monitor Dashboard - Agent Guide

## Core Development Commands

### Backend API
- Start API server: `uvicorn app:app --host 0.0.0.0 --port 8000`
- Health check: `curl http://localhost:8000/health`
- API documentation: `http://localhost:8000/docs`

### Database
- Initialize DB: Docker Compose handles schema initialization via `schema.sql`
- Reset DB: `docker compose down -v && docker compose up -d`
- Connect to DB: `psql postgresql://pixi:pixipass@localhost:5433/pixi_test`

### Docker
- Start all services: `docker compose up -d`
- Stop services: `docker compose down`
- View logs: `docker compose logs -f`

### Desktop Applications
- Log splitter: `python log_splitter_app.py`
- Log concatenate: `python log_concatenate_app.py`
- Log uploader: `python log_uploader_app.py`

### Tests
- Unit test suite: `python test_dashboard_suite.py`
- 89 tests: HTML structure, API pure functions (no DB), JS alignment logic via Node.js, Log Uploader core (`TestLogUploader`)
- Known pre-existing failures (20): donut-ring / MAC1-range / `alignGetStore` tests expect code not in the dashboard, and `TestAPIFunctions` cannot import `app.py` (needs `ai_summary_helper`/`log_splitter` on the path). New work must not add to this set.
- `TestLogUploader.test_sample_zip_matches_desktop_split_output` runs only when `rawlogs/5101-260630005.zip` and `rawlogs/5101-260630005_S/` exist (rawlogs is git-ignored)

## Environment Configuration
- Database URL: Set via `DATABASE_URL` env var (default: `postgresql://pixi:pixipass@localhost:5433/pixi_test`)
- DB Tweak credentials: Set via `TWEAK_USER` and `TWEAK_PASS` (default: `pixi`/`pixipass`)
- Local LLM: `LLM_API_BASE`, `LLM_MODEL` (default `Qwen3.8-27B-UD-Q6_K_L`), `LLM_ENABLE_THINKING` (default `false` — thinking costs ~15x latency on the dense 27B model), `LLM_REPORT_MAX_TOKENS`
- See `.env.example` for a complete template of all environment variables

## Project Structure
- `solo-pixi-essential/`: Contains FastAPI backend and dashboard
  - `api/`: FastAPI application
  - `api/app.py`: Main FastAPI application with all endpoints
  - `module_log_parser.py`: Log parsing utility used by the web Log Uploader, reparse endpoint and desktop uploader
  - `log_splitter.py`: Server-side splitter + upload validation rules (R1–R8) for the web Log Uploader; split output is byte-identical to `log_splitter_app.py`
  - `solo_pixi_dashboard.html`: Frontend dashboard (single-file, ~5000 lines)
  - `docker-compose.yml`: Service definitions for PostgreSQL, API, and Nginx
- Root level: Desktop applications, utility scripts, and test suite
  - `test_dashboard_suite.py`: Unit test suite (89 tests, no DB required)
  - `log_uploader_web_plan.md`: Design / alignment record for the web Log Uploader

## Key API Endpoints
- Health: `GET /health`
- Summary stats: `GET /api/summary`
- Yield trends: `GET /api/yield-trend`
- Fail analysis: `GET /api/fail-analysis`
- Filter options: `GET /api/filter-options`
- Work order summary: `GET /api/work-order-summary`
- Retries: `GET /api/retries`
- DB Tweak (admin): Requires `X-Tweak-Token` header (base64 encoded `user:pass`)
- Log Uploader (requires `X-Tweak-Token`):
  - `POST /api/log-upload/analyze` — multipart `file` (.zip); splits every `<station>/…/Log_All.txt` in memory, returns `upload_id` + facts (no `module_test` writes)
  - `POST /api/log-upload/validate` — `{upload_id, work_order, planned_qty}` → rule results `{issues, can_upload, needs_ack}`
  - `POST /api/log-upload/commit` — `{upload_id, work_order, planned_qty, ack_warnings, mode}` → NDJSON stream (`start`/`split`/`progress`/`done`/`error`); one upload at a time (pg advisory lock → 409)
  - `GET /api/log-upload/history`, `GET /api/log-upload/{upload_id}`, `DELETE /api/log-upload/{upload_id}` (undo a batch)

## Dashboard Pages
| Page key | Nav label | Auth required |
|---|---|---|
| `overview` | 📊 Dashboard | No |
| `workorders` | 📋 Work Orders | No |
| `fails` | ⚠️ Fail List | No |
| `bt` | 📡 BT Analysis | No |
| `wifi` | 📶 WiFi Analysis | No |
| `advanced` | 📈 Advanced Analytics | No |
| `failanalysis` | 🔍 Fail Analysis | No |
| `dbtweak` | 🔧 DB Tweak | Yes (Tweak Token) |
| `dataalign` | ⚖️ Data Alignment | No |
| `logupload` | 📤 Log Uploader | Yes (Tweak Token, shared session with DB Tweak) |

## Client-Side Storage
- `pixi-align-v1` (localStorage): JSON map of `{ [workOrder]: { target: number } }`.
  Stores per-WO alignment targets for the Data Alignment page. No backend write required.

## Special Notes
- Database migrations run automatically on API startup (see `run_migrations()` in app.py)
- DB Tweak endpoints provide administrative functions for data management
- Raw log access requires authentication via DB Tweak credentials
- Filtering system supports cascading dropdowns for Year/Month/Week/Day/Work Order
- All datetime filtering uses `unit_date` with fallback to `start_time::date`
- Data Alignment logic is purely client-side: golden stops and gap calculation happen in-browser
- Overview page fetches 6 API endpoints in parallel via `Promise.all` for faster load
- Search inputs (Work Orders, Fails, Fail Analysis) are debounced at 200 ms
- Log Uploader: `file_hash` = sha256 of each split record and ignores the work order, so a wrong work order cannot be fixed by re-uploading — undo the batch (`DELETE /api/log-upload/{id}`) first. Rows inserted by the web uploader carry `module_test.upload_id`; desktop-uploaded rows have NULL. Upload batches are recorded in `upload_log`; a successful upload writes the planned quantity to `alignment_targets`.
- `module_log_parser._plain_sum` keeps a left-to-right float sum on purpose: Python 3.12's compensated `sum()` shifts some 3-decimal WiFi power averages by 0.001 versus the Python 3.11 desktop uploader
- The scheduled 10-minute page carousel + latest-WO AI report popup was removed; the sidebar "AI Report — Latest WO" button opens it on demand. The 5-minute idle page rotation remains and pauses while a Log Upload is unfinished.
- API files are single-file bind mounts in `docker-compose.yml`: after editing `app.py`, `module_log_parser.py`, `log_splitter.py` or the dashboard HTML, restart the api container (`docker compose restart api`) or it keeps serving the old file