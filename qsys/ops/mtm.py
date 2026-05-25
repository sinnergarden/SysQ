"""Mark-to-market helpers for the daily alpha_v1 pipeline.

Extracted from scripts/run_alpha_v1_daily.py for Phase 1.5 boundary refactor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from qlib.data import D as qlib_D
from qsys.data.adapter import QlibAdapter
from qsys.common.io import write_json


class StaleDataError(Exception):
    """Raised when stale close-price check blocks execution."""

    def __init__(self, message: str, stale_check: dict) -> None:
        super().__init__(message)
        self.stale_check = stale_check


def prev_trading_day(trade_date: str) -> str | None:
    """Return the previous trading day for *trade_date*."""
    try:
        cal = qlib_D.calendar(start_time="2010-01-01", end_time=trade_date)
        if cal is None or len(cal) < 2:
            return None
        return pd.Timestamp(cal[-2]).strftime("%Y-%m-%d")
    except Exception:
        return None


def load_mtm_snapshot(snapshot_path: Path) -> dict | None:
    """Load MTM snapshot from explicit path."""
    if not snapshot_path.exists():
        return None
    try:
        return json.loads(snapshot_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_prod_mtm_snapshot(trade_date: str, project_root: Path) -> dict | None:
    """Load previous trading day's production MTM snapshot for stale check."""
    path = (
        project_root
        / "experiments"
        / "alpha_v1_daily"
        / trade_date
        / "mtm"
        / "mtm_snapshot.json"
    )
    return load_mtm_snapshot(path)


def check_stale_prices(
    trade_date: str,
    close_prices: dict[str, float],
    positions: pd.DataFrame,
    *,
    project_root: Path,
) -> dict:
    """Compare today's close prices with previous MTM snapshot.

    Uses the previous trading day's ``mtm_snapshot.json`` details for
    comparison.  If >85% of position close prices are unchanged (tolerance
    < 0.005) the check is hard-blocked via :class:`StaleDataError`.

    Returns a ``stale_check`` dict with check metadata.
    """
    prev_date = prev_trading_day(trade_date)
    result: dict = {
        "trade_date": trade_date,
        "prev_trade_date": prev_date,
        "checked_count": 0,
        "identical_count": 0,
        "identical_ratio": 0.0,
        "threshold": 0.85,
        "status": "skipped",
        "examples": [],
    }
    if prev_date is None:
        return result
    prev_snapshot = load_prod_mtm_snapshot(prev_date, project_root=project_root)
    if prev_snapshot is None:
        print(f"  ⚠ 无上一交易日 ({prev_date}) MTM 快照，跳过陈旧检查")
        return result
    prev_details: list = prev_snapshot.get("details", [])
    if not prev_details:
        return result
    prev_close: dict[str, float] = {}
    for entry in prev_details:
        if isinstance(entry, (list, tuple)) and len(entry) >= 5:
            inst = str(entry[0])
            close_val = float(entry[4])
            if close_val > 0:
                prev_close[inst] = close_val
    if not prev_close:
        return result
    checked = 0
    identical = 0
    tol = 0.005
    examples: list[str] = []
    for _, row in positions.iterrows():
        inst = str(row["instrument"])
        if inst in close_prices and inst in prev_close:
            checked += 1
            diff = abs(close_prices[inst] - prev_close[inst])
            if diff < tol:
                identical += 1
                if len(examples) < 3:
                    examples.append(
                        f"{inst}: {prev_close[inst]} → {close_prices[inst]} (no change)"
                    )
    stale_ratio = identical / checked if checked > 0 else 0.0
    result["checked_count"] = checked
    result["identical_count"] = identical
    result["identical_ratio"] = stale_ratio
    result["examples"] = examples
    if checked == 0:
        if prev_close and close_prices:
            result["status"] = "skipped_low_overlap"
            result["total_instruments"] = len(positions)
        else:
            result["status"] = "skipped"
        return result
    if stale_ratio > 0.85:
        result["status"] = "blocked"
        lines = [
            f"\n{'=' * 60}",
            f"⛔ CRITICAL: 收盘价数据疑似陈旧/前向填充！",
            f"{'=' * 60}",
            f"交易日: {trade_date}",
            f"上一交易日: {prev_date}",
            f"检查持仓: {checked}只",
            f"价格未变: {identical}只 ({stale_ratio:.0%})",
            f"阈值: > 85% 价格未变 → 判定数据陈旧",
        ]
        for ex in examples:
            lines.append(f"  {ex}")
        lines += [
            "",
            "说明: qlib 数据同步可能失败，当日最新行情未写入。",
            "      忽略此错误直接 MTM 会使用前一天的收盘价，",
            "      导致 PnL 结果完全错误（假 ¥0 日收益）。",
            "",
            "请检查数据同步: python scripts/ops/sync_csi800_daily.py --apply",
            "=" * 60,
        ]
        msg = "\n".join(lines)
        print(msg)
        raise StaleDataError(msg, result)
    else:
        result["status"] = "passed"
    return result


def save_stale_check(output_dir: Path, check_result: dict) -> None:
    """Persist stale-check result to JSON."""
    stale_path = output_dir / "mtm" / "stale_check.json"
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(stale_path, check_result)


