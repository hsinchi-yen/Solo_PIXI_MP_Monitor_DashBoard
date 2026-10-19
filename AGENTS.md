# Solo PIXI MP Monitor Dashboard - Agent Guide

## Core Commands

### Stack (run in `solo-pixi-essential/`)
- Production-style: `docker compose up -d --build` — code is baked into the api image; nginx serves `web/`
- Development: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d` — `api/` and the two shared modules are mounted; after editing Python run `... restart api`. `web/` and `nginx/` are mounted in both modes (reload the browser; `docker exec <prefix>-nginx nginx -s reload` after nginx changes)
- The local test stack on hermesrunner uses compose project `pixi-local` (`docker compose -p pixi-local …`)
- Health: `curl http://localhost:8001/health` (DB latency, LLM status, stats refresh time, version)
- API docs: `http://localhost:8001/docs`
- DB shell: `psql postgresql://pixi:pixipass@localhost:5433/pixi_test`
- Deploy: `scripts/deploy.sh` (backup → rsync → build → health → automatic rollback); rehearse with `scripts/deploy.sh --local DIR`. Production deploys need the user's explicit approval.

### Tests
- `python tests/test_unit.py` — no DB; also runs `node --test tests/js/` when Node is present; FastAPI tests skip where FastAPI is not installed (the host has none; run them in the api image: `docker run --rm -v "$PWD":/repo:ro pixi-dashboard-api:3.0.0 sh -c "pip install -q httpx && python /repo/tests/test_unit.py"`)
- `python tests/test_integration.py` — needs the running stack + `docker` (direct DB checks); uploads use `rawlogs/*.zip`
- `python tests/test_e2e.py [--shots DIR]` — Playwright; screenshots for docs/test-report
- CI: `.github/workflows/tests.yml` runs the unit suites

## Architecture

### Rule: existing tables keep their columns
`module_test` and the other existing tables are never altered (the desktop uploader writes `module_test` directly with a fixed column list). Allowed: new indexes, new tables, materialized views, query rewrites. Startup migrations check `information_schema` before touching `module_test` (only the four nullable columns earlier releases added: `fail_error_code`, `fail_category`, `unit_date`, `upload_id`).

### API (`solo-pixi-essential/api/`)
- `app.py` entry (lifespan: migrations + scheduler; request timing log; StaticFiles fallback when run without nginx)
- `config.py` env settings + shared thresholds (yield ≥98 green, ≥95 amber; retry ≤3 green, ≤5 amber); `db.py` per-worker pool (`with db.db() as conn`), dedicated `db.connect()` for work that holds advisory locks; slow statements (>500 ms) logged
- `errors.py` — every error is `{"error", "code"}`; raise `ApiError` / `bad_request` / `not_found` / `conflict`
- `routers/`: `analytics` (public dashboard data), `auth`, `admin` (accounts), `tweak` (DB Tweak + `/api/reparse` jobs), `upload` (Log Uploader), `reports` (AI), `system` (health, llm-status, stats refresh)
- `services/`: `filters` (date-range scope), `stats` (unit statistics from the views + result cache), `measurements` (SQL histograms), `reports` + `report_text` (AI report facts / prompt / number check / Markdown), `llm`, `ingest` (web upload thread), `reparse` (background job), `migrations`, `scheduler`, `auth`, `auth_utils`, `upload_chat`
- Shared with the desktop tools, one level up: `module_log_parser.py`, `log_splitter.py`

### Statistics
- "Unit" = (work order, MAC1), counted by its latest PASS if it ever passed, else its latest attempt (the original `ROW_NUMBER … PASS first, start_time DESC` rule, within the requested scope).
- `mv_unit_day` (per effective date × WO × MAC1) and `mv_unit` (per unit, all dates) are refreshed CONCURRENTLY every `STATS_REFRESH_SEC` (300) and right after web uploads, undo and DB Tweak deletes. Desktop uploads appear within one interval (or after 「立即更新」). Bump `MV_VERSION` in `services/migrations.py` when changing a view definition.
- Results are cached per worker under the data version (view refresh time + alignment targets), so nothing stale is served.
- Scope parameters: `date_from`, `date_to` (inclusive ISO dates), `work_order`; legacy `year/month/week/day/unit_date` still accepted (`week` is ISO and needs `year`). Filters use `COALESCE(unit_date, start_time::date)`, indexed by `idx_mt_eff_date` — keep that exact expression.
- A refactor must keep numbers identical: the pre-refactor comparison (210 checks) is described in `docs/test-report/test_report.md`.

### AI reports
- `build_facts()` computes every number (units, PASS/FAIL/STOP, yield, retry, top-5 error codes/categories, planned vs tested, period, previous 3 WOs, risk level by thresholds). The LLM writes only `summary / 3 findings / 3 recommendations` via `json_schema`, zh and en in parallel, temperature 0.2. `check_numbers()` flags numbers not in the facts and regenerates once.
- Stored in `ai_reports` (both languages per row, `data_hash` of the facts). Generated on demand (NDJSON stream), after each web upload (queued) and daily at `REPORT_DAILY_AT` (18:00, `APP_TZ`). One background generation at a time across workers (advisory locks). Follow-up questions: `POST /api/reports/{wo}/ask`.
- AI text is rendered with `textContent` only (no Markdown → HTML).

