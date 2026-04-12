# 計畫：Solo PIXI 模組測試分析儀表板

**摘要**：為 `outlog/` 資料夾內的 QCA9377 BT+WiFi 模組生產測試 log 建立完整分析系統。新建獨立 Docker stack 於 `solo-pixi-essential/`（API 埠號 8001，PostgreSQL 埠號 5433），並製作 PyQt5 桌面工具 `log_uploader_app.py`，採 Apple 暗色風格，直接連線 PostgreSQL 上傳資料。

---

## 技術決策

| 項目 | 決策 |
|------|------|
| 部署方式 | 獨立 `solo-pixi-essential/` 資料夾，Port API=8001，PostgreSQL=5433 |
| LogUploader 連線 | 直接使用 psycopg2 連線 PostgreSQL（不透過 API 中介） |
| WiFi 資料細節 | 儲存所有 rate 的細項 TX 測量值（DB 欄位完整，分析更豐富） |
| 介面主題 | 僅 Apple 暗色模式（無切換），macOS HIG 色系：`#007aff`、`#34c759`、`#ff3b30`、`#ff9500` |
| 視覺化方案 | 不使用 Grafana，採自製 HTML SPA |

---

## 第一階段 — 資料庫 Schema（`solo-pixi-essential/schema.sql`）

建立資料表 `module_test`，依測試區段分組欄位：

**識別資訊**：`id`、`work_order`、`mac1`、`mac2`、`tester_sn`、`start_time`、`end_time`、`test_duration_sec`、`result`（PASS/FAIL/STOP）、`file_hash UNIQUE`、`source_file`、`created_at`

**BT_TX_BDR**（2402 MHz，1DH1）：
`bdr_freq_error`、`bdr_freq_drift`、`bdr_delta_f2_max`、`bdr_power`、`bdr_delta_f1_avg`、`bdr_delta_f2_f1_ratio`、`bdr_pass`

**BT_TX_EDR1**（2441 MHz，2DH1）：
`edr1_devm_avg`、`edr1_devm_peak`、`edr1_power_diff`、`edr1_omega_i`、`edr1_omega_0`、`edr1_omega_i0`、`edr1_devm_99pct`、`edr1_power`、`edr1_pass`

**BT_TX_EDR2**（2480 MHz，3DH1）：
`edr2_devm_avg`、`edr2_devm_peak`、`edr2_power`、`edr2_pass`

**BT_TX_LE**（2402 MHz，1LE）：
`le_freq_error`、`le_delta_f2_avg`、`le_delta_f2_max`、`le_delta_f0_fn_max`、`le_delta_f1_f0`、`le_delta_fn_fn5_max`、`le_power`、`le_delta_f1_avg`、`le_f2_f1_ratio`、`le_pass`

**BT_RX**：`ber_2441`、`ber_2480`、`per_le`、`bt_rx_pass`

**WiFi 校正**：`xtal_cap`、`xtal_freq_error_ppm`、`cal_pass`

**WiFi 2.4G TX**（各 rate：CCK-11、OFDM-54、HT20、HT40，各含 EVM + Power）：
`wifi24_cck11_evm`、`wifi24_cck11_power`、`wifi24_ofdm54_evm`、`wifi24_ofdm54_power`、
`wifi24_ht20_evm`、`wifi24_ht20_power`、`wifi24_ht40_evm`、`wifi24_ht40_power`、`wifi24_tx_pass`

**WiFi 5G TX**（各 rate：OFDM-54、HT20、HT40、VHT80，各含 EVM + Power）：
`wifi5_ofdm54_evm`、`wifi5_ofdm54_power`、`wifi5_ht20_evm`、`wifi5_ht20_power`、
`wifi5_ht40_evm`、`wifi5_ht40_power`、`wifi5_vht80_evm`、`wifi5_vht80_power`、`wifi5_tx_pass`

**WiFi RX PER**：`wifi24_per_max`、`wifi24_rx_pass`、`wifi5_per_max`、`wifi5_rx_pass`

**失敗資訊**：`fail_step_num`、`fail_step_name`、`fail_message`

**Views**：`v_yield_by_wo`（各工單良率）、`v_fail_summary`（失敗步驟統計）、`v_retry_units`（重測機台清單）

**索引**：`start_time`、`work_order`、`result`、`mac1`、複合 `(work_order, start_time)`

---

## 第二階段 — Log 解析器（`solo-pixi-essential/module_log_parser.py`）

實作 `parse_log_file(path) -> dict`，使用 regex 解析：

- **檔名格式**：`{工單}_{YYYYMMDD}_{HHMMSS}_{MAC1}_{MAC2}_{結果}.txt`
- **標頭**：`MAC1:\t{mac}`、`MAC2:\t{mac}`、`Start:\t{datetime}`
- **結尾**：`End:\t\t{datetime}`、`Test Time:\t{mm:ss.s}`
- **區段偵測**：依步驟名稱行識別（BT_TX_BDR、BT_TX_EDR、BT_TX_LE、BT_RX_BER、BT_RX_LE、WIFI_TX_CALIBRATION、WIFI_TX_VERIFY_ALL 2.4G/5G、WIFI_RX_VERIFY_PER 2.4G/5G）
- **量測值格式**：`{標籤}  {數值} {單位}  ({上限} ~ {下限})  <-- pass/fail`
- **結果偵測**：`\*\*\*\* (P A S S|F A I L|S T O P) \*\*\*\*`
- **重複防止**：計算檔案內容 SHA256 存入 `file_hash`，`INSERT ... ON CONFLICT DO NOTHING`
- **失敗原因**：從 `*** DUT failed at {step_name}! ***` 行提取

