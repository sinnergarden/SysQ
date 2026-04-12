import pandas as pd
from qsys.live.account import RealAccount
from qsys.utils.logger import log


EXECUTION_AUDIT_COLUMNS = [
    "date",
    "account_name",
    "symbol",
    "side",
    "requested_amount",
    "filled_amount",
    "status",
    "reject_reason",
    "signal_basis_price",
    "plan_price",
    "fill_price_rule",
    "simulated_fill_price",
    "fee",
    "tax",
    "trade_value",
    "volume",
    "volume_participation_cap",
    "limit_state",
    "one_word_limit",
]


class ShadowSimulator:
    def __init__(
        self,
        account_name="shadow",
        initial_cash=1_000_000.0,
        db_path="data/meta/real_account.db",
        fee_rate=0.0003,
        tax_rate=0.001,
        slippage=0.0,
    ):
        self.account_name = account_name
        self.initial_cash = float(initial_cash)
        self.account = RealAccount(db_path=db_path, account_name=account_name)
        self.fee_rate = float(fee_rate)
        self.tax_rate = float(tax_rate)
        self.slippage = float(slippage)

    def initialize_if_needed(self, date: str) -> bool:
        latest = self.account.get_latest_date(self.account_name)
        if latest:
            return False
        empty_positions = pd.DataFrame(columns=["symbol", "amount", "price", "cost_basis"])
        self.account.sync_broker_state(
            date=date,
            cash=self.initial_cash,
            positions=empty_positions,
            total_assets=self.initial_cash,
            account_name=self.account_name,
        )
        return True

    def simulate_execution(self, plan_csv: str, date: str, volume_participation_cap: float | None = None):
        prev_date = self.account.get_latest_date(self.account_name, before_date=date)
        if not prev_date:
            log.error("No previous shadow state found. Simulation skipped.")
            return

        prev_state = self.account.get_state(prev_date, self.account_name)
        if not prev_state:
            log.error("Failed to load previous shadow state. Simulation skipped.")
            return

        cash = float(prev_state["cash"])
        positions = {}
        for sym, pos in prev_state["positions"].items():
            positions[sym] = {
                "amount": int(pos.get("total_amount", pos.get("amount", 0))),
                "price": float(pos.get("price", 0.0)),
                "cost_basis": float(pos.get("cost_basis", 0.0)),
            }

        plan_df = pd.read_csv(plan_csv)
        audit_rows = []
        self.account.clear_trade_log(date=date, account_name=self.account_name)
        if plan_df.empty:
            self._sync_state(date, cash, positions)
            return pd.DataFrame(columns=EXECUTION_AUDIT_COLUMNS)

        required_cols = {"symbol", "side", "amount"}
        if not required_cols.issubset(set(plan_df.columns)):
            log.error(f"Plan missing columns: {sorted(list(required_cols - set(plan_df.columns)))}")
            self._sync_state(date, cash, positions)
            return pd.DataFrame(columns=EXECUTION_AUDIT_COLUMNS)

        for _, row in plan_df.iterrows():
            symbol = str(row["symbol"])
            side = str(row["side"]).lower()
            amount = int(abs(row.get("amount", 0)))
            plan_price = float(row["price"]) if "price" in plan_df.columns and pd.notna(row.get("price")) else 0.0
            signal_basis_price = float(row.get("signal_basis_price", plan_price or 0.0) or 0.0)
            fill_price_rule = str(row.get("fill_price_rule", "plan_price_plus_slippage"))
            limit_state = str(row.get("limit_state", "unknown") or "unknown")
            one_word_limit = bool(row.get("one_word_limit", False))
            volume = float(row.get("volume", 0.0) or 0.0)
            status = "filled"
            reject_reason = ""
            filled_amount = amount
            simulated_fill_price = plan_price
            trade_price = plan_price if plan_price > 0 else float(positions.get(symbol, {}).get("price", 0.0))

            if amount <= 0:
                status = "rejected"
                reject_reason = "non_positive_amount"
                filled_amount = 0
            elif trade_price <= 0:
                status = "rejected"
                reject_reason = "missing_price"
                filled_amount = 0
            elif limit_state in {"up", "down"} or one_word_limit:
                status = "rejected"
                reject_reason = "limit_state_blocked"
                filled_amount = 0
            elif volume_participation_cap and volume > 0:
                filled_amount = min(filled_amount, int(volume * float(volume_participation_cap)))
                if filled_amount <= 0:
                    status = "rejected"
                    reject_reason = "volume_participation_cap"
            
            if status == "filled" and self.slippage:
                if side == "buy":
                    trade_price *= 1 + self.slippage
                elif side == "sell":
                    trade_price *= max(0.0, 1 - self.slippage)
            simulated_fill_price = trade_price if status == "filled" else None
            trade_value = (trade_price * filled_amount) if status == "filled" else 0.0
            fee = trade_value * self.fee_rate
            pos = positions.get(symbol, {"amount": 0, "price": trade_price, "cost_basis": trade_price})
            tax = 0.0

            if status == "filled" and side == "buy":
                total_cost = trade_value + fee
                if cash < total_cost:
                    status = "rejected"
                    reject_reason = "insufficient_cash"
                    filled_amount = 0
                    trade_value = 0.0
                    fee = 0.0
                else:
                    new_amount = pos["amount"] + filled_amount
                    new_cost_basis = ((pos["cost_basis"] * pos["amount"] + trade_price * filled_amount) / new_amount) if new_amount > 0 else trade_price
                    cash -= total_cost
                    positions[symbol] = {"amount": new_amount, "price": trade_price, "cost_basis": float(new_cost_basis)}
                    self.account.record_trade(date=date, account_name=self.account_name, symbol=symbol, side="buy", amount=filled_amount, price=trade_price, fee=fee, tax=0.0, total_cost=total_cost)
            elif status == "filled" and side == "sell":
                sell_amount = min(pos["amount"], filled_amount)
                if sell_amount <= 0:
                    status = "rejected"
                    reject_reason = "insufficient_position"
                    filled_amount = 0
                    trade_value = 0.0
                    fee = 0.0
                else:
                    trade_value = trade_price * sell_amount
                    fee = trade_value * self.fee_rate
                    tax = trade_value * self.tax_rate
                    cash += trade_value - fee - tax
                    remain = pos["amount"] - sell_amount
                    if remain > 0:
                        positions[symbol] = {"amount": remain, "price": trade_price, "cost_basis": float(pos["cost_basis"])}
                    else:
                        positions.pop(symbol, None)
                    filled_amount = sell_amount
                    self.account.record_trade(date=date, account_name=self.account_name, symbol=symbol, side="sell", amount=sell_amount, price=trade_price, fee=fee, tax=tax, total_cost=trade_value - fee - tax)

            audit_rows.append({
                "date": date,
                "account_name": self.account_name,
                "symbol": symbol,
                "side": side,
                "requested_amount": amount,
                "filled_amount": filled_amount,
                "status": status,
                "reject_reason": reject_reason,
                "signal_basis_price": signal_basis_price or None,
                "plan_price": plan_price or None,
                "fill_price_rule": fill_price_rule,
                "simulated_fill_price": simulated_fill_price,
                "fee": fee,
                "tax": tax,
                "trade_value": trade_value,
                "volume": volume or None,
                "volume_participation_cap": volume_participation_cap,
                "limit_state": limit_state,
                "one_word_limit": one_word_limit,
            })

        self._sync_state(date, cash, positions)
        state = self.account.get_state(date, self.account_name)
        if state:
            log.info(
                f"Shadow Simulation for {date} completed. Cash: {state['cash']:,.2f}, Total: {state['total_assets']:,.2f}"
            )
        return pd.DataFrame(audit_rows, columns=EXECUTION_AUDIT_COLUMNS)

    def _sync_state(self, date: str, cash: float, positions: dict):
        rows = []
        for sym, p in positions.items():
            rows.append(
                {
                    "symbol": sym,
                    "amount": int(p["amount"]),
                    "price": float(p.get("price", p.get("cost_basis", 0.0))),
                    "cost_basis": float(p.get("cost_basis", 0.0)),
                }
            )
        df_positions = pd.DataFrame(rows, columns=["symbol", "amount", "price", "cost_basis"])
        total_assets = float(cash)
        if not df_positions.empty:
            total_assets += float((df_positions["amount"] * df_positions["price"]).sum())
        self.account.sync_broker_state(
            date=date,
            cash=float(cash),
            positions=df_positions,
            total_assets=float(total_assets),
            account_name=self.account_name,
        )
