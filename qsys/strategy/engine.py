import pandas as pd
import numpy as np
from qsys.utils.logger import log

DEFAULT_TOP_K = 5


class StrategyEngine:
    def __init__(self, top_k=DEFAULT_TOP_K, method="equal_weight", risk_max_position=0.3):
        """
        Strategy Engine: Decides target positions based on scores and rules.
        """
        self.top_k = top_k
        self.method = method # equal_weight, score_weighted
        self.risk_max_position = risk_max_position

    def generate_target_weights(self, scores, market_status=None):
        """
        Input:
            scores: pd.Series (Index=Code, Value=Score)
            market_status: pd.DataFrame (Index=Code, Columns=[is_suspended, is_limit_up, ...])
        Output:
            target_weights: dict {code: weight}
        """
        # Ensure scores is Series
        if isinstance(scores, pd.DataFrame):
            # If multi-column, assume first column is score
            # Or if it has 'score' column
            if 'score' in scores.columns:
                scores = scores['score']
            else:
                scores = scores.iloc[:, 0]
                
        # 1. Soft Filter
        valid_scores = self._apply_soft_filters(scores, market_status)
        
        # 2. Top K
        if valid_scores.empty:
            return {}
            
        # Sort descending
        top_scores = valid_scores.sort_values(ascending=False).head(self.top_k)
        
        # 3. Weighting
        weights = self._calculate_weights(top_scores)
        
        # 4. Risk Constraints
        weights = self._apply_risk_constraints(weights)
        
        return weights.to_dict()

    def _apply_soft_filters(self, scores, market_status):
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
            return scores
            
        # Align index
        common_idx = scores.index.intersection(market_status.index)
        if common_idx.empty:
            return scores
            
        status = market_status.loc[common_idx]
        filtered_scores = scores.loc[common_idx].copy()
        
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
