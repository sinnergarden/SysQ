"""Shadow execution — execute plans, commit to ledger, manage artifacts.

Public APIs for plan execution, ledger commit, and artifact management.
Depends on ``market_snapshot``, ``plan_builder``, and ``qsys.ledger.*``.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.ops.commit_guard import cleanup_committing, committed_marker, committing_marker
from qsys.ops.market_snapshot import ShadowRebalanceError, fetch_market_snapshot
from qsys.ops.plan_builder import DEFAULT_INITIAL_CAPITAL, POSITION_COLUMNS, load_shadow_account
from qsys.trader.account import Account, Position
from qsys.trader.matcher import MatchEngine
from qsys.utils.json_io import write_json

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_TURNOVER_BUFFER = 0.0
DEFAULT_PRICE_MODE = "shadow_mark_price"
DEFAULT_REBALANCE_MODE = "daily"

LEDGER_COLUMNS = [
    "run_id", "trade_date", "instrument", "side", "quantity",
    "price", "amount", "fee", "status", "reason",
]


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ShadowRebalanceArtifacts:
    trade_date: str
    run_id: str
    status: str
    strategy_id: str
    strategy_version: str
    portfolio_method: str
    top_n: int
    buffer_hold: int
    buffer_buy: int
    single_stock_cap: float
    turnover_buffer: float
    price_mode: str
    rebalance_mode: str
    target_weights_path: str
    order_intents_path: str
    execution_summary_path: str
    account_after_path: str
    positions_after_path: str
    ledger_rows_path: str
    shadow_account_path: str
    shadow_positions_path: str
    shadow_ledger_path: str
    rebalance_audit_path: str
    order_count: int
    buy_count: int
    sell_count: int
    skipped_count: int
    filled_count: int
    rejected_count: int
    turnover: float
    cash_after: float
    total_value_after: float


# ── Helpers ──────────────────────────────────────────────────────────────────

def positions_frame(account: Account, current_prices: dict[str, float]) -> pd.DataFrame:
    """Build a positions DataFrame from an Account at given prices."""
    rows = []
    for instrument in sorted(account.positions):
        pos = account.positions[instrument]
        last_price = float(current_prices.get(instrument, 0.0) or 0.0)
        market_value = float(pos.total_amount * last_price)
        rows.append({
            "instrument": instrument,
            "quantity": int(pos.total_amount),
            "sellable_quantity": int(pos.sellable_amount),
            "cost_price": float(pos.avg_cost),
            "last_price": last_price,
            "market_value": market_value,
        })
    return pd.DataFrame(rows, columns=POSITION_COLUMNS)


def append_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    """Append ledger rows to a CSV file, creating header if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = LEDGER_COLUMNS
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ── Ledger write ─────────────────────────────────────────────────────────────

