# 部署指南 — Solo PIXI MP Monitor Dashboard

正式環境：`sit@10.20.31.40:/mnt/md127/pixi-solo-dashboard`（容器 `pixi-test-*`，看板 port 8001，資料庫 port 5433）。
同一台主機上的 `iw-solo-pixi-essential`（port 8004）是另一套系統，**不要動**。

> 任何正式環境部署都要先在本機驗證，並經負責人同意後才執行。

## 1. 一般部署（建議）

在開發機的 repo 內執行：

```bash
cd solo-pixi-essential
export SSHPASS='…'                 # 用密碼登入時（需要安裝 sshpass）；有 SSH key 則不需要
scripts/deploy.sh --dry-run        # 先看會同步哪些檔案
scripts/deploy.sh                  # 正式部署
```

`deploy.sh` 依序做：

| 步驟 | 內容 |
|---|---|
| 1. 備份 | 一天一個資料夾 `oldbackup/<YYMMDD>/`（例如 `oldbackup/261020/`）：目前的檔案 → `files_<HHMMSS>.tgz`；資料庫 `pg_dump -Fc` → `pixi_test_<時間>.dump`（備份是空的就中止，不做任何變更） |
| 2. 同步 | `rsync` 整個 `solo-pixi-essential/`（不含伺服器上的 `.env`、`oldbackup/`、`dbbackup/`）。加 `--delete` 才會刪除伺服器上多出來的舊檔 |
| 3. 建置 | `docker compose up -d --build` |
| 4. 健康檢查 | 最多 2 分鐘：`/health` 必須回 `ok` 且版本號正確，首頁要能開 |
| 5. 失敗自動還原 | 還原 `files.tgz` 並重新 build，等舊版本恢復健康；資料庫不受影響 |

本機演練（不會用到 SSH）：準備一個目錄放 `.env`（例如 `PIXI_PREFIX=pixi-rehearsal`、`PIXI_HTTP_PORT=8002`、`PIXI_DB_PORT=5434`），再執行 `scripts/deploy.sh --local <目錄>`。

### 第一次部署 v3.x（v3.0.0 起）要注意
- API 啟動時會自動建立新物件：`mv_unit_day`、`mv_unit`（統計 view）、`idx_mt_eff_date`（索引）、`ai_reports`、`job_runs`、`app_meta`，以及帳號／上傳紀錄相關資料表。**`module_test` 欄位不變**（只在缺少時補上 `upload_id` 等早期版本加過的 nullable 欄位）。7 萬筆資料約 2 秒。
- 舊的單檔 `solo_pixi_dashboard.html`、`ai_summary_helper.py` 已不再使用，留在伺服器上無害；用 `--delete` 部署會移除。
- 部署後所有人需要重新登入一次。
- 伺服器上的 `.env`（若有）請比對 `.env.example` 補上新設定；不補也可以，全部都有預設值。

## 2. 每日資料庫備份（14 天保留）

正式機 2026-10-20 起已設定（使用者 `sit` 的 crontab）。新主機在伺服器加入 cron（`crontab -e`）：

```
0 2 * * *  /mnt/md127/pixi-solo-dashboard/scripts/backup_db.sh >> /mnt/md127/pixi-solo-dashboard/dbbackup/backup.log 2>&1
```

還原：

```bash
docker exec -i pixi-test-db pg_restore -U pixi -d pixi_test --clean --if-exists < dbbackup/pixi_test_<時間>.dump
```

## 3. 手動回復到上一版

```bash
cd /mnt/md127/pixi-solo-dashboard
ls oldbackup/*/                                 # 找到要回復的日期與時間
find . -mindepth 1 -maxdepth 1 ! -name oldbackup ! -name dbbackup ! -name .env -exec rm -rf {} +
tar xzf oldbackup/<YYMMDD>/files_<HHMMSS>.tgz
docker compose up -d --build
```

新增的資料表和 view 不影響舊版程式，可以保留。

回到 v3 之前的版本（commit `137e1151`）用 `oldbackup/261020/files_102401.tgz`；同一個資料夾也有那時的 DB 備份（`.sql` 和 `.dump`）。

## 部署紀錄

| 日期 | 版本 | 備份 | 驗收 |
|---|---|---|---|
| 2026-10-20 10:24 | 3.1.0（取代 `137e1151`） | `oldbackup/261020/` | 新舊數字 238/238 相同；中斷約 14 秒（`docs/test-report/v3.1.0/test_report.md` 第 10 節） |

## 4. 常用指令（伺服器上）

| 目的 | 指令 |
|---|---|
| 狀態 | `docker compose ps`；`curl http://localhost:8001/health` |
| API 日誌（含每個請求耗時、慢查詢） | `docker compose logs -f api` |
| 手動更新統計 | 看板右上「立即更新」，或 `curl -X POST http://localhost:8001/api/stats/refresh` |
| 重新啟動 | `docker compose restart api` |

## 5. 設定（`.env`，與 `docker-compose.yml` 同目錄）

完整清單見 `solo-pixi-essential/.env.example`。常用：`TWEAK_USER` / `TWEAK_PASS`（管理者）、`LLM_API_BASE` / `LLM_MODEL`、`STATS_REFRESH_SEC`（預設 300 秒）、`REPORT_DAILY_AT`（預設 18:00）、`REPORT_AUTO`。