同時提供 `insert_record(conn, record_dict)` 及 `scan_and_ingest(folder, conn, on_progress=None)` 輔助函式。

---

## 第三階段 — FastAPI 後端（`solo-pixi-essential/api/app.py`）

參照 `dockerup-essential/api/app.py` 的 API 設計模式：

| 方法 | 路徑 | 用途 |
|------|------|------|
| GET | `/` | 提供 `solo_pixi_dashboard.html` |
| GET | `/health` | 資料庫連線檢查 |
| GET | `/api/summary` | KPI 摘要：total/pass/fail/stop/yield%/retry_rate |
| GET | `/api/yield-trend` | 逐小時良率趨勢，以 `generate_series` 補全缺失時段 |
| GET | `/api/pass-fail-split` | PASS/FAIL/STOP 數量（甜甜圈圖用） |
| GET | `/api/fail-analysis` | 最常見失敗步驟（橫向長條圖，前十名） |
| GET | `/api/bt-metrics` | BDR Power、EDR DEVM、BER/PER 分布統計 |
| GET | `/api/wifi-metrics` | 2.4G/5G 各 rate 的 EVM 與 PER 分布 |
| GET | `/api/calibration` | Xtal_cap 與 Xtal_freq_error_ppm 統計 |
| GET | `/api/work-orders` | 工單清單，含良率%、測試數量、日期區間、重測率 |
| GET | `/api/fails` | 最新 FAIL 紀錄，含失敗步驟名稱 |
| GET | `/api/retries` | 重測機台（attempt > 1），標示 retry_risk（low/medium/high） |

- 所有端點支援 `?work_order=` 與 `?year=` 查詢參數
- 使用 `LATEST_RECORD_CTE`（ROW_NUMBER PARTITION BY mac1 ORDER BY start_time DESC）去除重測重複計算

---

## 第四階段 — Docker Compose（`solo-pixi-essential/docker-compose.yml`）

兩個服務：

- **postgres**：`postgres:16-alpine`，埠號 `5433:5432`，資料庫 `pixi_test`，帳號 `pixi` / 密碼 `pixipass`
- **api**：自製建置 `./api`，埠號 `8001:8000`，掛載 `module_log_parser.py` 與 `solo_pixi_dashboard.html`

Named volume 儲存 pgdata。api 服務加入 postgres 健康檢查 depends_on 條件。

---

## 第五階段 — 儀表板 SPA（`solo-pixi-essential/solo_pixi_dashboard.html`）

Apple 暗色模式單頁應用，參照 `dockerup-essential/wifi_dashboard.html` 的側邊欄導覽結構，搭配 Chart.js 4.4.1（CDN）。

**色彩變數（CSS custom properties）**：
```css
--bg: #000000;          /* 最底層背景 */
--surface: #1c1c1e;     /* 卡片/側邊欄底色 */
--surface2: #2c2c2e;    /* 次層容器 */
--surface3: #3a3a3c;    /* 輸入框、懸停 */
--text: #f5f5f7;        /* 主文字 */
--text2: #aeaeb2;       /* 次要文字 */
--blue: #007aff;        /* 主要互動色 */
--green: #34c759;       /* PASS / 良率高 */
--red: #ff3b30;         /* FAIL / 警示 */
--orange: #ff9500;      /* STOP / 警告中 */
--purple: #bf5af2;      /* 重測 / 特殊標記 */
--teal: #5ac8fa;        /* 次要資訊色 */
```

**六個導覽頁面**（側邊欄 220px + 主內容區）：

1. **總覽（Overview）**
   - 6 個 KPI 指標卡：總測試數、PASS、FAIL、STOP、良率 %、重測率 %
   - 逐小時良率折線圖（Chart.js timeseries）
   - PASS / FAIL / STOP 動態甜甜圈圖
   - 側邊欄即時時鐘（HH:MM:SS）

2. **BT 分析（BT Analysis）**
   - BDR Power 分布長條圖
   - EDR DEVM 直方圖（2DH1 vs 3DH1 疊加）
   - BER / PER 通過率摘要卡片

3. **WiFi 分析（WiFi Analysis）**
   - Xtal Cap 分布直方圖
   - 2.4G 各 rate EVM 分組長條圖
   - 5G 各 rate EVM 分組長條圖
   - RX PER 分布（2.4G / 5G 並列）

4. **失敗分析（Fail Analysis）**
   - 失敗步驟頻率水平長條圖（前十名）
   - 失敗紀錄表格（MAC1、時間、失敗步驟名稱、失敗訊息）

