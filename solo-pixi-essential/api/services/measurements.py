"""BT / WiFi / calibration / test-time distributions, binned in SQL.

The browser used to download every raw value (WiFi alone was ~18 MB) and bin it in
JavaScript. Here each histogram comes back as a few dozen counts. Bin widths and the
percentile method (nearest rank = percentile_disc) match the old client code, so the
charts and P90/P95 values are the same as before.
"""
import db
from services.filters import Scope, raw_where

MAX_BINS = 60

# name → (column, bin width, extra row condition)
HISTOGRAMS = {
    "bdr_power": ("bdr_power", 0.5, None),
    "edr1_devm": ("edr1_devm_avg", 0.5, None),
    "edr2_devm": ("edr2_devm_avg", 0.5, "edr1_devm_avg IS NOT NULL"),
    "xtal_cap": ("xtal_cap", 1, None),
    "xtal_ppm": ("xtal_freq_error_ppm", 0.5, "xtal_cap IS NOT NULL"),
}

EVM_COLUMNS = [
    ("2.4G CCK11", "wifi24_cck11_evm"), ("2.4G OFDM54", "wifi24_ofdm54_evm"),
    ("2.4G HT20", "wifi24_ht20_evm"), ("2.4G HT40", "wifi24_ht40_evm"),
    ("5G OFDM54", "wifi5_ofdm54_evm"), ("5G HT20", "wifi5_ht20_evm"),
    ("5G HT40", "wifi5_ht40_evm"), ("5G VHT80", "wifi5_vht80_evm"),
]


def _num(v, digits=4):
    return round(float(v), digits) if v is not None else None


