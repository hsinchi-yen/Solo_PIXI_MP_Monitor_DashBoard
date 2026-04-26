# Solo PIXI MP Monitor Dashboard (Solo PIXI 模組測試監控與分析儀表板)

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

### Project Overview
The **Solo PIXI MP Monitor Dashboard** is a comprehensive solution designed for production line monitoring and analysis of Solo PIXI module tests. It streamlines the workflow from raw log processing to real-time data visualization.

### Key Features
- **Log Splitting Tool (`log_splitter_app.py`)**: 
    - A PyQt5-based desktop application with a modern macOS-style UI.
    - Automatically parses large consolidated logs (e.g., `Log_All.txt`) into individual unit test records.
    - Real-time tracking of PASS, FAIL, and STOP counts.
- **Log Management & Upload**:
    - Infrastructure to upload processed test data to a centralized PostgreSQL database.
- **Advanced Dashboard (`solo-pixi-essential/`)**:
    - **FastAPI Backend**: Provides robust RESTful APIs for data retrieval and management.
    - **Interactive Visualization**: Real-time charts for Yield Rate trends, Fail analysis (Pareto charts), and detailed RF metrics (BT Power, WiFi EVM, etc.).
    - **Filtering & Search**: Cascading filters by Year, Month, Week, Day, and Work Order.
    - **Aligned KPI vs Raw KPI**: The Overview page displays two KPI rows — Aligned (▲) on top and Raw (▼) below — so management can see both corrected and un-corrected yield at a glance.
    - **Data Alignment** (`⚖️` page, no login required): Per-Work-Order target management. Untested units (Gap = Target − Tested) are counted as FAIL with reason "can't test". Units with prior PASS records that re-appear as STOP (golden sample re-validation) are automatically reclassified to PASS.
- **Maintenance Tools**:
    - **DB Tweak**: An administrative interface for managing records, deleting old data, and reparsing raw logs.

### Data Alignment Logic
| Term | Definition |
|---|---|
| Golden Stops | Retry records where `pass_count > 0` AND `stop_count > 0` (unit previously passed, now a golden sample being re-validated as STOP → reclassified to PASS) |
| Gap | `max(0, Target Total − Tested)` per Work Order; counted as FAIL (can't test) |
| Aligned PASS | `Raw PASS + Golden Stops` |
| Aligned FAIL | `Raw FAIL + Total Gap` |
| Aligned Total | `Raw Total + Total Gap` |
| Aligned Yield | `Aligned PASS / Aligned Total × 100 %` |

Targets are stored in `localStorage` key `pixi-align-v1` — no backend write and no authentication required.

### Technical Stack
- **Frontend**: HTML5, Vanilla JavaScript, Chart.js / D3.js (via dashboard).
- **Backend**: Python 3.10+, FastAPI, Uvicorn.
- **Database**: PostgreSQL.
- **Desktop UI**: PyQt5.
- **Deployment**: Docker & Docker Compose support.

### Performance Notes
- Dashboard uses `Promise.all` parallel fetching (6 concurrent API calls on the Overview page).
- CSS `contain: layout` applied to chart boxes and data tables to hint layout isolation.
- Search inputs debounced at 200 ms to prevent excessive re-renders on keystrokes.

### Testing
Run the included unit test suite from the project root:
```bash
python test_dashboard_suite.py
```
The suite has 74 tests across three classes:
- `TestHTMLStructure` — validates all page elements, IDs, nav links, CSS classes, JS functions
- `TestAPIFunctions` — tests pure backend helpers (`_normalize_page_size`, `_build_where`) without a DB
- `TestJavaScriptLogic` — runs alignment JS functions via Node.js (requires Node ≥ 18)

---

<a name="中文"></a>
## 中文

### 專案簡介
**Solo PIXI MP Monitor Dashboard** 是一套專為 Solo PIXI 模組產線測試開發的完整監控與分析解決方案。本工具實現了從原始日誌處理到即時數據視覺化的自動化流程。

### 主要功能
- **日誌切分工具 (`log_splitter_app.py`)**: 
    - 基於 PyQt5 開發的桌面應用程式，具備現代化的 macOS 風格介面。
    - 自動將龐大的合併日誌（如 `Log_All.txt`）切分為獨立的單機測試紀錄。
    - 即時統計總筆數、PASS、FAIL 及 STOP 數量。
- **日誌管理與上傳**:
    - 提供將處理後的測試數據同步至中央 PostgreSQL 資料庫的機制。
- **進階儀表板 (`solo-pixi-essential/`)**:
    - **FastAPI 後端**: 提供高效的 RESTful API，負責數據查詢與管理。
    - **互動式視覺化**: 即時展示直通率 (Yield Rate) 趨勢、不良分析 (Pareto 圖) 以及詳細的 RF 指標（BT 功率、WiFi EVM 等）。
    - **篩選與搜索**: 支援按年、月、週、日及工單 (Work Order) 進行多層次聯動篩選。
    - **校正 KPI vs 原始 KPI**: 概覽頁面同時顯示校正後（▲ Aligned KPI）與原始（▼ Raw KPI）兩列指標，供管理層快速比較。
    - **數據校正** (`⚖️` 頁面，無需登入): 按工單設定目標總數；未測試數量（Gap = 目標 − 已測）計為 FAIL（無法測試）；具有先前 PASS 記錄的 STOP 單元（黃金樣品再驗證）自動轉回 PASS。
- **維護工具**:
    - **DB Tweak**: 提供管理員介面，用於管理紀錄、刪除舊數據以及重新解析日誌。

### 技術棧
- **前端**: HTML5, Vanilla JavaScript, 可視化圖表。
- **後端**: Python 3.10+, FastAPI, Uvicorn。
- **資料庫**: PostgreSQL。
- **桌面 UI**: PyQt5 (使用現代化樣式表)。
- **部署**: 支援 Docker 與 Docker Compose 容器化部署。

### 單元測試
從專案根目錄執行測試套件：
```bash
python test_dashboard_suite.py
```
共 74 個測試，包含 HTML 結構驗證、後端純函式測試（無需 DB 連線）及 JavaScript 邏輯測試（需 Node.js ≥ 18）。