### Accounts
- `X-Tweak-Token` carries a random session token (SHA-256 stored in `app_sessions`, `SESSION_TTL_HOURS` = 8). Administrator = `TWEAK_USER`/`TWEAK_PASS`; uploaders = `app_users` (disabled, never deleted). Dependencies: `Depends(auth.current_user)`, `Depends(auth.require_admin)`.
- Uploaders: Log Uploader (own batches only) and planned quantities. Admin: everything incl. DB Tweak, reparse, accounts, daily-batch trigger.

### Web (`solo-pixi-essential/web/`, no build step)
- `index.html` shell; `css/theme.css` (Dark default / Light — the only two themes; all colours incl. charts are variables), `css/base.css`, `css/pages.css`
- `js/main.js` routing (`#overview` …), filter bar (全部/今天/本週/本月/自訂 + 工單; default = latest work order), freshness + 立即更新, theme, idle rotation (overview ↔ workorders every 30 s after 5 min idle; filters untouched; paused while busy)
- `js/core/`: `api.js` (errors, NDJSON, authenticated downloads), `auth.js`, `charts.js`, `format.js`, `scope.js`, `i18n.js` (every UI string; `data-i18n*` attributes in HTML), `ui.js`
- `js/pages/*.js` one module per page: `{id, scoped, init(app), load(app), onAuthChange?, isBusy?}`; `js/report.js` the single AI report window
- Chart.js is vendored (`vendor/chart.umd-4.4.1.min.js`); no CDN at runtime
- Unit tests check: every `t('key')` / `data-i18n` key exists, ids used by JS exist, no inline styles, no hex colours outside `theme.css`

## Dashboard Pages
| Page key | Nav label | Auth |
|---|---|---|
| `overview` | 📊 儀表板 | — |
| `workorders` | 📋 工單 | — |
| `fails` | ⚠️ 失敗清單 | — |
| (button) | ✨ AI 報告（最新工單） | — (generation needs the LLM) |
| `measurements` | 📡 量測分析 (BT / WiFi / 其他 tabs) | — |
| `failanalysis` | 🔍 失敗分析 | — |
| `dbtweak` | 🔧 資料庫維護 (DB Tweak) | administrator |
| `dataalign` | ⚖️ 計畫數對齊 (Data Alignment) | view: —; save: signed in |
| `logupload` | 📤 上傳 Log | administrator or uploader |
| `accounts` | 👥 帳號管理 | administrator (hidden otherwise) |

## Key Endpoints
- Dashboard (public): `GET /api/overview`, `/api/filter-options`, `/api/summary`, `/api/pass-fail-split`, `/api/trend`, `/api/monthly-count`, `/api/work-order-summary`, `/api/retries`, `/api/fails` (paged), `/api/fail-analysis`, `/api/config-analysis`, `/api/measurements`, `/api/alignment`, `/api/raw-log/{id}`, `/api/meta`
- `GET/POST /api/alignment-targets` (POST needs sign-in)
- `POST /api/stats/refresh` (at most once per 10 s)
- Auth: `POST /api/auth/login` (alias `/api/tweak/login`), `/logout`, `GET /me`, `POST /change-password`
- Admin: `/api/admin/users` (GET/POST), `PATCH /api/admin/users/{u}`, `POST …/reset-password`, `GET /api/admin/audit`, `/api/admin/login-log`
- DB Tweak (admin): `GET /api/tweak/records`, `DELETE /api/tweak/delete-scope`, `DELETE /api/tweak/records/{id}`, `GET /api/tweak/raw-log/{id}`; reparse `POST/GET /api/reparse`, `POST /api/reparse/{id}/cancel`
- Log Uploader (signed in): `POST /api/log-upload/analyze|validate|chat|commit`, `GET /history`, `GET|DELETE /{id}`
- AI reports: `GET /api/reports/{wo}`, `POST …/generate?force=`, `GET …/history`, `GET …/compare`, `GET …/markdown?lang=`, `POST …/ask`, `POST /api/reports/run-daily` (admin)

## Environment
`solo-pixi-essential/.env.example` lists everything: `PIXI_PREFIX` / `PIXI_HTTP_PORT` / `PIXI_DB_PORT` (container names and ports), `TWEAK_USER` / `TWEAK_PASS`, `SESSION_TTL_HOURS`, `LLM_*`, `STATS_REFRESH_SEC`, `REPORT_AUTO`, `REPORT_DAILY_AT`, `APP_TZ`, `DB_POOL_MAX`, `APP_VERSION`.

## Notes
- nginx: no upstream keepalive (it caused ~40 ms stalls on small responses); NDJSON routes are unbuffered; 512 MB bodies only for `/api/log-upload/analyze`; gzip on; `index.html`/js/css revalidate (`no-cache`), `vendor/` is immutable.
- `module_log_parser._plain_sum` keeps a left-to-right float sum on purpose (matches the Python 3.11 desktop uploader).
- Log Uploader: `file_hash` ignores the work order, so a wrong work order cannot be fixed by re-uploading — undo the batch first. Rows from the web uploader carry `module_test.upload_id`.
- Desktop tools (`log_splitter_app.py`, `log_uploader_app.py`, `log_concatenate_app.py`) are unchanged and keep working against the same database.
- Design records: `improve_plan.md` (this round), `log_uploader_web_plan.md` (uploader / accounts); superseded plans in `docs/archive/`.
