#!/usr/bin/env python3
"""Build csi1800_pit_v1 point-in-time membership artifact + csi1800_pit_union registry.

CSI1800 = CSI800 (000906.SH) ∪ CSI1000 (000852.SH).  The CSI800 leg reuses the
existing csi800_pit_v1 raw snapshots; the CSI1000 leg is pulled fresh from
Tushare index_weight (month-end trading day snapshots, 2014+ — the index
launched 2014-08-29, so earlier months return empty and are skipped).

Output layout is byte-identical to csi800_pit_v1 so ``PitUniverseStore`` reads
it unchanged:
    data/research/universes/csi1800_pit_v1/
    ├── manifest.json                 # provenance + hashes
    ├── membership.parquet            # spans table (index_code + 7 cols)
    └── raw/
        ├── index_weight_snapshots.parquet   # 000906 + 000852 merged
        ├── csi1000_snapshots.parquet        # 000852 leg (fresh pull)
        └── snapshot_dates.csv               # per-month-end n_constituents

Registry (data/qlib_bin/instruments/csi1800_pit_union.txt) is the PIT union of
both legs clipped to the 2018-2026 research window, same tab format as
csi800_pit_union.txt.

Usage:
    python scripts/data/build_csi1800_pit_v1.py            # pull (if missing) + build
    python scripts/data/build_csi1800_pit_v1.py --no-pull  # skip Tushare, only (re)build

Requires TUSHARE_TOKEN env or settings.yaml tushare_token.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ART_DIR = Path("data") / "research" / "universes" / "csi1800_pit_v1"
RAW_DIR = ART_DIR / "raw"
CSI800_RAW = Path("data") / "research" / "universes" / "csi800_pit_v1" / "raw" / "index_weight_snapshots.parquet"
CSI800_SNAPSHOTS = Path("data") / "research" / "universes" / "csi800_pit_v1" / "raw" / "snapshot_dates.csv"
REGISTRY = Path("data") / "qlib_bin" / "instruments" / "csi1800_pit_union.txt"
REGISTRY_WINDOW = ("2018-01-01", "2026-07-31")
INDEX_1000 = "000852.SH"
SOURCE = "tushare_index_weight"
SOURCE_DATE = "2026-08-21"
SOURCE_VERSION = "index_weight_monthly"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pull_csi1000() -> pd.DataFrame:
    """Pull 000852.SH month-end snapshots from Tushare, resuming via checkpoint."""
    from qsys.data.collector import TushareCollector

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ckpt = RAW_DIR / "csi1000_progress.json"
    out_path = RAW_DIR / "csi1000_snapshots.parquet"

    sd = pd.read_csv(CSI800_SNAPSHOTS)
    sd["snapshot_date"] = sd["snapshot_date"].astype(str).str.strip()
    dates = sorted(sd[sd["snapshot_date"] >= "20140101"]["snapshot_date"].tolist())

    done: set[str] = set()
    if ckpt.exists():
        done = set(json.loads(ckpt.read_text()))
    rows: list[dict] = []
    if out_path.exists():
        prev = pd.read_parquet(out_path)
        done |= set(prev["trade_date"].astype(str))
        rows = prev.to_dict("records")

    todo = [d for d in dates if d not in done]
    print(f"csi1000 pull: {len(done)} done, {len(todo)} to fetch")
    if not todo:
        return pd.read_parquet(out_path)

    c = TushareCollector()
    empty: list[str] = []
    for i, d in enumerate(todo):
        try:
            df = c.get_index_weights(INDEX_1000, trade_date=d)
            if df is not None and not df.empty:
                df["trade_date"] = df["trade_date"].astype(str)
                rows.extend(df[["index_code", "con_code", "trade_date", "weight"]].to_dict("records"))
                done.add(d)
            else:
                empty.append(d)
        except Exception as e:  # noqa: BLE001 — checkpoint lets a rerun resume
            empty.append(d)
            print(f"  ERR {d}: {e}")
        if (i + 1) % 25 == 0:
            pd.DataFrame(rows).to_parquet(out_path, index=False)
            ckpt.write_text(json.dumps(sorted(done)))
            print(f"  checkpoint {i+1}/{len(todo)} done={len(done)} empty={len(empty)}")

    pd.DataFrame(rows).to_parquet(out_path, index=False)
    ckpt.write_text(json.dumps(sorted(done)))
    print(f"csi1000 pull FINAL: done={len(done)} empty={len(empty)} rows={len(rows)} "
          f"(empty sample: {empty[:5]})")
    return pd.read_parquet(out_path)


def build_spans(raw: pd.DataFrame) -> pd.DataFrame:
    """Per-index contiguous-run spans (same diff semantics as csi800_pit_v1).

    Removal boundary = last snapshot still containing the name (conservative).
    """
    all_dates = sorted(raw["trade_date"].unique())
    spans: list[tuple] = []
    for index_code, leg in raw.groupby("index_code"):
        presence: dict[str, set[str]] = collections.defaultdict(set)
        for d, s in leg.groupby("trade_date")["con_code"].apply(set).items():
            for cc in s:
                presence[cc].add(d)
        for stock in sorted(presence):
            dates = sorted(presence[stock])
            start = prev = dates[0]
            for d in dates[1:]:
                idx = all_dates.index(prev)
                is_next = (idx + 1 < len(all_dates)) and all_dates[idx + 1] == d
                if not is_next:
                    spans.append((index_code, stock, start, prev))
                    start = d
                prev = d
            spans.append((index_code, stock, start, prev))
    sp = pd.DataFrame(spans, columns=["index_code", "instrument", "effective_from", "effective_to"])
    sp["source"] = SOURCE
    sp["source_date"] = SOURCE_DATE
    sp["source_version"] = SOURCE_VERSION
    return sp.sort_values(["instrument", "effective_from"]).reset_index(drop=True)


def build_artifact(raw: pd.DataFrame, *, pull_fresh: bool) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    merged = raw[["index_code", "con_code", "trade_date", "weight"]].copy()
    merged["trade_date"] = merged["trade_date"].astype(str)
    merged = merged.sort_values(["trade_date", "con_code"]).reset_index(drop=True)
    merged.to_parquet(RAW_DIR / "index_weight_snapshots.parquet", index=False)

    snaps = merged.groupby("trade_date")["con_code"].count()
    pd.DataFrame({"snapshot_date": snaps.index, "n_constituents": snaps.values}).to_csv(
        RAW_DIR / "snapshot_dates.csv", index=False
    )

    sp = build_spans(merged)
    sp.to_parquet(ART_DIR / "membership.parquet", index=False)

    raw_hash = _sha256(RAW_DIR / "index_weight_snapshots.parquet")
    mem_hash = _sha256(ART_DIR / "membership.parquet")
    manifest = {
        "universe_id": "csi1800_pit_v1",
        "index_code": "000906.SH,000852.SH",
        "index_name": "CSI1800 (CSI800 ∪ CSI1000)",
        "description": "Point-in-time CSI1800 membership reconstructed from Tushare index_weight monthly snapshots",
        "source": SOURCE,
        "source_endpoint": "pro.index_weight",
        "source_date": SOURCE_DATE,
        "snapshot_granularity": "monthly (month-end trading day, 1 snapshot/month)",
        "membership_dating_limitation": "membership changes are dated at month-end snapshot granularity (rebalance effect up to ~1 month delayed vs effective date)",
        "n_snapshots": int(merged["trade_date"].nunique()),
        "snapshot_date_range": [merged["trade_date"].min(), merged["trade_date"].max()],
        "n_unique_instruments": int(merged["con_code"].nunique()),
        "n_membership_spans": int(len(sp)),
        "constituents_per_snapshot": {"min": int(snaps.min()), "max": int(snaps.max())},
        "raw_source_hash": raw_hash,
        "membership_sha256": mem_hash,
        "normalization_version": "v1",
        "built_by": "PIT universe ladder diagnostic",
    }
    (ART_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"artifact written to {ART_DIR}: membership={len(sp)} rows, "
          f"raw_hash={raw_hash[:16]}, membership_sha256={mem_hash[:16]}")


def build_registry() -> None:
    from qsys.research.pit_universe import PitUniverseStore

    store = PitUniverseStore(ART_DIR)
    frame = store.to_registry_frame(*REGISTRY_WINDOW)
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(REGISTRY, sep="\t", header=False, index=False)
    print(f"registry written to {REGISTRY}: {len(frame)} rows, "
          f"{frame['instrument'].nunique()} instruments")


def main() -> None:
    p = argparse.ArgumentParser(description="Build csi1800_pit_v1 artifact + registry")
    p.add_argument("--no-pull", action="store_true", help="skip Tushare pull, only (re)build")
    args = p.parse_args()

    if args.no_pull:
        if not (RAW_DIR / "csi1000_snapshots.parquet").exists():
            raise SystemExit("csi1000_snapshots.parquet missing; run without --no-pull first")
        csi1000 = pd.read_parquet(RAW_DIR / "csi1000_snapshots.parquet")
    else:
        csi1000 = pull_csi1000()

    csi800 = pd.read_parquet(CSI800_RAW)
    raw = pd.concat([csi800, csi1000], ignore_index=True)
    build_artifact(raw, pull_fresh=not args.no_pull)
    build_registry()


if __name__ == "__main__":
    main()
