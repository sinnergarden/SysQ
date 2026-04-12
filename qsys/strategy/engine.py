import pandas as pd
import numpy as np
from qsys.research.signal import to_signal_frame
from qsys.utils.logger import log

DEFAULT_TOP_K = 5


class StrategyEngine:
    def __init__(
        self,
        top_k=DEFAULT_TOP_K,
        method="equal_weight",
        risk_max_position=0.3,
        strategy_type="rank_topk",
        min_signal_threshold=0.0,
        min_selected_count=1,
        allow_empty_portfolio=True,
    ):
        """
        Strategy Engine: Decides target positions based on scores and rules.
        """
        self.top_k = top_k
        self.method = method # equal_weight, score_weighted
        self.risk_max_position = risk_max_position
        self.strategy_type = strategy_type
        self.min_signal_threshold = float(min_signal_threshold)
        self.min_selected_count = max(int(min_selected_count), 0)
        self.allow_empty_portfolio = bool(allow_empty_portfolio)

    def generate_target_weights(self, scores, market_status=None):
        """
        Input:
            scores: pd.Series (Index=Code, Value=Score)
            market_status: pd.DataFrame (Index=Code, Columns=[is_suspended, is_limit_up, ...])
        Output:
            target_weights: dict {code: weight}
        """
        signal_frame = to_signal_frame(scores)

        # 1. Soft Filter
        valid_signal_frame = self._apply_soft_filters(signal_frame, market_status)

        # 2. Strategy rule
        top_scores = self.select_target_scores(valid_signal_frame)
         
        if top_scores.empty:
            return {}
        
        # 3. Weighting
        weights = self._calculate_weights(top_scores)
        
        # 4. Risk Constraints
        weights = self._apply_risk_constraints(weights)
        
        return weights.to_dict()

    def _apply_soft_filters(self, signal_frame, market_status):
        """
        Filter out untradable stocks from the *Candidate List*.
        Note: If we already hold it, we might want to keep holding it (weight > 0) or sell it (weight 0).
        Here we assume we are generating *Ideal Target Portfolio*.
        If a stock is suspended, we ideally shouldn't be able to buy/sell it.
        But Strategy Engine defines *Target*. 
        If we hold it and it's suspended, our Target might be 0 (sell), but Match Engine will reject the sell order.
        Or our Target might be keep holding.
        
        Common practice: 
        - Don't buy Limit Up.
        - Don't buy Suspended.
        - Don't sell Limit Down (though we can try).
        """
        if market_status is None or market_status.empty:
            return signal_frame

        # Align index
        common_idx = signal_frame.index.intersection(market_status.index)
        if common_idx.empty:
            return signal_frame

        status = market_status.loc[common_idx]
        filtered_scores = signal_frame.loc[common_idx].copy()
        
        # Filter: Exclude if suspended or limit up (cannot buy)
        # Note: This logic assumes we are opening new positions.
        # If we already hold it, and want to hold, it's fine.
        # But for TopK selection, usually we only select *buyable* top stocks.
        
        # Mask for 'Buyable'
        # Not suspended AND Not Limit Up
        is_buyable = ~(status['is_suspended'] | status['is_limit_up'])
        
        # For stocks we assume we want to buy, we filter them out from candidates.
        filtered_scores = filtered_scores[is_buyable]
        
        return filtered_scores

    def select_target_scores(self, signal_frame: pd.DataFrame) -> pd.Series:
        valid_scores = self._apply_strategy_rule(signal_frame)
        if valid_scores.empty:
            return pd.Series(dtype=float)
        return valid_scores.sort_values(ascending=False).head(self.top_k)

    def _apply_strategy_rule(self, signal_frame: pd.DataFrame) -> pd.Series:
        scores = signal_frame["signal_value"].astype(float)
        if self.strategy_type == "rank_topk":
            return scores
        if self.strategy_type == "rank_topk_with_cash_gate":
            eligible = scores[scores > self.min_signal_threshold]
            if len(eligible) >= max(self.min_selected_count, 1):
                return eligible
            if self.allow_empty_portfolio:
                return pd.Series(dtype=float)
            fallback_count = min(self.top_k, max(self.min_selected_count, 1), len(scores))
            if fallback_count <= 0:
                return pd.Series(dtype=float)
            return scores.sort_values(ascending=False).head(fallback_count)
        if self.strategy_type == "rank_plus_binary_gate":
            if "binary" not in signal_frame.columns:
                raise ValueError("rank_plus_binary_gate requires explicit binary field in signal frame")
            binary_mask = pd.to_numeric(signal_frame["binary"], errors="coerce") == 1
            return scores[binary_mask]
        raise ValueError(f"Unknown strategy_type: {self.strategy_type}")

    def _calculate_weights(self, top_scores):
        if self.method == "equal_weight":
            count = len(top_scores)
            if count == 0:
                return pd.Series()
            return pd.Series(1.0 / count, index=top_scores.index)
            
        elif self.method == "score_weighted":
            # Normalize scores to sum to 1
            # Assuming positive scores or handle negative?
            # Softmax or simple sum normalization?
            # Simple sum for now, assuming scores > 0 or Shifted.
            # If scores can be negative (LGBM regression often is), we might need softmax.
            
            # Using Softmax for safety
            exps = np.exp(top_scores)
            return exps / exps.sum()
            
        else:
            raise ValueError(f"Unknown weighting method: {self.method}")

    def _apply_risk_constraints(self, weights):
        """
        Simple risk control: Cap max weight.
        """
        # Cap weight
        weights = weights.clip(upper=self.risk_max_position)
        
        # Re-normalize? 
        # If we clip, sum < 1. That implies holding cash. That's fine.
        # Or we can redistribute. For simplicity, just clip (hold cash remainder).
        return weights
