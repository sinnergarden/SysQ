#!/usr/bin/env python3
"""Build csi_liquid_pit_v1 — PIT liquid A-share membership artifact (U3).

This is a DIAGNOSTIC universe for the universe-ladder study, NOT a production
universe.  The ST screen uses the CURRENT stock_basic name snapshot as an
approximation (a listed name flagged ST today is excluded for the entire
history) — it is not a strict PIT ST reconstruction.

Source: canonical daily (data/canonical/daily/*.feather, all 5576 A-shares) +
Tushare stock_basic (list_date, name) + trading calendar.  Never uses today's
index membership as the eligible set.

Documented eligibility rule (all point-in-time as of t, no lookahead):

| Sub-rule          | Definition                                                      | Mechanism |
|-------------------|-----------------------------------------------------------------|-----------|
| Base universe     | all.txt (5576) ∩ canonical daily                                | registry  |
| Non-ST / 退        | current stock_basic name contains "ST" or "退"                  | excluded entirely (not in spans) |
| Minimum listing   | t >= list_date + 365 calendar days                              | span effective_from |
| Upper bound       | last canonical bar (<= 2026-07-31)                              | span effective_to |
| Abnormal susp.    | >=10 consecutive calendar-trading days with no valid bar        | per-day exclusion rows |
| Liquidity         | trailing-20-trading-day mean amount >= 5e7 AND mean turnover_rate >= 0.005 | per-day exclusion rows |

spans table holds only the slow-moving rules (one span per eligible stock);
liquidity_exclusions.parquet holds per-day (trade_date, instrument) rows that
the generator anti-joins after the span filter.

Output layout matches csi800_pit_v1 so PitUniverseStore reads it unchanged:
    data/research/universes/csi_liquid_pit_v1/
    ├── manifest.json
    ├── membership.parquet
    └── raw/
        ├── liquidity_exclusions.parquet
        └── filter_histogram.csv          # excluded-rows-per-date audit
"""
from __future__ import annotations

import hashlib
import json
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ART_DIR = Path("data") / "research" / "universes" / "csi_liquid_pit_v1"
RAW_DIR = ART_DIR / "raw"
CANON_DIR = Path("data") / "canonical" / "daily"
STOCK_BASIC = Path("data") / "tushare" / "stock_basic.parquet"
ALL_REG = Path("data") / "qlib_bin" / "instruments" / "all.txt"
SOURCE = "canonical_daily+stock_basic"
SOURCE_DATE = "2026-08-21"
SOURCE_VERSION = "liquid_pit_v1"

# ── U3 thresholds (user-confirmed) — keep as top-of-file constants ──
MIN_LISTING_DAYS = 365          # calendar days after list_date
SUSPENSION_RUN_DAYS = 10        # consecutive calendar-trading days without a bar
LIQ_WINDOW = 20                 # trailing trading days for the mean
LIQ_AMOUNT_MIN = 5e7            # CNY
LIQ_TURNOVER_MIN = 0.005        # 0.5%
# ──────────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_meta() -> tuple[pd.DataFrame, set[str], set[str]]:
    sb = pd.read_parquet(STOCK_BASIC)
    sb["list_date"] = pd.to_datetime(sb["list_date"], format="%Y%m%d")
    sb["st"] = sb["name"].astype(str).str.contains("ST|退", na=False)
    st_set = set(sb.loc[sb["st"], "ts_code"])
    reg = pd.read_csv(ALL_REG, sep="\t", header=None)[0].unique()
    return sb, st_set, set(reg)


