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
- **Maintenance Tools**:
    - **DB Tweak**: An administrative interface for managing records, deleting old data, and reparsing raw logs.

### Technical Stack
- **Frontend**: HTML5, Vanilla JavaScript, Chart.js / D3.js (via dashboard).
- **Backend**: Python 3.10+, FastAPI, Uvicorn.
- **Database**: PostgreSQL.
- **Desktop UI**: PyQt5.
- **Deployment**: Docker & Docker Compose support.

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
- **維護工具**:
    - **DB Tweak**: 提供管理員介面，用於管理紀錄、刪除舊數據以及重新解析日誌。

### 技術棧
- **前端**: HTML5, Vanilla JavaScript, 可視化圖表。
- **後端**: Python 3.10+, FastAPI, Uvicorn。
- **資料庫**: PostgreSQL。
- **桌面 UI**: PyQt5 (使用現代化樣式表)。
- **部署**: 支援 Docker 與 Docker Compose 容器化部署。
