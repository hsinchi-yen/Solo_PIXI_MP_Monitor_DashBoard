# Tests

| Suite | Needs | Command |
|---|---|---|
| Unit (Python) | Python 3.10+. FastAPI tests run only where `api/requirements.txt` is installed | `python tests/test_unit.py` |
| Unit (JavaScript) | Node.js 22+ | `node --test "tests/js/*.test.mjs"` (also run by `test_unit.py` when Node is present) |
| Integration | Running stack, `docker` on PATH, admin credentials; `rawlogs/*.zip` for upload tests; LLM for AI tests | `PIXI_BASE_URL=http://localhost:8001 python tests/test_integration.py` |
| Browser E2E | Running stack + `pip install playwright` (Chrome, or `python -m playwright install chromium`) | `python tests/test_e2e.py [--shots DIR]` |

CI (`.github/workflows/tests.yml`) runs both unit suites on every push.

Integration and E2E tests create their own data (a temporary account, uploads of the two
sample work orders, AI reports) and remove it again. Upload tests skip themselves when the
sample work orders already exist in the database.

Environment: `PIXI_BASE_URL` (default `http://localhost:8001`), `TWEAK_USER` / `TWEAK_PASS`,
`PIXI_DB_CONTAINER` (default `pixi-test-db`), `PIXI_BROWSER_CHANNEL` (default `chrome`),
`PIXI_SHOTS` (screenshot folder, default `docs/test-report/v3.1.0/screenshots`; the v3.0.0 set in
`docs/test-report/screenshots` is kept as that release's record).