def histogram(conn, scope: Scope, column, bin_width, condition=None):
    """{bin, start, labels, counts, n, clipped} with empty bins filled between min and max.

    If the data spans more than MAX_BINS bins, values outside the 0.5–99.5 percentile
    are folded into the first/last bin (`clipped` says how many)."""
    extra = [f"{column} IS NOT NULL"] + ([condition] if condition else [])
    where, params = raw_where(scope, extra)
    rows = db.fetch_all(conn, f"""
        SELECT FLOOR({column} / %s)::int AS b, COUNT(*)::int AS n
        FROM module_test {where} GROUP BY 1 ORDER BY 1
    """, [bin_width] + params)
    if not rows:
        return {"bin": bin_width, "labels": [], "counts": [], "n": 0, "clipped": 0}
    counts = {r["b"]: r["n"] for r in rows}
    lo, hi = rows[0]["b"], rows[-1]["b"]
    clipped = 0
    if hi - lo + 1 > MAX_BINS:
        p = db.fetch_one(conn, f"""
            SELECT percentile_disc(0.005) WITHIN GROUP (ORDER BY {column}) AS lo,
                   percentile_disc(0.995) WITHIN GROUP (ORDER BY {column}) AS hi
            FROM module_test {where}
        """, params)
        lo_c = int(p["lo"] // bin_width)
        hi_c = max(lo_c, int(p["hi"] // bin_width))
        if hi_c - lo_c + 1 > MAX_BINS:
            hi_c = lo_c + MAX_BINS - 1
        folded = {}
        for b, n in counts.items():
            target = min(max(b, lo_c), hi_c)
            if target != b:
                clipped += n
            folded[target] = folded.get(target, 0) + n
        counts, lo, hi = folded, lo_c, hi_c
    labels, values = [], []
    for b in range(lo, hi + 1):
        start = b * bin_width
        labels.append(f"{start:.1f}" if isinstance(bin_width, float) else str(start))
        values.append(counts.get(b, 0))
    return {"bin": bin_width, "labels": labels, "counts": values, "n": sum(values), "clipped": clipped}


def bt(conn, scope: Scope):
    where, params = raw_where(scope)
    summary = db.fetch_one(conn, f"""
        SELECT COUNT(*) FILTER (WHERE bt_rx_pass = true)  AS bt_rx_passed,
               COUNT(*) FILTER (WHERE bt_rx_pass = false) AS bt_rx_failed,
               COUNT(*) FILTER (WHERE bdr_pass = true)    AS bdr_passed,
               COUNT(*) FILTER (WHERE bdr_pass = false)   AS bdr_failed,
               COUNT(*) FILTER (WHERE le_pass = true)     AS le_passed,
               COUNT(*) FILTER (WHERE le_pass = false)    AS le_failed,
               AVG(ber_2441) AS avg_ber_2441, AVG(ber_2480) AS avg_ber_2480, AVG(per_le) AS avg_per_le
        FROM module_test {where}
    """, params)
    return {
        "summary": summary,
        "hist": {name: histogram(conn, scope, *HISTOGRAMS[name]) for name in ("bdr_power", "edr1_devm", "edr2_devm")},
    }


def wifi(conn, scope: Scope):
    w24, p24 = raw_where(scope, ["wifi24_cck11_evm IS NOT NULL"])
    avg_24 = db.fetch_one(conn, f"""
        SELECT AVG(wifi24_cck11_evm) AS cck11, AVG(wifi24_ofdm54_evm) AS ofdm54,
               AVG(wifi24_ht20_evm) AS ht20, AVG(wifi24_ht40_evm) AS ht40
        FROM module_test {w24}
    """, p24)
    w5, p5 = raw_where(scope, ["wifi5_ofdm54_evm IS NOT NULL"])
    avg_5 = db.fetch_one(conn, f"""
        SELECT AVG(wifi5_ofdm54_evm) AS ofdm54, AVG(wifi5_ht20_evm) AS ht20,
               AVG(wifi5_ht40_evm) AS ht40, AVG(wifi5_vht80_evm) AS vht80
        FROM module_test {w5}
    """, p5)
    # Tail EVM over the rows the old page used (wifi24_cck11_evm present), nearest-rank percentiles.
    pct = ", ".join(
        f"percentile_disc(0.9) WITHIN GROUP (ORDER BY {col}) AS p90_{i}, "
        f"percentile_disc(0.95) WITHIN GROUP (ORDER BY {col}) AS p95_{i}"
        for i, (_, col) in enumerate(EVM_COLUMNS))
    row = db.fetch_one(conn, f"SELECT {pct} FROM module_test {w24}", p24) or {}
    tail = [{"label": label, "p90": _num(row.get(f"p90_{i}")), "p95": _num(row.get(f"p95_{i}"))}
            for i, (label, _) in enumerate(EVM_COLUMNS)]
    return {
        "avg_evm_24g": avg_24,
        "avg_evm_5g": avg_5,
        "tail": tail,
        "hist": {name: histogram(conn, scope, *HISTOGRAMS[name]) for name in ("xtal_cap", "xtal_ppm")},
    }


def other(conn, scope: Scope, retry_counts):
    where, params = raw_where(scope, ["test_duration_sec IS NOT NULL", "test_duration_sec > 0"])
    by_result = db.fetch_all(conn, f"""
        SELECT result, COUNT(*) AS count, AVG(test_duration_sec) AS avg,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY test_duration_sec)  AS p50,
               percentile_disc(0.9) WITHIN GROUP (ORDER BY test_duration_sec)  AS p90,
               percentile_disc(0.95) WITHIN GROUP (ORDER BY test_duration_sec) AS p95,
               percentile_disc(0.99) WITHIN GROUP (ORDER BY test_duration_sec) AS p99
        FROM module_test {where} GROUP BY result ORDER BY result
    """, params)
    overall = db.fetch_one(conn, f"""
        SELECT COUNT(*) AS count, AVG(test_duration_sec) AS avg,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY test_duration_sec)  AS p50,
               percentile_disc(0.9) WITHIN GROUP (ORDER BY test_duration_sec)  AS p90,
               percentile_disc(0.95) WITHIN GROUP (ORDER BY test_duration_sec) AS p95,
               percentile_disc(0.99) WITHIN GROUP (ORDER BY test_duration_sec) AS p99
        FROM module_test {where}
    """, params)
    w, p = raw_where(scope, ["xtal_freq_error_ppm IS NOT NULL"])
    ppm_trend = db.fetch_all(conn, f"""
        SELECT COALESCE(unit_date, start_time::date) AS day, COUNT(*) AS n,
               AVG(xtal_freq_error_ppm) AS avg, MIN(xtal_freq_error_ppm) AS min, MAX(xtal_freq_error_ppm) AS max
        FROM module_test {w} GROUP BY 1 ORDER BY 1
    """, p)
    for r in by_result + [overall] + ppm_trend:
        for k in ("avg", "p50", "p90", "p95", "p99", "min", "max"):
            if k in r:
                r[k] = _num(r[k], 3)
    drift = None
    if ppm_trend:
        drift = _num(max(r["max"] for r in ppm_trend) - min(r["min"] for r in ppm_trend), 3)
    return {"duration": {"overall": overall, "by_result": by_result},
            "retry": retry_counts, "ppm_trend": ppm_trend, "ppm_drift": drift}
