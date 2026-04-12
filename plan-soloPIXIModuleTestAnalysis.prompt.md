# Plan: Solo PIXI Module Test Analysis Dashboard

**TL;DR**: Build a complete analytics stack for the QCA9377 BT+WiFi module production tests in `outlog/`. New independent Docker stack in `solo-pixi-essential/` (API:8001, PG:5433) + a PyQt5 `log_uploader_app.py` desktop app with Apple dark style for direct PostgreSQL uploads.

---

## Decisions
- **Deployment**: Independent `solo-pixi-essential/` folder, ports API=8001, PostgreSQL=5433
- **LogUploader**: Direct psycopg2 to PostgreSQL (no API intermediary)
- **WiFi detail**: Store all per-rate TX measurements as individual DB columns
- **Theme**: Apple dark mode only (no toggle), macOS HIG colors: `#007aff`, `#34c759`, `#ff3b30`, `#ff9500`
- **Stack**: No Grafana, custom HTML SPA only

---

## Phase 1 — DB Schema (`solo-pixi-essential/schema.sql`)

Create table `module_test` with columns grouped by test section:

**Identity**: `id`, `work_order`, `mac1`, `mac2`, `tester_sn`, `start_time`, `end_time`, `test_duration_sec`, `result` (PASS/FAIL/STOP), `file_hash UNIQUE`, `source_file`, `created_at`

**BT_TX_BDR** (2402 MHz, 1DH1):
`bdr_freq_error`, `bdr_freq_drift`, `bdr_delta_f2_max`, `bdr_power`, `bdr_delta_f1_avg`, `bdr_delta_f2_f1_ratio`, `bdr_pass`

**BT_TX_EDR1** (2441 MHz, 2DH1):
`edr1_devm_avg`, `edr1_devm_peak`, `edr1_power_diff`, `edr1_omega_i`, `edr1_omega_0`, `edr1_omega_i0`, `edr1_devm_99pct`, `edr1_power`, `edr1_pass`

**BT_TX_EDR2** (2480 MHz, 3DH1):
`edr2_devm_avg`, `edr2_devm_peak`, `edr2_power`, `edr2_pass`

**BT_TX_LE** (2402 MHz, 1LE):
`le_freq_error`, `le_delta_f2_avg`, `le_delta_f2_max`, `le_delta_f0_fn_max`, `le_delta_f1_f0`, `le_delta_fn_fn5_max`, `le_power`, `le_delta_f1_avg`, `le_f2_f1_ratio`, `le_pass`

**BT_RX**: `ber_2441`, `ber_2480`, `per_le`, `bt_rx_pass`

**WiFi Calibration**: `xtal_cap`, `xtal_freq_error_ppm`, `cal_pass`

**WiFi 2.4G TX** (per rate — EVM, Power for CCK-11, OFDM-54, HT20, HT40):
`wifi24_cck11_evm`, `wifi24_cck11_power`, `wifi24_ofdm54_evm`, `wifi24_ofdm54_power`,
`wifi24_ht20_evm`, `wifi24_ht20_power`, `wifi24_ht40_evm`, `wifi24_ht40_power`, `wifi24_tx_pass`

**WiFi 5G TX** (per rate — OFDM-54, HT20, HT40, VHT80):
`wifi5_ofdm54_evm`, `wifi5_ofdm54_power`, `wifi5_ht20_evm`, `wifi5_ht20_power`,
`wifi5_ht40_evm`, `wifi5_ht40_power`, `wifi5_vht80_evm`, `wifi5_vht80_power`, `wifi5_tx_pass`

**WiFi RX PER**: `wifi24_per_max`, `wifi24_rx_pass`, `wifi5_per_max`, `wifi5_rx_pass`

**Fail info**: `fail_step_num`, `fail_step_name`, `fail_message`

**Views**: `v_yield_by_wo`, `v_fail_summary`, `v_retry_units`

---

## Phase 2 — Log Parser (`solo-pixi-essential/module_log_parser.py`)

