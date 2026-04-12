-- Solo PIXI Module Test Database Schema
-- QCA9377 BT+WiFi production test analysis
-- PostgreSQL 16+

-- ─── Core test record table ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS module_test (
    id                  SERIAL PRIMARY KEY,

    -- Identity (from filename + log header)
    work_order          TEXT,
    mac1                TEXT NOT NULL,
    mac2                TEXT,
    tester_sn           TEXT,
    start_time          TIMESTAMP NOT NULL,
    end_time            TIMESTAMP,
    test_duration_sec   FLOAT,
    result              TEXT NOT NULL CHECK (result IN ('PASS','FAIL','STOP')),

    -- ── BT_TX_BDR (2402 MHz, 1DH1) ──
    bdr_freq_error      FLOAT,
    bdr_freq_drift      FLOAT,
    bdr_delta_f2_max    FLOAT,
    bdr_power           FLOAT,
    bdr_delta_f1_avg    FLOAT,
    bdr_delta_f2_f1_ratio FLOAT,
    bdr_pass            BOOLEAN,

    -- ── BT_TX_EDR1 (2441 MHz, 2DH1) ──
    edr1_devm_avg       FLOAT,
    edr1_devm_peak      FLOAT,
    edr1_power_diff     FLOAT,
    edr1_omega_i        FLOAT,
    edr1_omega_0        FLOAT,
    edr1_omega_i0       FLOAT,
    edr1_devm_99pct     FLOAT,
    edr1_power          FLOAT,
    edr1_pass           BOOLEAN,

    -- ── BT_TX_EDR2 (2480 MHz, 3DH1) ──
    edr2_devm_avg       FLOAT,
    edr2_devm_peak      FLOAT,
    edr2_power_diff     FLOAT,
    edr2_omega_i        FLOAT,
    edr2_omega_0        FLOAT,
    edr2_omega_i0       FLOAT,
    edr2_devm_99pct     FLOAT,
    edr2_power          FLOAT,
    edr2_pass           BOOLEAN,

    -- ── BT_TX_LE (2402 MHz, 1LE) ──
    le_freq_error       FLOAT,
    le_delta_f2_avg     FLOAT,
    le_delta_f2_max     FLOAT,
    le_delta_f0_fn_max  FLOAT,
    le_delta_f1_f0      FLOAT,
    le_delta_fn_fn5_max FLOAT,
    le_power            FLOAT,
    le_delta_f1_avg     FLOAT,
    le_f2_f1_ratio      FLOAT,
    le_pass             BOOLEAN,

    -- ── BT RX ──
    ber_2441            FLOAT,
    ber_2480            FLOAT,
    per_le              FLOAT,
    bt_rx_pass          BOOLEAN,

    -- ── WiFi Calibration ──
    xtal_cap            INT,
    xtal_freq_error_ppm FLOAT,
    cal_pass            BOOLEAN,

    -- ── WiFi 2.4G TX (worst EVM across channels, avg Power) ──
    wifi24_cck11_evm    FLOAT,
    wifi24_cck11_power  FLOAT,
    wifi24_ofdm54_evm   FLOAT,
    wifi24_ofdm54_power FLOAT,
    wifi24_ht20_evm     FLOAT,
    wifi24_ht20_power   FLOAT,
    wifi24_ht40_evm     FLOAT,
    wifi24_ht40_power   FLOAT,
    wifi24_tx_pass      BOOLEAN,

    -- ── WiFi 5G TX ──
    wifi5_ofdm54_evm    FLOAT,
    wifi5_ofdm54_power  FLOAT,
    wifi5_ht20_evm      FLOAT,
    wifi5_ht20_power    FLOAT,
    wifi5_ht40_evm      FLOAT,
    wifi5_ht40_power    FLOAT,
    wifi5_vht80_evm     FLOAT,
    wifi5_vht80_power   FLOAT,
    wifi5_tx_pass       BOOLEAN,

    -- ── WiFi RX PER ──
    wifi24_per_max      FLOAT,
    wifi24_rx_pass      BOOLEAN,
    wifi5_per_max       FLOAT,
    wifi5_rx_pass       BOOLEAN,

    -- ── Fail info ──
    fail_step_num       INT,
    fail_step_name      TEXT,
    fail_message        TEXT,
    fail_error_code     TEXT,
    fail_category       TEXT,

    -- ── Housekeeping ──
    raw_log             TEXT,
    file_hash           TEXT UNIQUE,
    source_file         TEXT,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ─── Indexes ────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_mt_start_time  ON module_test(start_time);
CREATE INDEX IF NOT EXISTS idx_mt_work_order  ON module_test(work_order);
CREATE INDEX IF NOT EXISTS idx_mt_result      ON module_test(result);
CREATE INDEX IF NOT EXISTS idx_mt_mac1        ON module_test(mac1);
CREATE INDEX IF NOT EXISTS idx_mt_wo_time     ON module_test(work_order, start_time);

-- ─── Views ──────────────────────────────────────────────────────────────────

-- Yield per work order
CREATE OR REPLACE VIEW v_yield_by_wo AS
SELECT
    work_order,
    COUNT(*)                                                              AS total,
    COUNT(*) FILTER (WHERE result = 'PASS')                               AS passed,
    COUNT(*) FILTER (WHERE result = 'FAIL')                               AS failed,
    COUNT(*) FILTER (WHERE result = 'STOP')                               AS stopped,
    ROUND(COUNT(*) FILTER (WHERE result = 'PASS') * 100.0
          / NULLIF(COUNT(*), 0), 2)                                       AS yield_pct,
    MIN(start_time)                                                       AS first_test,
    MAX(start_time)                                                       AS last_test
FROM module_test
GROUP BY work_order;

-- Fail step summary
CREATE OR REPLACE VIEW v_fail_summary AS
SELECT
    COALESCE(fail_step_name, 'Unknown') AS fail_step,
    fail_message,
    COUNT(*)                            AS occurrences
FROM module_test
WHERE result = 'FAIL'
GROUP BY fail_step_name, fail_message
ORDER BY occurrences DESC;

-- Retry units (same mac1 tested more than once)
CREATE OR REPLACE VIEW v_retry_units AS
SELECT
    mac1, mac2, work_order,
    COUNT(*)                                      AS attempts,
    COUNT(*) FILTER (WHERE result = 'PASS')       AS pass_count,
    COUNT(*) FILTER (WHERE result = 'FAIL')       AS fail_count,
    COUNT(*) FILTER (WHERE result = 'STOP')       AS stop_count,
    MIN(start_time)                               AS first_attempt,
    MAX(start_time)                               AS last_attempt
FROM module_test
GROUP BY mac1, mac2, work_order
HAVING COUNT(*) > 1
ORDER BY attempts DESC;
