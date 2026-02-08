from .base import IStrategy

class PortfolioOptimizer(IStrategy):
    def generate_orders(self, signals, current_portfolio):
        # TopK or Mean-Variance optimization
        pass
