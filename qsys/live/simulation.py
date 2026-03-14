import pandas as pd
from qsys.live.account import RealAccount
from qsys.utils.logger import log


class ShadowSimulator:
    def __init__(self, account_name="shadow", initial_cash=1_000_000.0, db_path="data/real_account.db"):
        self.account_name = account_name
        self.initial_cash = float(initial_cash)
        self.account = RealAccount(db_path=db_path, account_name=account_name)
        self.fee_rate = 0.0003
        self.tax_rate = 0.001

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

    def simulate_execution(self, plan_csv: str, date: str):
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
        if plan_df.empty:
            self._sync_state(date, cash, positions)
            return

        required_cols = {"symbol", "side", "amount"}
        if not required_cols.issubset(set(plan_df.columns)):
            log.error(f"Plan missing columns: {sorted(list(required_cols - set(plan_df.columns)))}")
            self._sync_state(date, cash, positions)
            return

        for _, row in plan_df.iterrows():
            symbol = str(row["symbol"])
            side = str(row["side"]).lower()
            amount = int(abs(row.get("amount", 0)))
            if amount <= 0:
                continue

            trade_price = float(row["price"]) if "price" in plan_df.columns and pd.notna(row.get("price")) else 0.0
            if trade_price <= 0:
                trade_price = float(positions.get(symbol, {}).get("price", 0.0))
            if trade_price <= 0:
                continue

            trade_value = trade_price * amount
            fee = trade_value * self.fee_rate

            pos = positions.get(symbol, {"amount": 0, "price": trade_price, "cost_basis": trade_price})

            if side == "buy":
                total_cost = trade_value + fee
                if cash < total_cost:
                    continue
                new_amount = pos["amount"] + amount
                if new_amount > 0:
                    new_cost_basis = (
                        pos["cost_basis"] * pos["amount"] + trade_price * amount
                    ) / new_amount
                else:
                    new_cost_basis = trade_price
                cash -= total_cost
                positions[symbol] = {
                    "amount": new_amount,
                    "price": trade_price,
                    "cost_basis": float(new_cost_basis),
                }
            elif side == "sell":
                sell_amount = min(pos["amount"], amount)
                if sell_amount <= 0:
                    continue
                sell_value = trade_price * sell_amount
                tax = sell_value * self.tax_rate
                cash += sell_value - fee - tax
                remain = pos["amount"] - sell_amount
                if remain > 0:
                    positions[symbol] = {
                        "amount": remain,
                        "price": trade_price,
                        "cost_basis": float(pos["cost_basis"]),
                    }
                else:
                    positions.pop(symbol, None)

        self._sync_state(date, cash, positions)
        state = self.account.get_state(date, self.account_name)
        if state:
            log.info(
                f"Shadow Simulation for {date} completed. Cash: {state['cash']:,.2f}, Total: {state['total_assets']:,.2f}"
            )

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
