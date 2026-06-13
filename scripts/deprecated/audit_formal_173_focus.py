#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from qlib.data import D
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.live.market_rules import AShareMarketRules

BASE = PROJECT_ROOT / "scratch/formal_173_compare"
OUT = PROJECT_ROOT / "reports/audits/formal_173_2025_01_focus"
OUT.mkdir(parents=True, exist_ok=True)


def board_of(symbol: str) -> str:
    s = str(symbol)
    if s.startswith("688"):
        return "STAR"
    if s.startswith("300"):
        return "ChiNext"
    if s.startswith("8") or s.startswith("4"):
        return "BSE"
    if s.endswith(".SH"):
        return "SSE Main"
    if s.endswith(".SZ"):
        return "SZSE Main"
    return "Unknown"


def load_trades() -> pd.DataFrame:
    df = pd.read_csv(BASE / "experiments/backtest_trades.csv")
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df[(df["date"] >= "2025-01-01") & (df["date"] <= "2025-01-31")].copy()


def prev_trading_day(value: str) -> str:
    ts = pd.Timestamp(value)
    cal = D.calendar(start_time=ts - pd.Timedelta(days=15), end_time=ts)
    prev = [pd.Timestamp(x) for x in cal if pd.Timestamp(x) < ts]
    return max(prev).strftime("%Y-%m-%d")


def _prove_last_train_label_visible() -> dict[str, Any]:
    feature_fields = FeatureLibrary.get_alpha158_extended_config()
    label_fields = ["(Ref($close, -5) / Ref($close, -1) - 1)"]
    signal_date = "2025-01-07"
    train_start = (pd.Timestamp(signal_date) - pd.DateOffset(years=4) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    dh_config = {
        "start_time": train_start,
        "end_time": signal_date,
        "instruments": "csi300",
        "infer_processors": [
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True, "fit_start_time": train_start, "fit_end_time": signal_date}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature", "fill_value": 0}},
        ],
        "learn_processors": [
            {"class": "DropnaLabel"},
            {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}},
        ],
        "data_loader": {"class": "QlibDataLoader", "kwargs": {"config": {"feature": (feature_fields, feature_fields), "label": (label_fields, label_fields)}}},
    }
    ds = DatasetH(handler={"class": "DataHandlerLP", "module_path": "qlib.data.dataset.handler", "kwargs": dh_config}, segments={"train": (train_start, signal_date)})
    df = ds.prepare("train", col_set=["label"], data_key=DataHandlerLP.DK_L)
    idx = pd.to_datetime(df.index.get_level_values("datetime"))
    last_rows = df[idx == pd.Timestamp(signal_date)]
    return {
        "proof_signal_date": signal_date,
        "proof_rows_on_train_end": int(len(last_rows)),
        "proof_non_na_labels_on_train_end": int(last_rows.iloc[:, 0].notna().sum()) if not last_rows.empty else 0,
        "proof_sample_labels": last_rows.iloc[:5, 0].tolist() if not last_rows.empty else [],
    }


def _actual_execution_dates() -> list[str]:
    daily_root = BASE / 'daily'
    return sorted([p.name for p in daily_root.iterdir() if p.is_dir() and '2025-01-01' <= p.name <= '2025-01-31'])