def write_execution_to_ledger(
    db_path: str,
    execution_date: str,
    strategy_id: str,
    orders: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
    results: list[dict[str, Any]],
    close_prices: dict[str, float],
    cash_after: float,
    market_value_after: float,
    total_value_after: float,
    positions_after: pd.DataFrame,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    run_id: str | None = None,
) -> None:
    """Write execution results to SQLite ledger.

    Parameters
    ----------
    run_id : str, optional
        If provided, use as-is.  If ``None``, derive
        ``f"{execution_date}.{strategy_id}.shadow"`` (backward-compatible default).
    """
    from qsys.ledger.service import LedgerService

    service = LedgerService(db_path)
    account_id = f"shadow_{strategy_id}"
    resolved_run_id = run_id or f"{execution_date}.{strategy_id}.shadow"
    trade_date = execution_date

    service.create_account(account_id, "shadow", initial_capital)

    existing_run = service.get_run(resolved_run_id)
    if existing_run:
        if existing_run["status"] == "completed":
            print(f"  ⏭  Run {resolved_run_id} already completed — skip ledger write")
            service.close()
            return
        print(f"  ⚠  Run {resolved_run_id} exists (status={existing_run['status']}) — force re-start")
        service.start_run(
            run_id=resolved_run_id, trade_date=trade_date,
            strategy_id=strategy_id, account_id=account_id,
            mode="postclose", force=True,
        )
    else:
        service.start_run(
            run_id=resolved_run_id, trade_date=trade_date,
            strategy_id=strategy_id, account_id=account_id,
            mode="postclose",
        )

    try:
        order_dicts = []
        for i, o in enumerate(orders):
            oid = f"ord_{resolved_run_id.replace('.', '_')}_{o['symbol']}_{o['side']}_{i}"
            o["_oid"] = oid
            order_dicts.append({
                "order_id": oid,
                "run_id": resolved_run_id,
                "account_id": account_id,
                "strategy_id": strategy_id,
                "trade_date": trade_date,
                "symbol": o["symbol"],
                "side": "BUY" if o["side"] == "buy" else "SELL",
                "order_type": o.get("order_type", "market"),
                "quantity": int(o.get("amount", 0)),
                "limit_price": o.get("price"),
                "status": "filled",
                "reason": "plan_execution",
            })
        service.record_orders(resolved_run_id, order_dicts, idempotent=True)

        fill_dicts = []
        for i, item in enumerate(results):
            if item.get("status") != "filled":
                continue
            o = item["order"]
            qty = int(item.get("filled_amount", o.get("amount", 0)) or 0)
            if qty <= 0:
                continue
            price = float(item.get("deal_price", o.get("price", 0.0)) or 0.0)
            fee = float(item.get("fee", 0.0) or 0.0)
            gross = qty * price
            side = "BUY" if o["side"] == "buy" else "SELL"
            net = gross + fee if side == "BUY" else gross - fee
            order_id = f"ord_{resolved_run_id.replace('.', '_')}_{o['symbol']}_{o['side']}_{i}"

            fill_dicts.append({
                "fill_id": f"fil_{resolved_run_id.replace('.', '_')}_{o['symbol']}_{o['side']}_{i}",
                "order_id": order_id,
                "run_id": resolved_run_id,
                "account_id": account_id,
                "strategy_id": strategy_id,
                "trade_date": trade_date,
                "symbol": o["symbol"],
                "side": side,
                "quantity": qty,
                "price": price,
                "gross_amount": gross,
                "commission": fee,
                "stamp_tax": 0.0,
                "slippage": 0.0,
                "net_amount": net,
                "source": "simulation",
            })

        if fill_dicts:
            # T+1 settlement: yesterday's positions are fully available today.
            service.roll_available_positions(account_id, execution_date)
            service.apply_fills(resolved_run_id, fill_dicts, t_plus_one=True, idempotent=True)

        prices_dict: dict[str, float] = {}
        for _, row in positions_after.iterrows():
            sym = str(row["instrument"])
            prices_dict[sym] = close_prices.get(sym, float(row.get("last_price", 0)))
        service.create_portfolio_snapshot(resolved_run_id, trade_date, prices=prices_dict)

        service.finish_run(resolved_run_id, "completed")
        print(f"  ✅ Ledger run {resolved_run_id} completed")
    except BaseException:
        try:
            service.finish_run(resolved_run_id, "failed")
        except Exception:
            pass
        service.close()
        raise

    service.close()


# ── Execute plan ─────────────────────────────────────────────────────────────

def _load_account_from_ledger(
    service: "LedgerService",
    account_id: str,
    initial_capital: float,
) -> tuple[Account, dict[str, Any], pd.DataFrame]:
    """Load Account + prior_account dict + positions DataFrame from LedgerService."""
    # Deferred import to avoid circular dependency at module level
    from qsys.ledger.service import LedgerService as _LS

    # Check if the account exists in ledger — if not, use initial capital as cash
    acct_row = service.get_account(account_id)
    if acct_row is None:
        cash = initial_capital
        positions = []
    else:
        cash = service.get_cash(account_id)
        positions = service.get_positions(account_id)

    account = Account(init_cash=initial_capital)
    account.cash = cash

    pos_rows: list[dict[str, Any]] = []
    for p in positions:
        sym = p["symbol"]
        qty = int(p["quantity"])
        if qty <= 0:
            continue
        # T+1 has settled by the next trading day — all positions are sellable.
        # The ledger's ``available_quantity`` reflects same-day T+1 lock only.
        cost = float(p["avg_cost"])
        account.positions[sym] = Position(
            symbol=sym,
            total_amount=qty,
            sellable_amount=qty,
            avg_cost=cost,
        )
        pos_rows.append({
            "instrument": sym, "quantity": qty,
            "sellable_quantity": qty, "cost_price": cost,
            "last_price": float(p.get("last_price", 0)),
            "market_value": float(p.get("market_value", qty * cost)),
        })

    positions_df = pd.DataFrame(pos_rows, columns=POSITION_COLUMNS) if pos_rows else pd.DataFrame(columns=POSITION_COLUMNS)

    total_mv = float(positions_df["market_value"].sum()) if not positions_df.empty else 0.0
    total_value = cash + total_mv
    prior_account = {
        "trade_date": None,
        "cash": cash, "available_cash": cash,
        "market_value": total_mv, "total_value": total_value,
        "last_run_id": None, "initial_capital": initial_capital,
    }
    return account, prior_account, positions_df