Write `parse_log_file(path) -> dict` using regex patterns for:
- Filename: `{WO}_{YYYYMMDD}_{HHMMSS}_{MAC1}_{MAC2}_{RESULT}.txt`
- Header: `MAC1:\t{mac}`, `MAC2:\t{mac}`, `Start:\t{datetime}`
- End: `End:\t\t{datetime}`, `Test Time:\t{mm:ss.s}`
- Section detection by step name line (BT_TX_BDR, BT_TX_EDR, BT_TX_LE, BT_RX_BER, BT_RX_LE, WIFI_TX_CALIBRATION, WIFI_TX_VERIFY_ALL 2.4G/5G, WIFI_RX_VERIFY_PER 2.4G/5G)
- Metric line format: `{label}  {value} {unit}  ({hi} ~ {lo})  <-- pass/fail`
- Result detection: `\*\*\*\* (P A S S|F A I L|S T O P) \*\*\*\*`
- SHA256 hash of file content for `file_hash` deduplication (same pattern as `dockerup-essential/log_parser.py`)
- Fail info extracted from `*** DUT failed at {step_name}! ***` lines

Also expose `insert_record(conn, record_dict)` and `scan_and_ingest(folder, conn, on_progress=None)` helpers.

---

## Phase 3 — FastAPI Backend (`solo-pixi-essential/api/app.py`)

Port the API pattern from `dockerup-essential/api/app.py`:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Serve `solo_pixi_dashboard.html` |
| GET | `/health` | DB connectivity check |
| GET | `/api/summary` | KPIs: total/pass/fail/stop/yield%/retry_rate |
| GET | `/api/yield-trend` | Hourly yield%, gap-filled with `generate_series` |
| GET | `/api/pass-fail-split` | PASS/FAIL/STOP counts for donut chart |
| GET | `/api/fail-analysis` | Most common fail step names (bar chart) |
| GET | `/api/bt-metrics` | BDR Power, EDR DEVM, BER/PER distributions |
| GET | `/api/wifi-metrics` | 2.4G/5G EVM + PER distributions by rate |
| GET | `/api/calibration` | Xtal_cap + Xtal_freq_error_ppm statistics |
| GET | `/api/work-orders` | WO list with yield%, unit count, date range, retry rate |
| GET | `/api/fails` | Latest FAIL records + fail_step_name |
| GET | `/api/retries` | MAC pairs with >1 attempt, retry_risk label (low/medium/high) |

All endpoints support `?work_order=` and `?year=` query filters.
Use `LATEST_RECORD_CTE` pattern (ROW_NUMBER PARTITION BY mac1 ORDER BY start_time DESC) to deduplicate retried units.

---

## Phase 4 — Docker Compose (`solo-pixi-essential/docker-compose.yml`)

Two services:
- `postgres`: `postgres:16-alpine`, port `5433:5432`, DB `pixi_test`, user `pixi` / password `pixipass`
- `api`: custom build `./api`, port `8001:8000`, mounts `module_log_parser.py` + `solo_pixi_dashboard.html`

Named volume for pgdata. Health check on postgres before api starts.

---

## Phase 5 — Dashboard SPA (`solo-pixi-essential/solo_pixi_dashboard.html`)

Apple dark mode SPA, modeled on `dockerup-essential/wifi_dashboard.html`'s sidebar nav + Chart.js 4.4.1 (CDN).

**Color palette** (CSS custom properties):
```css
--bg: #000000;
--surface: #1c1c1e;
--surface2: #2c2c2e;
--surface3: #3a3a3c;
--text: #f5f5f7;
--text2: #aeaeb2;
--blue: #007aff;
--green: #34c759;
--red: #ff3b30;
--orange: #ff9500;
--purple: #bf5af2;
--teal: #5ac8fa;
```

**6 nav pages** (sidebar 220px + main content area):

1. **Overview** — 6 KPI metric cards (Total Tested, PASS, FAIL, STOP, Yield %, Retry Rate) + hourly yield line chart + P/F/S animated donut chart + live sidebar clock
2. **BT Analysis** — BDR Power bar chart + EDR DEVM histogram (2DH1 vs 3DH1 overlay) + BER/PER pass rate summary cards
3. **WiFi Analysis** — Xtal cap frequency histogram + 2.4G EVM box/bar chart by rate + 5G EVM box/bar chart by rate + RX PER distribution
4. **Fail Analysis** — Fail step frequency horizontal bar chart (top 10) + fail records table with MAC1, time, fail_step_name, fail_message
5. **Work Orders** — Table with color-coded yield bar (green ≥95%, amber ≥80%, red <80%), unit count, pass/fail/stop counts, date range, retry rate
6. **Data** — Instructions for LogUploader App + DB Tweak panel (authenticate, list records, delete by MAC or WO, view source log)

