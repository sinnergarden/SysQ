"""Technical Composite V1 — OHLCV-derived cross-sectional composite signal.

For each trade_date:
- data_date = previous_trading_day(trade_date)
- use data observable at data_date only

Features (per instrument, cross-sectional ranked):
  momentum_20     = close / close.shift(20) - 1
  momentum_60     = close / close.shift(60) - 1
  reversal_5      = close / close.shift(5) - 1
  volume_confirm  = vol_ma_5 / vol_ma_20 - 1
  volatility_20   = rolling_std(ret_1d, 20)
  turnover_spike  = volume / vol_ma_20

Score:
  0.35 * cs_rank(momentum_20)
+ 0.25 * cs_rank(momentum_60)
- 0.20 * cs_rank(reversal_5)
+ 0.15 * cs_rank(volume_confirm)
- 0.15 * cs_rank(volatility_20)
- 0.10 * cs_rank(turnover_spike)

Uses cross-sectional rank per trade_date.
Data source is pluggable for testability.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd


def _cs_rank(s: pd.Series) -> pd.Series:
    """Cross-sectional rank, normalized to [-1, 1]."""
    r = s.rank(method="average")
    return (r / r.max() * 2 - 1).fillna(0.0)


def _previous_trading_day(trade_date: str, cal: set[str] | None = None) -> str:
    """Resolve previous trading day, preferring calendar when available."""
    if cal:
        candidates = sorted(d for d in cal if d < trade_date)
        if candidates:
            return candidates[-1]
    dt = pd.Timestamp(trade_date)
    prev = dt - timedelta(days=1)
    while prev.weekday() >= 5:  # Sat=5, Sun=6
        prev -= timedelta(days=1)
    return prev.strftime("%Y-%m-%d")


def _load_qlib_data(
    instruments: list[str],
    fields: list[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Load OHLCV data from qlib.

    Returns a DataFrame with MultiIndex (datetime, instrument) or empty.
    """
    try:
        from qlib.data import D
        df = D.features(instruments, fields, start_time=start_date, end_time=end_date)
        if df is not None and not df.empty:
            return df
    except Exception:
        pass
    return pd.DataFrame()


def default_data_loader(
    instruments: list[str] | None = None,
    start_date: str = "2000-01-01",
    end_date: str | None = None,
) -> pd.DataFrame:
    """Default data loader: fetch from qlib.

    Returns columns: trade_date, instrument, close, open, high, low, volume.
    """
    from qsys.config import cfg
    provider_uri = str(cfg.get_path("qlib_bin"))
    import qlib
    try:
        qlib.init(provider_uri=provider_uri, region="cn")
    except Exception:
        pass

    if instruments is None:
        # Read instruments directly from file (D.instruments may return dict)
        from qsys.config import cfg
        inst_file = cfg.get_path("qlib_bin") / "instruments" / "csi300.txt"
        if inst_file.exists():
            lines = inst_file.read_text().strip().split("\n")
            instruments = [line.split("\t")[0] for line in lines if line]
        else:
            instruments = []

    raw = _load_qlib_data(
        instruments,
        ["$close", "$open", "$high", "$low", "$volume"],
        start_date=start_date,
        end_date=end_date,
    )
    if raw.empty:
        return pd.DataFrame()

    df = raw.reset_index()
    df = df.rename(columns={"datetime": "trade_date"})

    # qlib returns MultiIndex columns; flatten to simple names
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    rename_map = {
        "$close": "close", "$open": "open",
        "$high": "high", "$low": "low", "$volume": "volume",
    }
    df = df.rename(columns=rename_map)
    df["trade_date"] = df["trade_date"].astype(str).str[:10]
    return df[["trade_date", "instrument", "close", "open", "high", "low", "volume"]]