def execute_shadow_plan(
    *,
    base_dir: str | Path,
    plan_dir: str | Path,
    execution_date: str,
    output_dir: str | Path,
    debug_run: bool = False,
    db_path: str | None = None,
    run_id: str | None = None,
    account: Account | None = None,
    prior_account: dict | None = None,
    positions_df: pd.DataFrame | None = None,
) -> ShadowRebalanceArtifacts:
    """Execute a saved plan using execution_date's OPEN price.

    Parameters
    ----------
    run_id : str, optional
        If provided, use as-is.  If ``None``, read ``strategy_id`` from
        ``plan_meta.json`` and generate
        ``f"{strategy_id}_execute_{execution_date}"``.
    """
    plan_dir = Path(plan_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    shadow_dir = Path(base_dir) / "shadow"

    plan_meta_path = plan_dir / "plan_meta.json"
    if not plan_meta_path.exists():
        raise ShadowRebalanceError(f"Plan meta not found: {plan_meta_path}")
    plan_meta = json.loads(plan_meta_path.read_text())

    order_intents = pd.read_csv(plan_dir / "order_intents.csv")
    if order_intents.empty:
        raise ShadowRebalanceError("Plan has no order intents")

    strategy_id = plan_meta.get("strategy_id", "alpha_v1")
    resolved_run_id = run_id or f"{strategy_id}_execute_{execution_date}"

    if account is not None:
        # Use pre-loaded account (alpha_v1 ledger path)
        pass
    elif db_path and Path(db_path).exists():
        from qsys.ledger.service import LedgerService

        svc = LedgerService(db_path)
        account_id = f"shadow_{strategy_id}"
        try:
            initial_capital = svc.get_initial_cash(account_id) or DEFAULT_INITIAL_CAPITAL
        except Exception:
            initial_capital = DEFAULT_INITIAL_CAPITAL
        account, prior_account, positions_df = _load_account_from_ledger(
            svc, account_id, initial_capital,
        )
    else:
        account, prior_account, positions_df = load_shadow_account(shadow_dir)

    positions_before_count = len(account.positions)
    instruments = sorted(set(order_intents["instrument"].astype(str)) | set(account.positions.keys()))

    write_json(output_dir / "account_before.json", prior_account or {})
    pos_before_path = output_dir / "positions_before.csv"
    if positions_df is not None and not positions_df.empty:
        positions_df.to_csv(pos_before_path, index=False)
    else:
        pd.DataFrame(columns=POSITION_COLUMNS).to_csv(pos_before_path, index=False)

    open_prices, market_status = fetch_market_snapshot(
        execution_date, instruments, price_col="open",
    )

    orders = []
    for _, row in order_intents.iterrows():
        inst = str(row["instrument"])
        side = str(row["side"])
        qty = int(float(row.get("requested_qty", 0)))
        if qty <= 0:
            continue
        price = float(open_prices.get(inst, 0))
        if price <= 0:
            continue
        orders.append({
            "symbol": inst, "side": side, "amount": qty, "price": price,
            "order_type": "market",
            "strategy_id": plan_meta.get("strategy_id", strategy_id),
        })

    if not orders:
        raise ShadowRebalanceError("No executable orders after price validation")

    matcher = MatchEngine(slippage=0.0)
    results = matcher.match(orders, account, market_status, open_prices)
    account.settlement()

    buy_count = sum(1 for o in orders if o["side"] == "buy")
    sell_count = sum(1 for o in orders if o["side"] == "sell")
    filled_count = sum(1 for r in results if r["status"] == "filled")
    rejected_count = sum(1 for r in results if r["status"] == "rejected")
    turnover = float(sum(
        float(r.get("filled_amount", 0)) * float(r.get("deal_price", 0.0))
        for r in results if r["status"] == "filled"
    ))

    close_prices, _ = fetch_market_snapshot(execution_date, instruments, price_col="close")
    positions_after = positions_frame(account, close_prices)
    market_value_after = float(positions_after["market_value"].sum()) if not positions_after.empty else 0.0
    cash_after = float(account.cash)
    total_value_after = float(cash_after + market_value_after)

    positions_after.to_csv(output_dir / "positions_after.csv", index=False)
    account_after = {
        "trade_date": execution_date,
        "cash": cash_after,
        "available_cash": cash_after,
        "market_value": market_value_after,
        "total_value": total_value_after,
        "last_run_id": resolved_run_id,
        "initial_capital": float(prior_account.get("initial_capital", DEFAULT_INITIAL_CAPITAL)) if prior_account else DEFAULT_INITIAL_CAPITAL,
    }
    write_json(output_dir / "account_after.json", account_after)

    # Update shadow state for DailyRunner state persistence across days
    if not debug_run:
        write_json(shadow_dir / "account.json", account_after)
        positions_after.to_csv(shadow_dir / "positions.csv", index=False)

    ledger_rows = []
    for item in results:
        o = item["order"]
        qty = int(item.get("filled_amount", o.get("amount", 0)) or 0)
        price = float(item.get("deal_price", o.get("price", 0.0)) or 0.0)
        ledger_rows.append({
            "run_id": resolved_run_id,
            "trade_date": execution_date,
            "instrument": o["symbol"], "side": o["side"],
            "quantity": qty, "price": price, "amount": float(qty * price),
            "fee": float(item.get("fee", 0.0) or 0.0),
            "status": item["status"], "reason": item.get("reason", "plan_execution"),
        })
    pd.DataFrame(ledger_rows).to_csv(output_dir / "ledger_rows.csv", index=False)

    if db_path and not debug_run:
        ledger_payload = {
            "orders": orders,
            "results": results,
            "close_prices": close_prices,
            "initial_capital": float(prior_account.get("initial_capital", DEFAULT_INITIAL_CAPITAL)) if prior_account else DEFAULT_INITIAL_CAPITAL,
        }
        write_json(output_dir / "ledger_payload.json", ledger_payload)

    execution_summary = {
        "trade_date": execution_date,
        "run_id": resolved_run_id,
        "status": "success",
        "strategy_id": plan_meta.get("strategy_id", strategy_id),
        "strategy_version": plan_meta.get("strategy_version", ""),
        "portfolio_method": "plan_execution",
        "portfolio_params": {
            "price_mode": "open", "mtm_mode": "close",
            "plan_trade_date": plan_meta.get("trade_date", ""),
            "plan_reference_date": plan_meta.get("reference_date", ""),
        },
        "order_count": len(orders), "buy_count": buy_count, "sell_count": sell_count,
        "filled_count": filled_count, "rejected_count": rejected_count,
        "skipped_count": len(order_intents) - len(orders),
        "cash_before": float(prior_account.get("cash", 0)) if prior_account else 0,
        "cash_after": cash_after,
        "market_value_before": float(prior_account.get("market_value", 0)) if prior_account else 0,
        "market_value_after": market_value_after,
        "total_value_before": float(prior_account.get("total_value", 0)) if prior_account else 0,
        "total_value_after": total_value_after,
        "turnover": turnover,
        "positions_before_count": positions_before_count,
        "positions_after_count": len(positions_after),
        "no_real_orders": True,
        "notes": [
            "executed_at_open",
            f"execution_price=open_{execution_date}",
            f"mtm_price=close_{execution_date}",
            *(["debug_run"] if debug_run else []),
        ],
    }
    write_json(output_dir / "execution_summary.json", execution_summary)

    return ShadowRebalanceArtifacts(
        trade_date=execution_date,
        run_id=resolved_run_id,
        status="success",
        strategy_id=plan_meta.get("strategy_id", strategy_id),
        strategy_version=plan_meta.get("strategy_version", ""),
        portfolio_method="plan_execution",
        top_n=plan_meta.get("top_n", 20),
        buffer_hold=plan_meta.get("buffer_hold", 60),
        buffer_buy=plan_meta.get("buffer_buy", 40),
        single_stock_cap=plan_meta.get("single_stock_cap", 0.07),
        turnover_buffer=0.0, price_mode="open", rebalance_mode="plan_execution",
        target_weights_path=str(plan_dir / "target_weights.csv"),
        order_intents_path=str(plan_dir / "order_intents.csv"),
        execution_summary_path=str(output_dir / "execution_summary.json"),
        account_after_path=str(output_dir / "account_after.json"),
        positions_after_path=str(output_dir / "positions_after.csv"),
        ledger_rows_path=str(output_dir / "ledger_rows.csv"),
        shadow_account_path="",
        shadow_positions_path="",
        shadow_ledger_path="",
        rebalance_audit_path=str(plan_dir / "rebalance_audit.csv"),
        order_count=len(orders), buy_count=buy_count, sell_count=sell_count,
        skipped_count=max(len(order_intents) - len(orders), 0),
        filled_count=filled_count, rejected_count=rejected_count,
        turnover=turnover, cash_after=cash_after, total_value_after=total_value_after,
    )


# ── Commit execution artifacts ───────────────────────────────────────────────

def commit_execution_artifacts(
    *,
    run_root: str | Path,
    staging_dir: str | Path,
    db_path: str,
    trade_date: str,
    strategy_id: str,
    debug_run: bool = False,
) -> None:
    """Commit staged execution artifacts to run_root/execution/.

    1. Checks COMMITTING marker
    2. Writes SQLite ledger from ``ledger_payload.json`` (unless debug_run)
    3. Copies staging artifacts to ``execution/``
    4. Renames COMMITTING → COMMITTED
    """
    exec_dir = Path(run_root) / "execution"
    staging_dir = Path(staging_dir)
    exec_dir.mkdir(parents=True, exist_ok=True)

    committing_path = committing_marker(Path(run_root))
    if not committing_path.exists():
        print(f"  ❌ COMMITTING marker not found — commit order error")
        sys.exit(1)

    ledger_written = False
    try:
        if not debug_run:
            payload_path = staging_dir / "ledger_payload.json"
            if payload_path.exists():
                payload = json.loads(payload_path.read_text())
                positions_df = pd.DataFrame()
                pos_csv = staging_dir / "positions_after.csv"
                if pos_csv.exists():
                    positions_df = pd.read_csv(pos_csv)

                summary_path = staging_dir / "execution_summary.json"
                if summary_path.exists():
                    summary = json.loads(summary_path.read_text())
                    cash_after = summary.get("cash_after", 0.0)
                    market_value_after = summary.get("market_value_after", 0.0)
                    total_value_after = summary.get("total_value_after", 0.0)
                else:
                    cash_after = market_value_after = total_value_after = 0.0

                write_execution_to_ledger(
                    db_path=db_path,
                    execution_date=trade_date,
                    strategy_id=strategy_id,
                    orders=payload["orders"],
                    ledger_rows=[],
                    results=payload["results"],
                    close_prices=payload["close_prices"],
                    cash_after=cash_after,
                    market_value_after=market_value_after,
                    total_value_after=total_value_after,
                    positions_after=positions_df,
                    initial_capital=payload.get("initial_capital", 1_000_000.0),
                )
                ledger_written = True
            else:
                print(f"  ⚠ ledger_payload.json not found in {staging_dir}")

        for fname in [
            "account_after.json", "positions_after.csv", "execution_summary.json",
            "account_before.json", "positions_before.csv", "ledger_rows.csv",
            "ledger_payload.json",
        ]:
            src = staging_dir / fname
            if src.exists():
                shutil.copy2(str(src), str(exec_dir / fname))

        committing_path.rename(committed_marker(Path(run_root)))
        print(f"  ✅ Execution committed (COMMITTED): {exec_dir}")

    except BaseException:
        if ledger_written:
            print(f"  ❌ Ledger written but artifact commit failed — COMMITTING preserved")
        else:
            cleanup_committing(Path(run_root))
        raise


# ── Failed execution summary ─────────────────────────────────────────────────

def write_failed_execution_summary(
    *,
    output_dir: str | Path,
    trade_date: str,
    run_id: str,
    error: str,
    extra: dict[str, Any] | None = None,
    strategy_id: str = "alpha_v1",
    strategy_version: str = "",
) -> Path:
    """Write a failed execution summary JSON to *output_dir*."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return write_json(
        output_dir / "execution_summary.json",
        {
            "trade_date": trade_date,
            "run_id": run_id,
            "status": "failed",
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "turnover_buffer": DEFAULT_TURNOVER_BUFFER,
            "price_mode": DEFAULT_PRICE_MODE,
            "rebalance_mode": DEFAULT_REBALANCE_MODE,
            "order_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "skipped_count": 0,
            "filled_count": 0,
            "rejected_count": 0,
            "cash_before": None,
            "cash_after": None,
            "market_value_before": None,
            "market_value_after": None,
            "total_value_before": None,
            "total_value_after": None,
            "turnover": 0.0,
            "error": error,
            "notes": ["shadow_rebalance_failed"],
            **(extra or {}),
        },
    )