Sidebar shows: logo/title area, nav links, live clock (HH:MM:SS), work_order filter dropdown (populated from `/api/work-orders`), year filter.

---

## Phase 6 — LogUploader App (`log_uploader_app.py`)

PyQt5 desktop app, Apple `MAC_STYLESHEET` dark theme (same pattern as `log_splitter_app.py`).

**3-panel layout**:
1. **Connection** — host, port, dbname, user, password fields + "Test Connection" button + status label
2. **File Selection** — "Browse Folder" + "Add Files" buttons, QListWidget showing selected paths, file count label, "Clear" button
3. **Progress & Stats** — `QProgressBar`, stats grid (Queued / Uploaded / Skipped / Failed labels), console `QTextEdit` (read-only, monospace)

**`UploadWorkerThread(QThread)`** signals:
- `progress(int)` — 0–100
- `stats(dict)` — keys: queued, uploaded, skipped, failed
- `log(str)` — single log line
- `finished(str)` — summary message
- `error(str)` — error message

**Per-file logic** in worker:
1. Call `module_log_parser.parse_log_file(path)` → record dict
2. Compute `file_hash = sha256(file_content)`
3. Check `SELECT id FROM module_test WHERE file_hash = %s` — if found, emit skip, continue
4. Call `module_log_parser.insert_record(conn, record_dict)`
5. Batch commit every 10 records

**Buttons**: "Start Upload" (validates connection + files first), "Stop" (sets `self._stop = True` on worker), "Clear Stats"

---

## Files to Create

| Path | Description |
|------|-------------|
| `solo-pixi-essential/docker-compose.yml` | PostgreSQL + API services |
| `solo-pixi-essential/schema.sql` | `module_test` table + views + indexes |
| `solo-pixi-essential/module_log_parser.py` | Standalone parser + DB insert helpers |
| `solo-pixi-essential/solo_pixi_dashboard.html` | Apple dark SPA |
| `solo-pixi-essential/api/app.py` | FastAPI backend |
| `solo-pixi-essential/api/Dockerfile` | Python 3.12-slim, uvicorn |
| `solo-pixi-essential/api/requirements.txt` | fastapi, uvicorn, psycopg2-binary, pydantic |
| `solo-pixi-essential/.env.example` | Template for STORAGE_PATH, DB credentials |
| `log_uploader_app.py` | PyQt5 desktop upload tool |

---

## Relevant Reference Files (do not modify)
- [dockerup-essential/api/app.py](dockerup-essential/api/app.py) — API pattern (gap-filling, CTE deduplication)
- [dockerup-essential/schema.sql](dockerup-essential/schema.sql) — DB schema conventions
- [dockerup-essential/wifi_dashboard.html](dockerup-essential/wifi_dashboard.html) — CSS variables, Chart.js, sidebar nav
- [log_splitter_app.py](log_splitter_app.py) — `MAC_STYLESHEET`, `QThread` signal pattern, layout structure
- `outlog/` — 235 log files: **200 PASS · 26 FAIL · 9 STOP**

---

## Verification Checklist
- [ ] `docker compose -f solo-pixi-essential/docker-compose.yml up -d` → API at `http://localhost:8001`, DB at port `5433`
- [ ] `GET /health` → `{"status":"ok"}`
- [ ] Run `log_uploader_app.py` → connect → select `outlog/` → upload 235 files → stats: 200 uploaded or skipped (PASS logic), 26 FAIL, 9 STOP all ingested
- [ ] Browse `http://localhost:8001` → all 6 nav pages load with Chart.js charts populated
- [ ] `GET /api/summary` yields ~85.1% yield (200/235 unique MACs before dedup)
- [ ] `GET /api/fail-analysis` shows `ATC_INITIALIZE_DUT` (WiFi init) as top fail reason
- [ ] Re-uploading same files with LogUploader shows all 235 as "Skipped" (duplicate prevention)

---

## Scope Exclusions
- No Grafana integration
- No light mode / theme toggle
- No login / authentication for dashboard
- `TN131_1v4_logs/` folder not in scope (different log format)
