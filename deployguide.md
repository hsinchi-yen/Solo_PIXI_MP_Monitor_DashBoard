# Deploy Guide — Solo PIXI MP Monitor Dashboard

> 適用版本：commit `32dab2ec`（feat: Local LLM AI Report, Aligned KPI fix, metallic UI）
> 目標：安全更新遠端機器，**DB 資料不受影響**

---

## 本次更新的檔案清單

| 遠端路徑（相對於專案根目錄）| 類型 | 說明 |
|---|---|---|
| `solo-pixi-essential/api/app.py` | 修改 | 後端 API |
| `solo-pixi-essential/ai_summary_helper.py` | **新增** ⚠️ 必須存在 | LLM prompt helper |
| `solo-pixi-essential/solo_pixi_dashboard.html` | 修改 | Dashboard UI |
| `solo-pixi-essential/docker-compose.yml` | 修改 | 新增 ai_summary_helper volume mount |

> `ai_summary_helper.py` 是全新檔案，**必須先複製到位**，否則 api 容器重啟會失敗。

---

## 方法一：遠端機有 Git（推薦）

```bash
# 1. 進入專案目錄
cd /path/to/Solo_PIXI_MP_Monitor_DashBoard

# 2. 拉取最新版本
git pull origin main

# 3. 安全重啟（只動 api + nginx，postgres 保持運行）
cd solo-pixi-essential
docker compose up -d --no-deps api nginx

# 4. 確認容器狀態
docker compose ps
```

---

## 方法二：手動複製檔案（SCP / SFTP）

### Step 1 — 複製 4 個檔案到遠端

```bash
# 從本機執行（替換 user@remote-ip 與路徑）
REMOTE="user@10.20.30.xx"
REMOTE_DIR="/path/to/Solo_PIXI_MP_Monitor_DashBoard/solo-pixi-essential"

scp solo-pixi-essential/api/app.py               ${REMOTE}:${REMOTE_DIR}/api/app.py
scp solo-pixi-essential/ai_summary_helper.py      ${REMOTE}:${REMOTE_DIR}/ai_summary_helper.py
scp solo-pixi-essential/solo_pixi_dashboard.html  ${REMOTE}:${REMOTE_DIR}/solo_pixi_dashboard.html
scp solo-pixi-essential/docker-compose.yml        ${REMOTE}:${REMOTE_DIR}/docker-compose.yml
```

### Step 2 — 在遠端機執行重啟

```bash
cd /path/to/Solo_PIXI_MP_Monitor_DashBoard/solo-pixi-essential

# 只重啟 api 和 nginx，postgres 完全不動
docker compose up -d --no-deps api nginx
```

---

## 為什麼這樣做是安全的

```
┌─────────────────────────────────────────────────────┐
│  docker compose 服務架構                              │
│                                                      │
│  ┌──────────────┐   不動  ┌──────────────────────┐   │
│  │   postgres   │ ──────► │  pixi-pgdata (Volume) │  │
│  │  pixi-test-db│         │  /var/lib/postgresql  │  │
│  └──────────────┘         └──────────────────────┘  │
│         ▲  DB 資料在 Named Volume，與容器生命週期無關    │
│                                                      │
│  ┌──────────────┐  重啟   ← api/app.py       (mount) │
│  │     api      │         ← ai_summary_helper.py     │
│  │ pixi-test-api│         ← solo_pixi_dashboard.html │
│  └──────────────┘                                    │
│                                                      │
│  ┌──────────────┐  重啟                              │
│  │    nginx     │                                    │
│  │pixi-test-nginx                                    │
│  └──────────────┘                                    │
└─────────────────────────────────────────────────────┘
```

- `postgres` 容器使用 **Named Volume** (`pixi-pgdata`)，資料與容器無關，容器重啟或刪除都不會損失資料
- `api` 的應用程式碼全部透過 **volume mount** 注入（非 image 內）—— 更新檔案後只需 restart，**不需要重新 build image**
- `docker compose up -d --no-deps api nginx`：`--no-deps` 確保不會連帶重啟 postgres

---

## 指令說明

| 指令 | 用途 |
|---|---|
| `docker compose up -d --no-deps api nginx` | 根據最新 compose 設定重啟指定服務（**本次推薦**，因 compose.yml 有異動）|
| `docker compose restart api nginx` | 僅重啟容器，不重讀 compose 設定（若只有改程式碼可用）|
| `docker compose ps` | 查看所有容器狀態 |
| `docker compose logs -f api` | 即時查看 api 容器 log |
| `docker compose logs --tail=50 api` | 查看最近 50 行 api log |

---

## 重啟後驗證

```bash
# 1. 確認容器都是 Up 狀態
docker compose ps

# 2. 確認 api health check 通過（status 應為 healthy）
docker compose ps api

# 3. 快速測試 API
curl http://localhost:8001/health

# 4. 確認 ai_summary_helper 有正確掛載
docker exec pixi-test-api python -c "import ai_summary_helper; print('OK')"
```

---

## 若 api 容器起不來（排錯）

```bash
# 查看啟動 log
docker compose logs api

# 常見原因：ai_summary_helper.py 未複製到位
ls -la /path/to/solo-pixi-essential/ai_summary_helper.py

# 強制重建（不影響 DB）
docker compose up -d --no-deps --force-recreate api nginx
```

---

> DB 備份建議（可選，預防萬一）：
> ```bash
> docker exec pixi-test-db pg_dump -U pixi pixi_test > backup_$(date +%Y%m%d).sql
> ```
