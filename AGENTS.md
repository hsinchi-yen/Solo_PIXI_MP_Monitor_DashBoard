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
- 74 tests: HTML structure, API pure functions (no DB), JS alignment logic via Node.js

## Environment Configuration
- Database URL: Set via `DATABASE_URL` env var (default: `postgresql://pixi:pixipass@localhost:5433/pixi_test`)
- DB Tweak credentials: Set via `TWEAK_USER` and `TWEAK_PASS` (default: `pixi`/`pixipass`)
- See `.env.example` for a complete template of all environment variables

## Project Structure
- `solo-pixi-essential/`: Contains FastAPI backend and dashboard
  - `api/`: FastAPI application
  - `api/app.py`: Main FastAPI application with all endpoints
  - `module_log_parser.py`: Log parsing utility used by reparse endpoint
  - `solo_pixi_dashboard.html`: Frontend dashboard (single-file, ~3260 lines)
  - `docker-compose.yml`: Service definitions for PostgreSQL, API, and Nginx
- Root level: Desktop applications, utility scripts, and test suite
  - `test_dashboard_suite.py`: Unit test suite (74 tests, no DB required)

## Key API Endpoints
- Health: `GET /health`
- Summary stats: `GET /api/summary`
- Yield trends: `GET /api/yield-trend`
- Fail analysis: `GET /api/fail-analysis`
- Filter options: `GET /api/filter-options`
- Work order summary: `GET /api/work-order-summary`
- Retries: `GET /api/retries`
- DB Tweak (admin): Requires `X-Tweak-Token` header (base64 encoded `user:pass`)

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