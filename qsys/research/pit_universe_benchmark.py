"""Auditable synthetic benchmark for a point-in-time equity universe."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from qsys.research.pit_universe import PitUniverseStore
from qsys.research.portfolio_analytics import write_portfolio_analytics


SCHEMA_VERSION = "pit_universe_benchmark_v1"
CONFIG_SCHEMA_VERSION = "pit_universe_benchmark_config_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is not a regular file: {path}")


def _normalize_dates(values: pd.Series) -> pd.Series:
    dates = pd.to_datetime(values.astype(str), errors="coerce")
    if dates.isna().any():
        raise ValueError("market data contains invalid trade_date values")
    return dates.dt.normalize()


def write_pit_universe_benchmark(
    *,
    benchmark_id: str,
    universe_artifact: str | Path,
    canonical_data_root: str | Path,
    calendar_csv: str | Path,
    output_dir: str | Path,
    start_date: str,
    end_date: str,
    holdout_start: str,
    min_constituent_coverage: float = 0.95,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a prior-float-cap-weighted total-return proxy without lookahead.

    Constituents and weights are both selected from strictly-prior information:
    prior-calendar-day PIT membership and the latest prior observed ``circ_mv``.
    Realized returns use
    adjusted close-to-close changes, except for the first day where open-to-close
    matches the cached-signal portfolio analytics convention. A missing quote for
    an already weighted suspended constituent contributes zero until its next
    observed quote.
    """
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    holdout = pd.Timestamp(holdout_start).normalize()
    if start > end:
        raise ValueError("benchmark start_date is after end_date")
    if end >= holdout:
        raise ValueError("benchmark range overlaps declared holdout")
    if not 0.0 < min_constituent_coverage <= 1.0:
        raise ValueError("min_constituent_coverage must be in (0, 1]")

    universe_dir = Path(universe_artifact).resolve()
    market_root = Path(canonical_data_root).resolve()
    calendar_path = Path(calendar_csv).resolve()
    target = Path(output_dir).resolve()
    universe_manifest_path = universe_dir / "manifest.json"
    membership_path = universe_dir / "membership.parquet"
    for path, label in (
        (universe_manifest_path, "universe manifest"),
        (membership_path, "universe membership"),
        (calendar_path, "benchmark calendar"),
    ):
        _regular_file(path, label)
    if market_root.is_symlink() or not market_root.is_dir():
        raise ValueError(f"canonical data root is not a regular directory: {market_root}")
    if target.is_symlink():
        raise ValueError(f"benchmark output directory is a symlink: {target}")
    if target.exists() and not target.is_dir():
        raise ValueError(f"benchmark output path is not a directory: {target}")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"benchmark output directory is not empty: {target}")

    calendar = pd.read_csv(calendar_path)
    if "trade_date" not in calendar.columns:
        raise ValueError("benchmark calendar CSV missing trade_date")
    calendar_dates = _normalize_dates(calendar["trade_date"])
    dates = pd.DatetimeIndex(
        sorted(calendar_dates[(calendar_dates >= start) & (calendar_dates <= end)].unique())
    )
    if dates.empty or dates[0] != start or dates[-1] != end:
        raise ValueError("benchmark calendar does not exactly cover requested endpoints")
    if dates.duplicated().any():
        raise ValueError("benchmark calendar contains duplicate trade dates")

    store = PitUniverseStore(universe_dir)
    membership_start = start - pd.Timedelta(days=1)
    membership_end = end - pd.Timedelta(days=1)
    instruments = store.membership_window(
        membership_start.strftime("%Y%m%d"), membership_end.strftime("%Y%m%d")
    )
    if not instruments:
        raise ValueError("PIT universe is empty over benchmark range")

    source_rows: list[dict[str, Any]] = []
    in_range: list[pd.DataFrame] = []
    prior_cap: dict[str, float] = {}
    prior_adjusted_close: dict[str, float] = {}
    required_columns = ["trade_date", "open", "close", "factor", "circ_mv"]
    calendar_set = set(dates)
    for instrument in instruments:
        path = market_root / f"{instrument}.feather"
        if not path.exists():
            source_rows.append({"instrument": instrument, "status": "missing"})
            continue
        _regular_file(path, f"canonical market data for {instrument}")
        frame = pd.read_feather(path, columns=required_columns)
        frame = frame.copy()
        frame["trade_date"] = _normalize_dates(frame["trade_date"])
        if frame["trade_date"].duplicated().any():
            raise ValueError(f"duplicate canonical trade_date for {instrument}")
        for column in ("open", "close", "factor", "circ_mv"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.sort_values("trade_date")
        source_rows.append({
            "instrument": instrument,
            "status": "loaded",
            "path": str(path),
            "sha256": _sha256(path),
            "row_count": int(len(frame)),
            "date_min": (
                frame["trade_date"].min().strftime("%Y-%m-%d") if not frame.empty else None
            ),
            "date_max": (
                frame["trade_date"].max().strftime("%Y-%m-%d") if not frame.empty else None
            ),
        })
        valid = frame[
            frame["close"].gt(0)
            & frame["factor"].gt(0)
            & frame["circ_mv"].gt(0)
        ].copy()
        before = valid[valid["trade_date"] < start]
        if not before.empty:
            row = before.iloc[-1]
            prior_cap[instrument] = float(row["circ_mv"])
            prior_adjusted_close[instrument] = float(row["close"] * row["factor"])
        current = valid[valid["trade_date"].isin(calendar_set)].copy()
        if not current.empty:
            current.insert(0, "instrument", instrument)
            in_range.append(current)

    updates = (
        pd.concat(in_range, ignore_index=True)
        .sort_values(["trade_date", "instrument"])
        .reset_index(drop=True)
        if in_range
        else pd.DataFrame(columns=["instrument", *required_columns])
    )
    grouped = iter(updates.groupby("trade_date", sort=True))
    next_group = next(grouped, None)
    benchmark_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    previous_level = 1000.0

    for index, date in enumerate(dates):
        group = None
        if next_group is not None and pd.Timestamp(next_group[0]) == date:
            group = next_group[1]
            next_group = next(grouped, None)
        current_returns: dict[str, float] = {}
        updates_after_close: list[tuple[str, float, float]] = []
        if group is not None:
            for row in group.itertuples(index=False):
                adjusted_close = float(row.close * row.factor)
                if index == 0 and math.isfinite(row.open) and row.open > 0:
                    realized_return = float(row.close / row.open - 1.0)
                else:
                    previous = prior_adjusted_close.get(row.instrument)
                    realized_return = (
                        float(adjusted_close / previous - 1.0)
                        if previous is not None and previous > 0
                        else math.nan
                    )
                if math.isfinite(realized_return):
                    current_returns[row.instrument] = realized_return
                updates_after_close.append(
                    (row.instrument, float(row.circ_mv), adjusted_close)
                )

        membership_date = date - pd.Timedelta(days=1)
        members = store.membership_as_of(membership_date.strftime("%Y%m%d"))
        weighted = [
            instrument for instrument in members
            if prior_cap.get(instrument, 0.0) > 0
            and prior_adjusted_close.get(instrument, 0.0) > 0
        ]
        coverage = len(weighted) / len(members) if members else 0.0
        if coverage < min_constituent_coverage:
            raise ValueError(
                f"constituent coverage {coverage:.6f} below threshold "
                f"{min_constituent_coverage:.6f} on {date:%Y-%m-%d}"
            )
        total_cap = sum(prior_cap[instrument] for instrument in weighted)
        if not math.isfinite(total_cap) or total_cap <= 0:
            raise ValueError(f"invalid prior float market cap on {date:%Y-%m-%d}")
        daily_return = sum(
            prior_cap[instrument] / total_cap * current_returns.get(instrument, 0.0)
            for instrument in weighted
        )
        if not math.isfinite(daily_return) or daily_return <= -1.0:
            raise ValueError(f"invalid benchmark return on {date:%Y-%m-%d}")
        open_level = previous_level
        close_level = open_level * (1.0 + daily_return)
        benchmark_rows.append({
            "trade_date": date.strftime("%Y%m%d"),
            "membership_date": membership_date.strftime("%Y%m%d"),
            "open": open_level,
            "close": close_level,
            "daily_return": daily_return,
        })
        coverage_rows.append({
            "trade_date": date.strftime("%Y%m%d"),
            "member_count": len(members),
            "weighted_member_count": len(weighted),
            "observed_return_count": sum(
                instrument in current_returns for instrument in weighted
            ),
            "zero_return_carry_count": sum(
                instrument not in current_returns for instrument in weighted
            ),
            "constituent_count_coverage": coverage,
            "prior_float_market_cap": total_cap,
            "daily_return": daily_return,
        })
        previous_level = close_level
        for instrument, cap, adjusted_close in updates_after_close:
            prior_cap[instrument] = cap
            prior_adjusted_close[instrument] = adjusted_close

    benchmark = pd.DataFrame(benchmark_rows)
    coverage = pd.DataFrame(coverage_rows)
    sources = {
        "schema_version": "pit_universe_benchmark_sources_v1",
        "canonical_data_root": str(market_root),
        "files": source_rows,
    }
    target.mkdir(parents=True, exist_ok=True)
    benchmark_path = target / "benchmark.csv"
    coverage_path = target / "daily_constituent_coverage.csv"
    sources_path = target / "source_files.json"
    _write_text(benchmark_path, benchmark.to_csv(index=False, lineterminator="\n"))
    _write_text(coverage_path, coverage.to_csv(index=False, lineterminator="\n"))
    _write_text(
        sources_path,
        json.dumps(sources, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
    )

    universe_manifest = json.loads(universe_manifest_path.read_text(encoding="utf-8"))
    inputs = {
        "universe_id": store.provenance.universe_id,
        "universe_manifest_path": str(universe_manifest_path),
        "universe_manifest_sha256": _sha256(universe_manifest_path),
        "membership_path": str(membership_path),
        "membership_sha256": _sha256(membership_path),
        "membership_manifest_sha256": universe_manifest["membership_sha256"],
        "calendar_path": str(calendar_path),
        "calendar_sha256": _sha256(calendar_path),
        "source_inventory_sha256": _sha256(sources_path),
    }
    if config_path is not None:
        resolved_config = Path(config_path).resolve()
        _regular_file(resolved_config, "benchmark config")
        inputs.update({
            "config_path": str(resolved_config),
            "config_sha256": _sha256(resolved_config),
        })
    identity = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": benchmark_id,
        "benchmark_kind": "synthetic_proxy_not_official_index",
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "holdout_start": holdout.strftime("%Y-%m-%d"),
        "weight_contract": (
            "strict_prior_calendar_day_pit_membership_and_strict_prior_circ_mv_"
            "daily_rebalanced_v1"
        ),
        "return_contract": (
            "first_day_open_to_close_then_adjusted_close_to_close;"
            "missing_quote_zero_until_next_observation_v1"
        ),
        "min_constituent_coverage": min_constituent_coverage,
        "producer_code_sha256": _sha256(Path(__file__)),
        "inputs": inputs,
    }
    manifest = {
        **identity,
        "benchmark_identity_sha256": _canonical_hash(identity),
        "observations": int(len(benchmark)),
        "coverage": {
            "minimum": float(coverage["constituent_count_coverage"].min()),
            "mean": float(coverage["constituent_count_coverage"].mean()),
            "member_count_min": int(coverage["member_count"].min()),
            "member_count_max": int(coverage["member_count"].max()),
        },
        "outputs": {
            "benchmark.csv": {"sha256": _sha256(benchmark_path), "row_count": len(benchmark)},
            "daily_constituent_coverage.csv": {
                "sha256": _sha256(coverage_path), "row_count": len(coverage)
            },
            "source_files.json": {
                "sha256": _sha256(sources_path), "row_count": len(source_rows)
            },
        },
    }
    manifest_path = target / "manifest.json"
    _write_text(
        manifest_path,
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
    )
    return {
        "benchmark_id": benchmark_id,
        "benchmark_identity_sha256": manifest["benchmark_identity_sha256"],
        "benchmark": str(benchmark_path),
        "manifest": str(manifest_path),
    }


def validate_pit_universe_benchmark(output_dir: str | Path) -> dict[str, Any]:
    """Validate frozen benchmark outputs without rereading mutable raw sources."""
    target = Path(output_dir).resolve()
    manifest_path = target / "manifest.json"
    _regular_file(manifest_path, "benchmark manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported PIT universe benchmark schema")
    identity = {
        key: value for key, value in manifest.items()
        if key not in {"benchmark_identity_sha256", "observations", "coverage", "outputs"}
    }
    if _canonical_hash(identity) != manifest.get("benchmark_identity_sha256"):
        raise ValueError("benchmark identity hash mismatch")
    for name, expected in manifest.get("outputs", {}).items():
        path = target / name
        _regular_file(path, f"benchmark output {name}")
        if _sha256(path) != expected.get("sha256"):
            raise ValueError(f"benchmark output hash mismatch: {name}")
    benchmark = pd.read_csv(target / "benchmark.csv")
    dates = _normalize_dates(benchmark["trade_date"])
    if len(benchmark) != int(manifest["observations"]):
        raise ValueError("benchmark observation count mismatch")
    if dates.min().strftime("%Y-%m-%d") != manifest["start_date"]:
        raise ValueError("benchmark start date mismatch")
    if dates.max().strftime("%Y-%m-%d") != manifest["end_date"]:
        raise ValueError("benchmark end date mismatch")
    returns = pd.to_numeric(benchmark["close"], errors="coerce").pct_change()
    returns.iloc[0] = float(benchmark.iloc[0]["close"] / benchmark.iloc[0]["open"] - 1.0)
    declared = pd.to_numeric(benchmark["daily_return"], errors="coerce")
    if not (returns - declared).abs().le(1e-12).all():
        raise ValueError("benchmark level and declared return disagree")
    if dates.max() >= pd.Timestamp(manifest["holdout_start"]):
        raise ValueError("benchmark consumes declared holdout")
    return {
        "benchmark_id": manifest["benchmark_id"],
        "benchmark_identity_sha256": manifest["benchmark_identity_sha256"],
        "manifest": str(manifest_path),
        "validation": "passed",
    }


def run_pit_universe_benchmark_config(
    config_path: str | Path, *, validate_only: bool = False
) -> dict[str, Any]:
    """Build/validate a benchmark and optional frozen-backtest analytics from YAML."""
    path = Path(config_path).resolve()
    _regular_file(path, "benchmark config")
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("unsupported PIT universe benchmark config schema")
    benchmark_config = dict(config.get("benchmark") or {})
    reuse_existing = bool(benchmark_config.pop("reuse_existing", False))
    if validate_only or reuse_existing:
        result = validate_pit_universe_benchmark(benchmark_config["output_dir"])
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        for key in ("benchmark_id", "start_date", "end_date", "holdout_start"):
            if str(benchmark_config[key]) != str(manifest[key]):
                raise ValueError(f"reused benchmark {key} does not match config")
        result["benchmark"] = str(
            Path(benchmark_config["output_dir"]).resolve() / "benchmark.csv"
        )
    else:
        result = write_pit_universe_benchmark(
            **benchmark_config,
            config_path=path,
        )
    analytics_config = config.get("portfolio_analytics")
    if analytics_config and not validate_only:
        analytics = write_portfolio_analytics(
            benchmark_id=benchmark_config["benchmark_id"],
            benchmark_csv=result["benchmark"],
            holdout_start=benchmark_config["holdout_start"],
            **analytics_config,
        )
        result["portfolio_analytics"] = analytics
    return result
