import pandas as pd
import numpy as np
from qsys.utils.logger import log

class TopKSwapStrategy:
    def __init__(self, max_slots=5, buy_rank_threshold=5, sell_rank_threshold=10, 
                 min_swap_gap=0.05, stop_loss_pct=0.08):
        """
        Strict Entry, Wide Exit, Significant Swap Strategy.
        """
        self.max_slots = max_slots
        self.buy_rank_threshold = buy_rank_threshold
        self.sell_rank_threshold = sell_rank_threshold
        self.min_swap_gap = min_swap_gap
        self.stop_loss_pct = stop_loss_pct

    def generate_target_weights(self, scores, market_status, account, current_prices):
        """
        scores: pd.Series (Index=Code, Value=Score)
        market_status: pd.DataFrame
        account: Account object (with positions)
        current_prices: dict {code: price}
        """
        # 0. Data Prep
        # Rank scores descending (Rank 1 is best)
        # Note: scores series might be incomplete (only tradable stocks?)
        # Better to rank ALL provided scores.
        ranked_scores = scores.sort_values(ascending=False)
        # Create a mapping code -> rank (1-based)
        rank_map = {code: i+1 for i, code in enumerate(ranked_scores.index)}
        
        current_positions = account.positions
        held_codes = list(current_positions.keys())
        
        target_weights = {} # code -> weight
        
        # We need to know 'Buyable' stocks for Fill/Swap
        # Filter suspended/limit_up from candidates
        buyable_candidates = []
        if market_status is not None:
            # Assume market_status index aligned or subset
            # Filter candidates
            for code in ranked_scores.index:
                if code in market_status.index:
                    status = market_status.loc[code]
                    if status['is_suspended'] or status['is_limit_up']:
                        continue
                buyable_candidates.append(code)
        else:
            buyable_candidates = ranked_scores.index.tolist()
            
        # ---------------------------------------------------------
        # Step 1: Fire Logic (Check held stocks)
        # ---------------------------------------------------------
        slots_taken = 0
        kept_codes = []
        
        for code in held_codes:
            pos = current_positions[code]
            should_sell = False
            sell_reason = ""
            
            # A. Hard Stop Loss
            curr_price = current_prices.get(code, 0)
            if curr_price > 0 and pos.avg_cost > 0:
                pnl = (curr_price - pos.avg_cost) / pos.avg_cost
                if pnl < -self.stop_loss_pct:
                    should_sell = True
                    sell_reason = f"Stop Loss ({pnl:.1%})"
            
            # B. Rank Stop (Wide Exit)
            if not should_sell:
                rank = rank_map.get(code, 9999)
                if rank > self.sell_rank_threshold:
                    should_sell = True
                    sell_reason = f"Rank Drop ({rank} > {self.sell_rank_threshold})"
            
            # Action
            if should_sell:
                # Target weight 0
                target_weights[code] = 0.0
                # log.info(f"Selling {code}: {sell_reason}")
            else:
                # Keep
                target_weights[code] = 1.0 / self.max_slots
                slots_taken += 1
                kept_codes.append(code)

        # ---------------------------------------------------------
        # Step 2: Fill Logic (Buy new if slots available)
        # ---------------------------------------------------------
        free_slots = self.max_slots - slots_taken
        
        if free_slots > 0:
            # Find best candidates
            for code in buyable_candidates:
                if free_slots <= 0:
                    break
                
                # Already held? (and kept)
                if code in kept_codes:
                    continue
                
                # Buy Criteria: Rank <= Buy Threshold
                rank = rank_map.get(code, 9999)
                if rank <= self.buy_rank_threshold:
                    target_weights[code] = 1.0 / self.max_slots
                    free_slots -= 1
                    # log.info(f"Buying {code}: Rank {rank}")
                else:
                    # Since candidates are sorted by rank, if we hit rank > threshold, 
                    # no more candidates will satisfy.
                    break
                    
        # ---------------------------------------------------------
        # Step 3: Upgrade Logic (Significant Swap)
        # ---------------------------------------------------------
        # Only if full (slots_taken == max_slots after fill? Or if we just filled?)
        # Logic says: "If Empty Slots == 0 (Full)"
        # So check if we are full NOW.
        
        # Recount slots in target_weights
        current_target_holders = [c for c, w in target_weights.items() if w > 0]
        if len(current_target_holders) == self.max_slots:
            # Find Weakest Held
            # Sort held by Score (ascending)
            # We need score map
            score_map = scores.to_dict()
            
            # Only consider those we decided to HOLD in target_weights
            held_with_scores = [(c, score_map.get(c, -999)) for c in current_target_holders]
            held_with_scores.sort(key=lambda x: x[1]) # Ascending score
            
            weakest_code, weakest_score = held_with_scores[0]
            
            # Find Strongest Candidate (Not Held)
            strongest_code = None
            strongest_score = -999
            
            for code in buyable_candidates:
                if code not in current_target_holders:
                    s = score_map.get(code, -999)
                    # Must be better than buy threshold? Usually yes if it's Rank 1.
                    # But let's just take the absolute best outsider.
                    strongest_code = code
                    strongest_score = s
                    break # First one is best
            
            if strongest_code:
                # Check Gap
                gap = strongest_score - weakest_score
                if gap > self.min_swap_gap:
                    # Execute Swap
                    # log.info(f"Swapping {weakest_code}({weakest_score:.2f}) -> {strongest_code}({strongest_score:.2f}), Gap={gap:.2f}")
                    target_weights[weakest_code] = 0.0
                    target_weights[strongest_code] = 1.0 / self.max_slots

        return target_weights
