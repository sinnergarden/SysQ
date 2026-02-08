from qsys.utils.logger import log

class MatchEngine:
    def __init__(self, commission=0.0003, stamp_duty=0.001, min_commission=5.0, slippage=0.001):
        self.commission = commission
        self.stamp_duty = stamp_duty
        self.min_commission = min_commission
        self.slippage = slippage # 0.1%

    def match(self, orders, account, market_status, current_prices):
        """
        Execute orders against market environment.
        Updates account in-place.
        Returns: list of executed trades/results
        """
        results = []
        
        for order in orders:
            sym = order['symbol']
            side = order['side']
            amount = order['amount']
            
            # 1. Get Market Info
            price_raw = current_prices.get(sym)
            if not price_raw:
                results.append({'order': order, 'status': 'rejected', 'reason': 'No Price'})
                continue
                
            status = market_status.loc[sym] if sym in market_status.index else None
            
            # 2. Hard Constraints Check
            
            # A. Suspension
            if status is not None and status.get('is_suspended', False):
                 results.append({'order': order, 'status': 'rejected', 'reason': 'Suspended'})
                 continue
                 
            # B. Limit Up/Down
            # Buy >= Limit Up? -> Reject
            # Sell <= Limit Down? -> Reject
            # Assuming price_raw is today's execution price (e.g. Open or Close)
            if status is not None:
                if side == 'buy' and status.get('is_limit_up', False):
                     results.append({'order': order, 'status': 'rejected', 'reason': 'Limit Up'})
                     continue
                if side == 'sell' and status.get('is_limit_down', False):
                     results.append({'order': order, 'status': 'rejected', 'reason': 'Limit Down'})
                     continue

            # C. Slippage
            # Buy: Pay more (Price * (1+S))
            # Sell: Get less (Price * (1-S))
            slip_factor = (1 + self.slippage) if side == 'buy' else (1 - self.slippage)
            deal_price = price_raw * slip_factor
            
            # D. Position/Cash Check
            if side == 'sell':
                pos = account.positions.get(sym)
                if not pos or pos.sellable_amount < amount:
                     # Partial fill or Reject?
                     # Let's try partial fill if we have some
                     available = pos.sellable_amount if pos else 0
                     if available == 0:
                         results.append({'order': order, 'status': 'rejected', 'reason': 'No Sellable Pos'})
                         continue
                     else:
                         # Partial
                         amount = available
                         # Update order info for record
                         order['filled_amount'] = amount
            
            # Calculate Fees
            turnover = amount * deal_price
            comm = max(turnover * self.commission, self.min_commission)
            tax = turnover * self.stamp_duty if side == 'sell' else 0.0
            total_fee = comm + tax
            
            if side == 'buy':
                cost = turnover + total_fee
                if account.cash < cost:
                    # Not enough cash.
                    # Try to shrink order?
                    # Max afford = (Cash - MinComm) / (Price * (1+CommRate)) approx
                    # For simplicity, just Reject or Simple scaling
                    # Let's Reject for safety in baseline, or simple scale down
                    max_can_buy = (account.cash - self.min_commission) / (deal_price * (1 + self.commission))
                    max_lots = int(max_can_buy / 100) * 100
                    if max_lots <= 0:
                        results.append({'order': order, 'status': 'rejected', 'reason': 'Insufficient Cash'})
                        continue
                    amount = max_lots
                    # Recalc fees
                    turnover = amount * deal_price
                    comm = max(turnover * self.commission, self.min_commission)
                    total_fee = comm # No tax on buy
            
            # Execute
            account.update_after_deal(sym, amount, deal_price, total_fee, side)
            results.append({
                'order': order,
                'status': 'filled',
                'filled_amount': amount,
                'deal_price': deal_price,
                'fee': total_fee
            })
            
        return results
