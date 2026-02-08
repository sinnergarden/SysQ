import pandas as pd
import sys
from pathlib import Path
# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.utils.logger import log
from qsys.trader.broker import BrokerAdapter
from qsys.trader.notifier import WeChatNotifier
import datetime

def main():
    log.info("Starting Daily Reconciliation...")
    
    # Config
    order_file = cfg.get_path("root") / "broker" / "orders.csv" # Export from Broker
    plan_file = cfg.get_path("root") / "plans" / f"plan_{datetime.date.today()}.csv" # Today's plan
    webhook_url = cfg.get("wechat_webhook")
    
    if not order_file.exists():
        log.error("Order file not found. Please export orders first.")
        return
        
    # 1. Load Data
    broker = BrokerAdapter()
    real_orders = broker.parse_orders(order_file)
    
    # 2. Stats
    total_buy = 0
    total_sell = 0
    total_fee = 0
    
    for o in real_orders:
        val = o['price'] * o['amount']
        if o['side'] == 'buy':
            total_buy += val
        elif o['side'] == 'sell':
            total_sell += val
        total_fee += o['fee']
        
    net_flow = total_sell - total_buy
    
    # 3. Report
    summary = f"""
## 📊 Daily Reconciliation ({datetime.date.today()})

- **Trade Count**: {len(real_orders)}
- **Total Buy**: {total_buy:,.2f}
- **Total Sell**: {total_sell:,.2f}
- **Net Flow**: {net_flow:,.2f}
- **Total Fee**: {total_fee:,.2f}
    """
    
    print(summary)
    
    if webhook_url:
        WeChatNotifier(webhook_url).send_message(summary)
        
    # TODO: Save to DB (Shadow Ledger)
    
if __name__ == "__main__":
    main()
