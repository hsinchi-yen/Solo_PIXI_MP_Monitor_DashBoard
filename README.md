# Solo PIXI MP Monitor Dashboard

QCA9377 BT + WiFi 模組量產測試的監控看板：上傳測試 log、看良率與失敗原因、產生 AI 品質報告。

- **看板**（`solo-pixi-essential/web/`）：儀表板、工單、失敗清單、量測分析、失敗分析、AI 報告；繁體中文介面，深色／淺色兩種主題，以產線大電視（1920×1080）為主要版面。
- **API**（`solo-pixi-essential/api/`）：FastAPI + PostgreSQL 16。統計數字來自 materialized view，量測資料在伺服器端分箱。
- **上傳**：網頁版「上傳 Log」（zip → 切檔 → 寫入資料庫，含 AI 對話確認），以及並行使用的桌面工具（`log_splitter_app.py`、`log_uploader_app.py`）。
- **AI 報告**：本機 LLM（llama.cpp, Qwen3.8-27B）依伺服器算好的事實表撰寫中英文報告；網頁上傳後與每天 18:00 自動產生。

操作說明請看 [docs/operator_manual.md](docs/operator_manual.md)；部署請看 [deployguide.md](deployguide.md)。

## 快速開始（本機）

```bash
cd solo-pixi-essential
cp .env.example .env            # 需要時再修改；全部都有預設值
docker compose up -d --build    # postgres :5433、看板 http://localhost:8001
```

第一次啟動時，`schema.sql` 會建立 `module_test`，API 啟動時會自動建立其餘的資料表、索引和統計 view。
管理者帳號預設是 `pixi` / `pixipass`（`.env` 的 `TWEAK_USER` / `TWEAK_PASS`）。

開發時改用原始碼掛載（改 Python 後重啟 api；`web/` 直接重新整理瀏覽器即可）：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
docker compose -f docker-compose.yml -f docker-compose.dev.yml restart api
```

## 目錄

```
solo-pixi-essential/
├── api/                    FastAPI
│   ├── app.py              進入點：路由、啟動流程、請求計時
│   ├── config.py db.py errors.py schemas.py
│   ├── routers/            analytics, auth, admin, tweak, upload, reports, system
│   └── services/           stats（統計 view）, measurements（分箱）, reports + report_text（AI 報告）,
│                           llm, ingest（網頁上傳）, reparse, migrations, scheduler, filters, auth …
├── web/                    看板（不需要 build）：index.html, css/, js/core/, js/pages/, vendor/
├── nginx/                  靜態檔、gzip、API 反向代理
├── scripts/                deploy.sh（部署＋自動還原）, backup_db.sh（每日備份）
├── module_log_parser.py    log 解析（桌面工具共用）
├── log_splitter.py         Log_All.txt 切檔與上傳規則（網頁上傳用）
└── schema.sql              module_test 初始結構
tests/                      unit（Python + JS）、integration、e2e（Playwright）
docs/                       操作手冊、測試報告、archive/（舊計畫文件）
```

## 測試

```bash
python tests/test_unit.py                       # 單元測試（含 node --test "tests/js/*.test.mjs"）
python tests/test_integration.py                # 需要啟動中的 stack
python tests/test_e2e.py                        # 需要 playwright
```

GitHub Actions（`.github/workflows/tests.yml`）在每次 push 跑單元測試。詳見 [tests/README.md](tests/README.md)。

## 資料庫原則

既有的 `module_test` 等資料表欄位不變，桌面版上傳和舊資料都照常可用。新功能只新增獨立物件：
`mv_unit_day` / `mv_unit`（統計 view）、`idx_mt_eff_date`（日期索引）、`ai_reports`、`job_runs`、`app_meta`、
以及帳號與上傳紀錄相關的資料表。
