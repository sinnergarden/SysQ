from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from qsys.live.account import RealAccount
from qsys.utils.logger import log


REQUIRED_REAL_SYNC_COLUMNS = {
    "symbol",
    "amount",
    "price",
    "cost_basis",
    "cash",
    "total_assets",
}

STANDARD_PLAN_COLUMNS = [
    "symbol",
    "side",
    "amount",
    "price",
    "est_value",
    "weight",
    "score",
    "score_rank",
    "target_value",
    "current_value",
    "diff_value",
    "weight_method",
    "account_name",
    "signal_date",
    "plan_date",
    "execution_date",
    "status",
    "filled_amount",
    "filled_price",
    "fee",
    "tax",
    "total_cost",
    "order_id",
    "note",
]

OPTIONAL_REAL_SYNC_COLUMNS = {
    "side",
    "filled_amount",
    "filled_price",
    "fee",
    "tax",
    "total_cost",
    "order_id",
    "note",
}


@dataclass
class ReconciliationResult:
    summary: pd.DataFrame
    positions: pd.DataFrame
    real_trades: pd.DataFrame

    def is_empty(self) -> bool:
        return self.summary.empty and self.positions.empty and self.real_trades.empty


def normalize_real_sync_csv(csv_path: str | Path) -> pd.DataFrame:
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path)
    if df.empty:
        raise ValueError(f"Real sync file is empty: {csv_path}")

    missing = REQUIRED_REAL_SYNC_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            "Real sync file missing required columns: "
            f"{sorted(list(missing))}. Required columns: {sorted(list(REQUIRED_REAL_SYNC_COLUMNS))}"
        )

    normalized = df.copy()
    normalized["symbol"] = normalized["symbol"].astype(str)
    normalized["amount"] = normalized["amount"].fillna(0).astype(int)
    normalized["price"] = normalized["price"].astype(float)
    normalized["cost_basis"] = normalized["cost_basis"].fillna(normalized["price"]).astype(float)
    normalized["cash"] = normalized["cash"].astype(float)
    normalized["total_assets"] = normalized["total_assets"].astype(float)

    if "filled_amount" not in normalized.columns:
        normalized["filled_amount"] = normalized["amount"]
    normalized["filled_amount"] = normalized["filled_amount"].fillna(normalized["amount"]).astype(int)

    if "filled_price" not in normalized.columns:
        normalized["filled_price"] = normalized["price"]
    normalized["filled_price"] = normalized["filled_price"].fillna(normalized["price"]).astype(float)

    for col in ["fee", "tax", "total_cost"]:
        if col not in normalized.columns:
            normalized[col] = 0.0
        normalized[col] = normalized[col].fillna(0.0).astype(float)

    if "side" not in normalized.columns:
        normalized["side"] = "hold"
    normalized["side"] = normalized["side"].fillna("hold").astype(str).str.lower()

    if "order_id" not in normalized.columns:
        normalized["order_id"] = ""
    normalized["order_id"] = normalized["order_id"].fillna("").astype(str)

    if "note" not in normalized.columns:
        normalized["note"] = ""
    normalized["note"] = normalized["note"].fillna("").astype(str)

    return normalized


def sync_real_account_from_csv(
    real_account: RealAccount,
    account_name: str,
    sync_path: str | Path,
    date: str,
    *,
    persist_trade_log: bool = True,
) -> pd.DataFrame:
    normalized = normalize_real_sync_csv(sync_path)

    df_positions = normalized[["symbol", "amount", "price", "cost_basis"]].copy()
    latest_cash = float(normalized["cash"].dropna().iloc[0])
    latest_total_assets = float(normalized["total_assets"].dropna().iloc[0])

    real_account.sync_broker_state(
        date=date,
        cash=latest_cash,
        positions=df_positions,
        total_assets=latest_total_assets,
        account_name=account_name,
    )

    if persist_trade_log:
        trade_rows = normalized[normalized["side"].isin(["buy", "sell"])].copy()
        for _, row in trade_rows.iterrows():
            filled_amount = int(abs(row["filled_amount"]))
            if filled_amount <= 0:
                continue
            price = float(row["filled_price"])
            fee = float(row["fee"])
            tax = float(row["tax"])
            total_cost = float(row["total_cost"])
            if total_cost == 0.0:
                gross = price * filled_amount
                if row["side"] == "buy":
                    total_cost = gross + fee + tax
                else:
                    total_cost = gross - fee - tax
            real_account.record_trade(
                date=date,
                account_name=account_name,
                symbol=row["symbol"],
                side=row["side"],
                amount=filled_amount,
                price=price,
                fee=fee,
                tax=tax,
                total_cost=total_cost,
                order_id=row["order_id"],
            )

    log.info(f"Synced Real Account from {sync_path}. Positions: {len(df_positions)}")
    return normalized


