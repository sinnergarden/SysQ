from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.feature.definitions.common import resolve_data_root


INDEX_CODE_MAP = {
    "sse": "000001.SH",
    "hs300": "000300.SH",
    "zz500": "000905.SH",
    "zz1000": "000852.SH",
    "cyb": "399006.SZ",
    "kc50": "000688.SH",
}


def load_index_daily(index_name: str = "hs300", root: str | Path | None = None) -> pd.DataFrame:
    code = INDEX_CODE_MAP[index_name]
    base_dir = Path(root) if root is not None else resolve_data_root() / "raw" / "index"
    path = base_dir / f"{code}.csv"
    if not path.exists():
        return pd.DataFrame(columns=["trade_date", f"{index_name}_close"])

    df = pd.read_csv(path)
    if "trade_date" not in df.columns or "close" not in df.columns:
        return pd.DataFrame(columns=["trade_date", f"{index_name}_close"])

    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
    return df[["trade_date", "close"]].rename(columns={"close": f"{index_name}_close"})


def attach_index_context(df: pd.DataFrame, index_name: str = "hs300", root: str | Path | None = None) -> pd.DataFrame:
    out = df.copy()
    idx = load_index_daily(index_name=index_name, root=root)
    if idx.empty:
        out[f"{index_name}_close"] = pd.NA
        out["index_close"] = pd.NA
        return out

    out = out.merge(idx, on="trade_date", how="left")
    out["index_close"] = out[f"{index_name}_close"]
    return out