5. **工單管理（Work Orders）**
   - 表格欄位：良率進度條（綠 ≥95%、黃 ≥80%、紅 <80%）、測試數量、PASS/FAIL/STOP 細分、日期區間、重測率

6. **資料管理（Data）**
   - LogUploader App 使用說明與連線參數提示
   - DB Tweak 面板：驗證登入、依 MAC 或工單刪除紀錄、下載原始 log 檔

---

## 第六階段 — LogUploader App（`log_uploader_app.py`）

PyQt5 桌面應用程式，採 Apple `MAC_STYLESHEET` 暗色主題（與 `log_splitter_app.py` 相同風格）。

**三欄版面配置**：

1. **連線設定**
   - 欄位：Host、Port、資料庫名稱、使用者、密碼
   - 「測試連線」按鈕 + 連線狀態標籤（綠色/紅色）

2. **檔案選擇**
   - 「選擇資料夾」+「新增檔案」按鈕
   - QListWidget 顯示已選路徑
   - 檔案數量標籤、「清除」按鈕

3. **進度與統計**
   - `QProgressBar`（0–100%）
   - 統計網格：排隊中 / 已上傳 / 已略過 / 失敗（即時更新）
   - 主控台 `QTextEdit`（唯讀，等寬字型）

**`UploadWorkerThread(QThread)` 訊號**：

| 訊號 | 型態 | 說明 |
|------|------|------|
| `progress` | int | 整體進度 0–100 |
| `stats` | dict | keys: queued / uploaded / skipped / failed |
| `log` | str | 單行 log 訊息 |
| `finished` | str | 完成摘要文字 |
| `error` | str | 錯誤訊息 |

**每個檔案的處理流程**：
1. 呼叫 `module_log_parser.parse_log_file(path)` → record dict
2. 計算 `file_hash = sha256(檔案內容)`
3. 查詢 `SELECT id FROM module_test WHERE file_hash = %s`，若存在則 emit skip 並繼續
4. 呼叫 `module_log_parser.insert_record(conn, record_dict)`
5. 每 10 筆批次 commit

**按鈕**：「開始上傳」（啟動前驗證連線與檔案清單）、「停止」（設定 `self._stop = True`）、「清除統計」

---

## 待建立檔案清單

| 路徑 | 說明 |
|------|------|
| `solo-pixi-essential/docker-compose.yml` | PostgreSQL + API 服務定義 |
| `solo-pixi-essential/schema.sql` | `module_test` 資料表、Views、索引 |
| `solo-pixi-essential/module_log_parser.py` | 獨立 log 解析器 + DB 插入輔助函式 |
| `solo-pixi-essential/solo_pixi_dashboard.html` | Apple 暗色 SPA 儀表板 |
| `solo-pixi-essential/api/app.py` | FastAPI 後端 |
| `solo-pixi-essential/api/Dockerfile` | Python 3.12-slim + uvicorn |
| `solo-pixi-essential/api/requirements.txt` | fastapi、uvicorn、psycopg2-binary、pydantic |
| `solo-pixi-essential/.env.example` | 環境變數範本（STORAGE_PATH、DB 帳密） |
| `log_uploader_app.py` | PyQt5 桌面上傳工具 |

---

## 參考檔案（請勿修改）

- [dockerup-essential/api/app.py](dockerup-essential/api/app.py) — API 設計模式（generate_series 補全、CTE 去重）
- [dockerup-essential/schema.sql](dockerup-essential/schema.sql) — DB schema 慣例
- [dockerup-essential/wifi_dashboard.html](dockerup-essential/wifi_dashboard.html) — CSS 變數、Chart.js 用法、側邊欄導覽結構
- [log_splitter_app.py](log_splitter_app.py) — `MAC_STYLESHEET`、`QThread` 訊號模式、版面配置參考
- `outlog/` — 235 個 log 檔：**200 PASS · 26 FAIL · 9 STOP**

---

## 驗收清單

- [ ] `docker compose -f solo-pixi-essential/docker-compose.yml up -d` → API 在 `http://localhost:8001`，DB 在埠號 `5433`
- [ ] `GET /health` 回傳 `{"status":"ok"}`
- [ ] 執行 `log_uploader_app.py` → 連線 → 選擇 `outlog/` → 上傳 235 個檔案 → 統計顯示正確數量
- [ ] 瀏覽 `http://localhost:8001` → 六個導覽頁面全部載入，圖表有資料顯示
- [ ] `GET /api/summary` 回傳良率約 85.1%（235 個原始紀錄中 200 PASS）
- [ ] `GET /api/fail-analysis` 最高頻失敗步驟為 `ATC_INITIALIZE_DUT`（WiFi 初始化失敗）
- [ ] 重複執行 LogUploader 上傳相同 235 個檔案 → 全部顯示為「已略過」（重複防止機制驗證）

---

## 不在本次範圍內

- 不整合 Grafana
- 不製作淺色模式 / 主題切換
- 儀表板不設置登入驗證
- `TN131_1v4_logs/` 資料夾不在本次範圍（log 格式不同）