def build_reconciliation_result(
    account: RealAccount,
    date: str,
    *,
    real_account_name: str = "real",
    shadow_account_name: str = "shadow",
) -> ReconciliationResult:
    real_state = account.get_state(date, real_account_name)
    shadow_state = account.get_state(date, shadow_account_name)

    if not real_state:
        raise ValueError(f"Missing real account state for {date}")
    if not shadow_state:
        raise ValueError(f"Missing shadow account state for {date}")

    summary_rows = [
        {
            "metric": "cash",
            "real": float(real_state["cash"]),
            "shadow": float(shadow_state["cash"]),
        },
        {
            "metric": "total_assets",
            "real": float(real_state["total_assets"]),
            "shadow": float(shadow_state["total_assets"]),
        },
        {
            "metric": "position_count",
            "real": float(len(real_state["positions"])),
            "shadow": float(len(shadow_state["positions"])),
        },
    ]
    summary = pd.DataFrame(summary_rows)
    summary["diff"] = summary["real"] - summary["shadow"]

    symbols = sorted(set(real_state["positions"].keys()) | set(shadow_state["positions"].keys()))
    position_rows = []
    for symbol in symbols:
        real_pos = real_state["positions"].get(symbol, {})
        shadow_pos = shadow_state["positions"].get(symbol, {})
        real_amount = int(real_pos.get("amount", real_pos.get("total_amount", 0)) or 0)
        shadow_amount = int(shadow_pos.get("amount", shadow_pos.get("total_amount", 0)) or 0)
        real_price = float(real_pos.get("price", 0.0) or 0.0)
        shadow_price = float(shadow_pos.get("price", 0.0) or 0.0)
        real_cost_basis = float(real_pos.get("cost_basis", 0.0) or 0.0)
        shadow_cost_basis = float(shadow_pos.get("cost_basis", 0.0) or 0.0)
        position_rows.append(
            {
                "symbol": symbol,
                "real_amount": real_amount,
                "shadow_amount": shadow_amount,
                "amount_diff": real_amount - shadow_amount,
                "real_price": real_price,
                "shadow_price": shadow_price,
                "real_cost_basis": real_cost_basis,
                "shadow_cost_basis": shadow_cost_basis,
                "cost_basis_diff": real_cost_basis - shadow_cost_basis,
                "real_market_value": real_amount * real_price,
                "shadow_market_value": shadow_amount * shadow_price,
            }
        )
    positions = pd.DataFrame(position_rows)
    if not positions.empty:
        positions["market_value_diff"] = positions["real_market_value"] - positions["shadow_market_value"]
        positions = positions.sort_values(["amount_diff", "market_value_diff", "symbol"], ascending=[False, False, True])

    real_trades = account.get_trade_log(date=date, account_name=real_account_name)
    return ReconciliationResult(summary=summary, positions=positions, real_trades=real_trades)


def write_reconciliation_outputs(
    result: ReconciliationResult,
    output_dir: str | Path,
    *,
    date: str,
    real_sync_snapshot: Optional[pd.DataFrame] = None,
) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written = {}
    summary_path = output_dir / f"reconcile_summary_{date}.csv"
    result.summary.to_csv(summary_path, index=False)
    written["summary"] = str(summary_path)

    positions_path = output_dir / f"reconcile_positions_{date}.csv"
    result.positions.to_csv(positions_path, index=False)
    written["positions"] = str(positions_path)

    trades_path = output_dir / f"reconcile_real_trades_{date}.csv"
    result.real_trades.to_csv(trades_path, index=False)
    written["real_trades"] = str(trades_path)

    if real_sync_snapshot is not None:
        snapshot_path = output_dir / f"real_sync_snapshot_{date}.csv"
        real_sync_snapshot.to_csv(snapshot_path, index=False)
        written["real_sync_snapshot"] = str(snapshot_path)

    return written


def export_plan_bundle(
    plan_df: pd.DataFrame,
    *,
    output_dir: str | Path,
    signal_date: str,
    plan_date: str,
    account_name: str,
    execution_date: Optional[str] = None,
) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    execution_date = execution_date or plan_date

    normalized = plan_df.copy()
    if normalized.empty:
        normalized = pd.DataFrame(columns=STANDARD_PLAN_COLUMNS)
    else:
        normalized["symbol"] = normalized["symbol"].astype(str)
        normalized["account_name"] = account_name
        normalized["signal_date"] = signal_date
        normalized["plan_date"] = plan_date
        normalized["execution_date"] = execution_date
        normalized["status"] = "planned"
        normalized["filled_amount"] = pd.NA
        normalized["filled_price"] = pd.NA
        normalized["fee"] = pd.NA
        normalized["tax"] = pd.NA
        normalized["total_cost"] = pd.NA
        normalized["order_id"] = ""
        normalized["note"] = ""
        for column in STANDARD_PLAN_COLUMNS:
            if column not in normalized.columns:
                normalized[column] = pd.NA
        normalized = normalized[STANDARD_PLAN_COLUMNS]

    execution_template = normalized.copy()
    execution_template["cash"] = pd.NA
    execution_template["total_assets"] = pd.NA
    execution_template["cost_basis"] = execution_template["price"]

    plan_path = output_dir / f"plan_{plan_date}_{account_name}.csv"
    normalized.to_csv(plan_path, index=False)

    template_path = output_dir / f"real_sync_template_{plan_date}_{account_name}.csv"
    execution_template.to_csv(template_path, index=False)

    return {
        "plan": str(plan_path),
        "real_sync_template": str(template_path),
    }


def reconciliation_to_markdown(result: ReconciliationResult, *, top_n_positions: int = 10) -> str:
    lines = ["## Daily Reconciliation"]
    if not result.summary.empty:
        lines.append("\n### Summary")
        for _, row in result.summary.iterrows():
            lines.append(
                f"- {row['metric']}: real={row['real']:,.2f}, shadow={row['shadow']:,.2f}, diff={row['diff']:,.2f}"
            )

    if not result.positions.empty:
        lines.append("\n### Position Gaps")
        preview = result.positions.head(top_n_positions)
        for _, row in preview.iterrows():
            lines.append(
                "- "
                f"{row['symbol']}: amount diff={int(row['amount_diff'])}, "
                f"market value diff={row['market_value_diff']:,.2f}, "
                f"cost basis diff={row['cost_basis_diff']:,.4f}"
            )
    else:
        lines.append("\n### Position Gaps")
        lines.append("- No position differences detected.")

    if not result.real_trades.empty:
        lines.append("\n### Real Trades Logged")
        lines.append(f"- Trade rows: {len(result.real_trades)}")

    return "\n".join(lines)
