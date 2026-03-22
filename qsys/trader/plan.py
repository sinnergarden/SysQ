import pandas as pd
from qsys.utils.logger import log


class PlanGenerator:
    def __init__(self, cash_buffer=0.02, min_trade_amount=5000):
        self.cash_buffer = cash_buffer
        self.min_trade_amount = min_trade_amount

    def generate_plan(
        self,
        target_weights,
        current_positions,
        total_assets,
        current_prices,
        *,
        score_lookup=None,
        score_rank_lookup=None,
        weight_method="equal_weight",
    ):
        """
        Generate trading plan from target weights and current positions.
        """
        score_lookup = score_lookup or {}
        score_rank_lookup = score_rank_lookup or {}
        plan = []
        all_symbols = set(target_weights.keys()) | set(current_positions.keys())

        for sym in all_symbols:
            price = current_prices.get(sym, 0)
            if price <= 0:
                log.warning(f"Skipping plan for {sym}: No price")
                continue

            target_weight = float(target_weights.get(sym, 0.0))
            target_value = total_assets * target_weight

            pos = current_positions.get(sym)
            current_amount = pos.get("total_amount", pos.get("amount", 0)) if pos else 0
            current_value = current_amount * price
            diff_value = target_value - current_value
            side = "buy" if diff_value > 0 else "sell"
            abs_diff_value = abs(diff_value)

            if abs_diff_value < self.min_trade_amount:
                continue

            diff_amount_raw = diff_value / price
            amount_lots = int(diff_amount_raw / 100) * 100
            if amount_lots == 0:
                continue

            plan.append(
                {
                    "symbol": sym,
                    "side": side,
                    "price": float(price),
                    "amount": abs(amount_lots),
                    "est_value": abs(amount_lots) * float(price),
                    "weight": target_weight,
                    "score": score_lookup.get(sym),
                    "score_rank": score_rank_lookup.get(sym),
                    "target_value": float(target_value),
                    "current_value": float(current_value),
                    "diff_value": float(diff_value),
                    "weight_method": weight_method,
                }
            )

        df_plan = pd.DataFrame(plan)
        if df_plan.empty:
            return df_plan

        df_sell = df_plan[df_plan["side"] == "sell"].sort_values("est_value", ascending=False)
        df_buy = df_plan[df_plan["side"] == "buy"].sort_values(["score_rank", "est_value"], ascending=[True, False])
        return pd.concat([df_sell, df_buy], ignore_index=True)

    def to_markdown(self, df_plan):
        if df_plan.empty:
            return "No trades planned."

        display_cols = [
            c
            for c in [
                "symbol",
                "side",
                "score",
                "score_rank",
                "weight",
                "amount",
                "price",
                "est_value",
                "target_value",
                "current_value",
                "diff_value",
                "weight_method",
            ]
            if c in df_plan.columns
        ]
        preview = df_plan[display_cols] if display_cols else df_plan

        try:
            return preview.to_markdown(index=False, floatfmt=".4f")
        except ImportError:
            return preview.to_string(index=False)