def build_spans_and_exclusions() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (spans, exclusions, filter_histogram)."""
    from qsys.data.calendar import get_trading_calendar

    sb, st_set, reg_set = load_meta()
    cal = get_trading_calendar("2007-01-01", "2026-08-21")  # "%Y-%m-%d", sorted
    span_rows: list[dict] = []
    excl_rows: list[dict] = []
    hist: dict[str, int] = {}
    eligible_count = 0

    files = sorted(CANON_DIR.glob("*.feather"))
    for fi, f in enumerate(files):
        inst = f.name[:-8]  # strip '.feather'
        if inst not in reg_set or inst in st_set:
            continue
        meta = sb[sb["ts_code"] == inst]
        if meta.empty:
            continue
        list_date = meta.iloc[0]["list_date"]
        df = pd.read_feather(f, columns=["trade_date", "close", "amount", "turnover_rate"])
        df = df[pd.to_datetime(df["trade_date"]).notna()]
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        df = df.sort_values("trade_date")

        # valid bars = close not NaN and bar present
        valid = df[df["close"].notna()].copy()
        if valid.empty:
            continue
        first_bar = valid["trade_date"].iloc[0]
        last_bar = valid["trade_date"].iloc[-1]
        eff_from = max(list_date + pd.Timedelta(days=MIN_LISTING_DAYS), pd.Timestamp(first_bar))
        eff_from_s = eff_from.strftime("%Y-%m-%d")
        if eff_from_s > last_bar:
            continue  # listed <365d at delist — not eligible in any span
        eligible_count += 1
        span_rows.append({
            "index_code": "ALL",
            "instrument": inst,
            "effective_from": eff_from_s.replace("-", ""),
            "effective_to": last_bar.replace("-", ""),
            "source": SOURCE,
            "source_date": SOURCE_DATE,
            "source_version": SOURCE_VERSION,
        })

        # ── per-day exclusion: suspension runs + liquidity ──
        # Restrict to the span interval [eff_from, last_bar].
        # 1) suspension: run of >=SUSPENSION_RUN_DAYS consecutive calendar
        #    trading days with no valid bar (missing row or NaN close).
        valid_days = set(valid["trade_date"])
        lo, hi = bisect_left(cal, eff_from_s), bisect_right(cal, last_bar)
        run = 0
        for d in cal[lo:hi]:
            if d in valid_days:
                run = 0
            else:
                run += 1
                if run >= SUSPENSION_RUN_DAYS:
                    excl_rows.append({"trade_date": d, "instrument": inst})
                    hist[d] = hist.get(d, 0) + 1

        # 2) liquidity: trailing LIQ_WINDOW valid bars mean amount/turnover.
        #    Only days that have a valid bar get evaluated; a stock inside a
        #    long suspension is already excluded above.
        amt_ma = valid["amount"].rolling(LIQ_WINDOW, min_periods=LIQ_WINDOW).mean()
        tr_ma = valid["turnover_rate"].rolling(LIQ_WINDOW, min_periods=LIQ_WINDOW).mean()
        liq_fail = (amt_ma < LIQ_AMOUNT_MIN) | (tr_ma < LIQ_TURNOVER_MIN)
        for d in valid.loc[liq_fail, "trade_date"]:
            excl_rows.append({"trade_date": d, "instrument": inst})
            hist[d] = hist.get(d, 0) + 1

        if (fi + 1) % 500 == 0:
            print(f"  {fi+1}/{len(files)} processed, eligible={eligible_count}, excl_rows={len(excl_rows)}")

    spans = pd.DataFrame(span_rows, columns=[
        "index_code", "instrument", "effective_from", "effective_to",
        "source", "source_date", "source_version",
    ])
    exclusions = pd.DataFrame(excl_rows, columns=["trade_date", "instrument"])
    if exclusions.empty:
        exclusions = pd.DataFrame(columns=["trade_date", "instrument"])
    hist_df = pd.DataFrame(
        [{"trade_date": k, "n_excluded": v} for k, v in sorted(hist.items())]
    )
    print(f"eligible instruments: {eligible_count}, spans: {len(spans)}, exclusions: {len(exclusions)}")
    return spans, exclusions, hist_df


def build_artifact() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    spans, exclusions, hist = build_spans_and_exclusions()
    spans.to_parquet(ART_DIR / "membership.parquet", index=False)
    exclusions.to_parquet(RAW_DIR / "liquidity_exclusions.parquet", index=False)
    hist.to_csv(RAW_DIR / "filter_histogram.csv", index=False)

    mem_hash = _sha256(ART_DIR / "membership.parquet")
    exc_hash = _sha256(RAW_DIR / "liquidity_exclusions.parquet")
    raw_hash = hashlib.sha256()
    for p in (STOCK_BASIC, ALL_REG):
        raw_hash.update(p.read_bytes())
    manifest = {
        "universe_id": "csi_liquid_pit_v1",
        "description": "PIT liquid A-share universe (all.txt minus current-ST, min listing 365d, suspension-run + trailing-20d liquidity exclusions) — DIAGNOSTIC ONLY, not a production universe",
        "source": SOURCE,
        "source_date": SOURCE_DATE,
        "raw_source_hash": raw_hash.hexdigest(),
        "st_approximation": "current stock_basic name snapshot, not strict PIT ST history",
        "rules": {
            "min_listing_days": MIN_LISTING_DAYS,
            "suspension_run_days": SUSPENSION_RUN_DAYS,
            "liquidity_window_trading_days": LIQ_WINDOW,
            "liquidity_amount_min_cny": LIQ_AMOUNT_MIN,
            "liquidity_turnover_min": LIQ_TURNOVER_MIN,
            "liquidity_mean_definition": "rolling over valid bars (suspension days excluded from the trailing-20 mean; long suspensions already excluded by the run rule)",
        },
        "n_eligible_instruments": int(len(spans)),
        "n_membership_spans": int(len(spans)),
        "n_exclusion_rows": int(len(exclusions)),
        "snapshot_date_range": [spans["effective_from"].min(), spans["effective_to"].max()],
        "membership_sha256": mem_hash,
        "exclusions_sha256": exc_hash,
        "built_by": "PIT universe ladder diagnostic",
    }
    (ART_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"artifact written to {ART_DIR}")
    print(f"  spans={len(spans)}, exclusions={len(exclusions)}, "
          f"membership_sha256={mem_hash[:16]}, raw_source_hash={raw_hash.hexdigest()[:16]}")


if __name__ == "__main__":
    build_artifact()