@dataclass
class TechnicalCompositeV1Generator:
    """OHLCV-derived composite signal generator.

    Parameters
    ----------
    data_loader:
        Callable(instruments, start_date, end_date) -> DataFrame with columns
        trade_date, instrument, close, open, high, low, volume.
        When None, uses ``default_data_loader`` (qlib).
    momentum_short:
        Short momentum window (default 20).
    momentum_long:
        Long momentum window (default 60).
    reversal_days:
        Reversal window (default 5).
    volatility_days:
        Volatility window (default 20).
    volume_short:
        Short volume MA window (default 5).
    volume_long:
        Long volume MA window (default 20).
    """

    data_loader: Callable[..., pd.DataFrame] | None = None
    momentum_short: int = 20
    momentum_long: int = 60
    reversal_days: int = 5
    volatility_days: int = 20
    volume_short: int = 5
    volume_long: int = 20

    # Internal state
    _loaded_data: pd.DataFrame | None = field(default=None, repr=False)
    _data_start: str = field(default="", repr=False)

    def generate(
        self,
        *,
        train_start: str,
        train_end: str,
        predict_start: str,
        predict_end: str,
        signal_id: str,
        signal_run_id: str,
    ) -> pd.DataFrame:
        """Generate technical composite signal predictions."""
        loader = self.data_loader or default_data_loader

        # Load data with sufficient lookback for feature computation
        lookback_start = _previous_trading_day(
            predict_start, None
        )
        # We need max(lookback) days of history before predict_start
        max_window = max(self.momentum_long, self.volatility_days, self.volume_long) + 5
        lookback_dt = pd.Timestamp(predict_start) - timedelta(days=int(max_window * 1.4))
        data_start = lookback_dt.strftime("%Y-%m-%d")

        raw = loader(
            instruments=None,
            start_date=data_start,
            end_date=predict_end,
        )
        if raw.empty:
            raise RuntimeError(
                f"TechnicalCompositeV1: no data for range "
                f"[{data_start}, {predict_end}]"
            )

        raw = raw.sort_values(["instrument", "trade_date"]).reset_index(drop=True)

        # Build calendar lookup for data_date resolution
        all_dates = sorted(raw["trade_date"].unique())
        cal_set = set(all_dates)
        trade_date_to_dd: dict[str, str] = {}
        for i, d in enumerate(all_dates):
            trade_date_to_dd[d] = all_dates[i - 1] if i > 0 else d

        # Compute features per instrument
        feature_rows = []
        for inst, grp in raw.groupby("instrument"):
            grp = grp.sort_values("trade_date").copy()
            grp["ret_1d"] = grp["close"].pct_change(fill_method=None)
            grp["momentum_20"] = grp["close"] / grp["close"].shift(self.momentum_short) - 1
            grp["momentum_60"] = grp["close"] / grp["close"].shift(self.momentum_long) - 1
            grp["reversal_5"] = grp["close"] / grp["close"].shift(self.reversal_days) - 1
            grp["vol_ma_5"] = grp["volume"].rolling(self.volume_short).mean()
            grp["vol_ma_20"] = grp["volume"].rolling(self.volume_long).mean()
            grp["volume_confirm"] = grp["vol_ma_5"] / grp["vol_ma_20"] - 1
            grp["volatility_20"] = grp["ret_1d"].rolling(self.volatility_days).std()
            grp["turnover_spike"] = grp["volume"] / grp["vol_ma_20"].replace(0, np.nan)
            feature_rows.append(grp)

        if not feature_rows:
            raise RuntimeError("TechnicalCompositeV1: no feature rows computed")

        all_feats = pd.concat(feature_rows, ignore_index=True)

        # Filter to predict range
        pred_mask = (all_feats["trade_date"] >= predict_start) & (
            all_feats["trade_date"] <= predict_end
        )
        pred_feats = all_feats[pred_mask].copy()
        if pred_feats.empty:
            raise RuntimeError(
                f"TechnicalCompositeV1: no data in predict range "
                f"[{predict_start}, {predict_end}]"
            )

        # Cross-sectional rank per trade_date
        feature_cols = [
            "momentum_20", "momentum_60", "reversal_5",
            "volume_confirm", "volatility_20", "turnover_spike",
        ]
        row_count = len(pred_feats)
        dropped_count = 0

        for col in feature_cols:
            pred_feats[col] = pred_feats.groupby("trade_date")[col].transform(
                lambda s: s.fillna(s.mean()) if s.notna().any() else 0.0
            )
            pred_feats[f"csrank_{col}"] = pred_feats.groupby("trade_date")[col].transform(
                _cs_rank
            )

        # Composite score
        pred_feats["score"] = (
            0.35 * pred_feats["csrank_momentum_20"]
            + 0.25 * pred_feats["csrank_momentum_60"]
            - 0.20 * pred_feats["csrank_reversal_5"]
            + 0.15 * pred_feats["csrank_volume_confirm"]
            - 0.15 * pred_feats["csrank_volatility_20"]
            - 0.10 * pred_feats["csrank_turnover_spike"]
        )

        # Build output frame
        output_rows = []
        for _, row in pred_feats.iterrows():
            td = row["trade_date"]
            dd = trade_date_to_dd.get(td, _previous_trading_day(td, cal_set))
            output_rows.append({
                "trade_date": td,
                "data_date": dd,
                "instrument": str(row["instrument"]),
                "signal_id": signal_id,
                "signal_run_id": signal_run_id,
                "score": float(row["score"]),
            })

        result = pd.DataFrame(output_rows)
        # Record dropped count
        self._dropped_count = dropped_count
        self._row_count = row_count
        return result
