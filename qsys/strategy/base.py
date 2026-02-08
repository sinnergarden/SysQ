from abc import ABC, abstractmethod

class IStrategy(ABC):
    @abstractmethod
    def generate_orders(self, signals, current_portfolio):
        # Convert signals (scores) to target portfolio weights/quantities
        pass