def build_leakage_table() -> tuple[pd.DataFrame, dict[str, Any]]:
    proof = _prove_last_train_label_visible()
    rows = []
    for execution_date in _actual_execution_dates():
        signal_date = prev_trading_day(execution_date)
        train_end = signal_date
        train_start = (pd.Timestamp(signal_date) - pd.DateOffset(years=4) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        last_train_sample_date = train_end
        max_label_date_used = (pd.Timestamp(last_train_sample_date) + pd.offsets.BDay(5)).strftime("%Y-%m-%d")
        rows.append({
            "signal_date": signal_date,
            "train_start": train_start,
            "train_end": train_end,
            "infer_date": signal_date,
            "label_horizon": "t+1_to_t+5_close_return",
            "max_feature_date_used": train_end,
            "max_label_date_used": max_label_date_used,
            "last_train_sample_date": last_train_sample_date,
            "is_label_mature_at_infer_time": False,
            "last_train_sample_label_non_na": True,
            "label_depends_on_post_infer_close": True,
            "preprocess_fit_start": train_start,
            "preprocess_fit_end": train_end,
            "preprocess_fit_on_full_data": False,
            "dataset_handler": "DataHandlerLP",
            "trainer_path": "QlibNativeModel.fit",
            "feature_set": "extended",
            "model_path": "data/models/qlib_lgbm_extended",
        })
    out = pd.DataFrame(rows)
    summary = {
        "feature_set": "extended",
        "model_path": "data/models/qlib_lgbm_extended",
        "dataset_handler": "DataHandlerLP",
        "trainer_path": "QlibNativeModel.fit",
        "label_depends_on_post_infer_close_count": int(out["label_depends_on_post_infer_close"].sum()),
        "preprocess_fit_on_full_data_count": int(out["preprocess_fit_on_full_data"].sum()),
        "handler_cache_future_pull_evidence": False,
        "173_2025_01_leaked": bool(out["label_depends_on_post_infer_close"].any()),
        **proof,
    }
    return out, summary


def build_execution_gap_table() -> tuple[pd.DataFrame, dict[str, Any]]:
    trades = load_trades()
    rules = AShareMarketRules()
    rows = []
    for date, grp in trades.groupby("date"):
        symbols = sorted(grp["symbol"].astype(str).unique())
        feats = rules.adapter.get_features(symbols, ["$open", "$close", "$high", "$low", "$high_limit", "$low_limit", "$paused", "$volume"], start_time=date, end_time=date)
        feat = feats.reset_index().rename(columns={"instrument": "symbol", "datetime": "date"}) if not feats.empty else pd.DataFrame()
        feat["date"] = pd.to_datetime(feat["date"]).dt.strftime("%Y-%m-%d") if not feat.empty else feat
        snap = feat.set_index("symbol").to_dict(orient="index") if not feat.empty else {}
        for row in grp.itertuples(index=False):
            s = snap.get(str(row.symbol), {})
            open_px = float(s.get("$open") or 0.0)
            high_px = float(s.get("$high") or 0.0)
            low_px = float(s.get("$low") or 0.0)
            high_lim = float(s.get("$high_limit") or 0.0)
            low_lim = float(s.get("$low_limit") or 0.0)
            volume = float(s.get("$volume") or 0.0)
            paused = bool(s.get("$paused") or False)
            filled = int(row.filled_amount)
            part = None if volume <= 0 else filled / volume
            side = str(row.side).lower()
            one_word_up = high_lim > 0 and abs(open_px - high_lim) < 1e-8 and abs(high_px - low_px) < 1e-8 and abs(high_px - high_lim) < 1e-8
            one_word_down = low_lim > 0 and abs(open_px - low_lim) < 1e-8 and abs(high_px - low_px) < 1e-8 and abs(low_px - low_lim) < 1e-8
            lot_valid = (filled % 100 == 0)
            suspicious = []
            if not lot_valid:
                suspicious.append("invalid_round_lot")
            if paused:
                suspicious.append("paused_but_filled")
            if side == "buy" and one_word_up:
                suspicious.append("one_word_limit_up_buy_filled")
            if side == "sell" and one_word_down:
                suspicious.append("one_word_limit_down_sell_filled")
            if part is not None and part > 0.1:
                suspicious.append("high_volume_participation_gt_10pct")
            rows.append({
                "date": str(row.date),
                "symbol": str(row.symbol),
                "side": side,
                "filled_amount": filled,
                "actual_trade_price": float(row.deal_price),
                "market": "SH" if str(row.symbol).endswith('.SH') else "SZ" if str(row.symbol).endswith('.SZ') else "UNK",
                "board": board_of(str(row.symbol)),
                "lot_size_rule_ok": lot_valid,
                "day_volume": volume,
                "participation_ratio": part,
                "paused": paused,
                "open_price": open_px,
                "high_price": high_px,
                "low_price": low_px,
                "high_limit": high_lim,
                "low_limit": low_lim,
                "one_word_limit_up": one_word_up,
                "one_word_limit_down": one_word_down,
                "reject_reason_or_should_fail_reason": "|".join(suspicious),
                "is_suspicious_fill": bool(suspicious),
            })
    out = pd.DataFrame(rows)
    summary = {
        "trade_count": int(len(out)),
        "suspicious_fill_count": int(out["is_suspicious_fill"].sum()) if not out.empty else 0,
        "top_reasons": out.loc[out["is_suspicious_fill"], "reject_reason_or_should_fail_reason"].value_counts().head(10).to_dict() if not out.empty else {},
    }
    return out, summary


def main() -> None:
    QlibAdapter().init_qlib()
    c_df, c_summary = build_leakage_table()
    a_df, a_summary = build_execution_gap_table()
    c_df.to_csv(OUT / "173_rolling_leakage_windows.csv", index=False)
    a_df.to_csv(OUT / "173_execution_realism_samples.csv", index=False)
    summary = {"C": c_summary, "A": a_summary}
    (OUT / "focus_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