def save_mtm_snapshot(output_dir: Path, snapshot: dict) -> None:
    """Persist MTM snapshot to JSON."""
    mtm_path = output_dir / "mtm" / "mtm_snapshot.json"
    mtm_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(mtm_path, snapshot)


def try_mark_to_market(
    trade_date: str,
    output_dir: Path,
    account_path: Path | None = None,
    positions_path: Path | None = None,
    close_prices_override: dict[str, float] | None = None,
    db_path: str | None = None,
    *,
    project_root: Path,
    shadow_account_id: str | None = None,
    get_stock_name_fn: callable = lambda x: x,
) -> dict | None:
    """Compute MTM snapshot for *trade_date* and persist to *output_dir*.

    Parameters
    ----------
    trade_date : str
        Date to snapshot (YYYY-MM-DD).
    output_dir : Path
        Directory where ``mtm/mtm_snapshot.json`` and related files are
        written.
    account_path, positions_path : Path or None
        Fallback file paths when the ledger is unavailable.
    close_prices_override : dict or None
        Pre-fetched close prices to skip the qlib call.
    db_path : str or None
        Path to the SQLite ledger.  When present and the file exists, the
        ledger is the preferred source for account / position data.
    project_root : Path
        Project root used to resolve the previous day's MTM snapshot.
    shadow_account_id : str or None
        Shadow account identifier used for ledger lookups.  Required when
        *db_path* is provided.
    get_stock_name_fn : callable
        Function ``str -> str`` that maps instrument codes to display
        names.  Defaults to identity.
    """
    # Load from ledger when available
    ledger_account: dict | None = None
    ledger_df = None
    if db_path and Path(db_path).exists():
        try:
            from qsys.ledger.service import LedgerService

            service = LedgerService(db_path)
            acct = service.get_account(shadow_account_id) if shadow_account_id else None
            if acct:
                ledger_account = dict(acct)
                ledger_account["cash"] = service.get_cash(
                    shadow_account_id
                ) if shadow_account_id else 0.0
                ledger_positions = (
                    service.get_positions(shadow_account_id) if shadow_account_id else []
                )
                ledger_rows = []
                for p in ledger_positions:
                    if int(p["quantity"]) <= 0:
                        continue
                    ledger_rows.append(
                        {
                            "instrument": p["symbol"],
                            "quantity": int(p["quantity"]),
                            "cost_price": float(p["avg_cost"]),
                            "last_price": float(p.get("last_price", 0)),
                            "market_value": float(p.get("market_value", 0)),
                        }
                    )
                if ledger_rows:
                    ledger_df = pd.DataFrame(ledger_rows)
                # else: keep ledger_account (with cash) even without positions
                #       — avoids cross-strategy fallback to wrong shadow/ dir
            service.close()
        except Exception:
            pass

    # ── Helper: find previous MTM snapshot (strategy-aware) ──────────────

    def _resolve_prev_snapshot(
        prev_date: str | None,
    ) -> dict | None:
        """Load previous day's MTM snapshot from strategy-specific path first,
        then fall back to the hardcoded production path."""
        if prev_date is None:
            return None
        # Derive prev path from current output_dir by replacing date
        prev_path = Path(
            str(output_dir).replace(trade_date, prev_date, 1)
        ) / "mtm" / "mtm_snapshot.json"
        if prev_path.exists():
            return load_mtm_snapshot(prev_path)
        return load_prod_mtm_snapshot(prev_date, project_root=project_root)

    if ledger_account is not None and ledger_df is not None:
        positions = ledger_df
        account = ledger_account
        use_ledger = True
    elif ledger_account is not None:
        # Account exists in ledger but no positions — return cash-only snapshot
        # instead of falling back to another strategy's shadow files.
        cash = float(ledger_account.get("cash", 0))
        if cash == 0.0 and db_path and Path(db_path).exists():
            try:
                from qsys.ledger.service import LedgerService
                cash = LedgerService(db_path).get_cash(shadow_account_id)
            except Exception:
                pass
        initial_capital = float(
            ledger_account.get("initial_capital") or
            ledger_account.get("initial_cash", 1_000_000)
        )
        total_value = cash
        cumulative_pnl = total_value - initial_capital
        cumulative_pnl_pct = round(cumulative_pnl / initial_capital * 100, 2) if initial_capital > 0 else 0.0
        # Ensure qlib is initialized for prev_trading_day to work
        try:
            QlibAdapter().init_qlib()
        except Exception:
            pass
        prev_date = prev_trading_day(trade_date)
        if prev_date is not None:
            prev_snap = _resolve_prev_snapshot(prev_date)
            if prev_snap is not None:
                daily_pnl = total_value - float(prev_snap["total_value"])
            else:
                daily_pnl = cumulative_pnl
        else:
            daily_pnl = cumulative_pnl
        snapshot = {
            "cash": cash, "market_value": 0.0,
            "total_value": total_value, "initial_capital": initial_capital,
            "cumulative_pnl": cumulative_pnl, "cumulative_pnl_pct": cumulative_pnl_pct,
            "daily_pnl": daily_pnl,
            "priced_count": 0, "total_positions": 0, "details": [],
        }
        save_mtm_snapshot(output_dir, snapshot)
        return snapshot
    else:
        if positions_path is None and ledger_df is None:
            positions_path = project_root / "shadow" / "positions.csv"
        if account_path is None and ledger_account is None:
            account_path = project_root / "shadow" / "account.json"
        if not positions_path or not positions_path.exists():
            return None
        if not account_path or not account_path.exists():
            return None
        try:
            positions = pd.read_csv(positions_path)
        except Exception:
            return None
        try:
            account = json.loads(account_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    if positions.empty:
        return None

    try:
        if close_prices_override is not None:
            close_prices = close_prices_override
        else:
            adapter = QlibAdapter()
            adapter.init_qlib()
            instruments = positions["instrument"].tolist()
            market = adapter.get_features(
                instruments,
                ["$close"],
                start_time=trade_date,
                end_time=trade_date,
            )
            if market is None or market.empty:
                return None
            if isinstance(market.index, pd.MultiIndex):
                market = market.copy()
                frame = market.reset_index()
                frame = frame[
                    frame.iloc[:, 1].astype(str).str.startswith(trade_date)
                ]
            else:
                frame = market.reset_index()
            if frame.empty:
                return None
            frame = frame.drop_duplicates(subset=["instrument"], keep="last")
            close_col = [c for c in frame.columns if c == "$close"]
            if not close_col:
                return None
            close_col = close_col[0]
            close_prices = {}
            for _, r in frame.iterrows():
                inst = str(r["instrument"])
                try:
                    val = float(r[close_col])
                    if not pd.isna(val) and val > 0:
                        close_prices[inst] = val
                except (ValueError, TypeError):
                    pass
        if not close_prices:
            return None
        total_market_value = 0.0
        total_cost = 0.0
        priced_count = 0
        details: list[tuple] = []
        for _, row in positions.iterrows():
            inst = str(row["instrument"])
            qty = int(float(row.get("quantity", 0)))
            if qty <= 0:
                continue
            cost = float(row.get("cost_price", 0))
            close = close_prices.get(inst)
            if close is None:
                continue
            market_val = qty * close
            total_market_value += market_val
            total_cost += qty * cost
            priced_count += 1
            details.append(
                (
                    inst,
                    get_stock_name_fn(inst),
                    qty,
                    cost,
                    close,
                    market_val - qty * cost,
                )
            )
        cash = float(account.get("cash", 0))
        if cash == 0.0 and db_path and Path(db_path).exists():
            try:
                from qsys.ledger.service import LedgerService

                cash = LedgerService(db_path).get_cash(shadow_account_id)
            except Exception:
                pass
        initial_capital = float(
            account.get("initial_capital") or
            account.get("initial_cash", 1_000_000)
        )
        total_value = cash + total_market_value
        cumulative_pnl = total_value - initial_capital
        cumulative_pnl_pct = (
            cumulative_pnl / initial_capital * 100 if initial_capital > 0 else 0.0
        )
        prev_date = prev_trading_day(trade_date)
        if prev_date is not None:
            prev_snap = _resolve_prev_snapshot(prev_date)
            if prev_snap is not None:
                daily_pnl = total_value - float(prev_snap["total_value"])
            else:
                daily_pnl = cumulative_pnl
        else:
            daily_pnl = cumulative_pnl
        if priced_count == 0:
            return None
        details.sort(key=lambda x: x[5], reverse=True)
        snapshot = {
            "cash": cash,
            "market_value": total_market_value,
            "total_value": total_value,
            "initial_capital": initial_capital,
            "cumulative_pnl": cumulative_pnl,
            "cumulative_pnl_pct": round(cumulative_pnl_pct, 2),
            "daily_pnl": daily_pnl,
            "priced_count": priced_count,
            "total_positions": len(positions),
            "details": details,
        }
        save_mtm_snapshot(output_dir, snapshot)
        return snapshot
    except Exception as e:
        if isinstance(e, SystemExit):
            raise
        print(f"  ⚠ mark-to-market failed: {e}")
        return None


def fetch_close_prices(
    trade_date: str, instruments: list[str]
) -> dict[str, float]:
    """Fetch close prices from qlib for given instruments on *trade_date*."""
    if not instruments:
        return {}
    try:
        adapter = QlibAdapter()
        adapter.init_qlib()
        market = adapter.get_features(
            instruments, ["$close"], start_time=trade_date, end_time=trade_date
        )
        if market is None or market.empty:
            return {}
        if isinstance(market.index, pd.MultiIndex):
            frame = market.reset_index()
            frame = frame[
                frame.iloc[:, 1].astype(str).str.startswith(trade_date)
            ]
        else:
            frame = market.reset_index()
        if frame.empty:
            return {}
        frame = frame.drop_duplicates(subset=["instrument"], keep="last")
        prices = {}
        for _, r in frame.iterrows():
            try:
                v = float(r["$close"])
                if not pd.isna(v) and v > 0:
                    prices[str(r["instrument"])] = v
            except (ValueError, TypeError):
                pass
        return prices
    except Exception:
        return {}
