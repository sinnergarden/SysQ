
import argparse
import sys
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from qsys.data.adapter import QlibAdapter
from qsys.live.manager import LiveManager
from qsys.live.simulation import ShadowSimulator
from qsys.live.account import RealAccount
from qsys.live.scheduler import ModelScheduler
from qsys.utils.logger import log

def update_data(force=True):
    log.info("Updating Qlib Data...")
    try:
        QlibAdapter().check_and_update(force=force)
    except Exception as e:
        log.error(f"Failed to update data: {e}")

def print_plan_summary(plan_df, account_name):
    if plan_df is None or plan_df.empty:
        log.info(f"No trades planned for {account_name}.")
        return

    log.info(f"=== Trading Plan for {account_name} ===")
    if 'amount' not in plan_df.columns:
        log.warning(f"Plan format invalid for {account_name}. Missing 'amount' column.")
        return

    trades = plan_df[plan_df['amount'] > 0]
    
    if trades.empty:
        log.info("No active trades required (Portfolio balanced).")
        return
        
    for _, row in trades.iterrows():
        action = row['side'].upper()
        amount = int(row['amount'])
        symbol = row['symbol']
        price = row['price']
        log.info(f"{action} {amount} shares of {symbol} @ {price:.2f}")
        
    log.info(f"Total Trades: {len(trades)}")

def sync_real_account_from_csv(real_account, account_name, sync_path, date):
    csv_path = Path(sync_path)
    if not csv_path.exists():
        log.error(f"Real sync file not found: {sync_path}")
        return False

    df = pd.read_csv(csv_path)
    required_cols = {"symbol", "amount", "price"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        log.error(f"Real sync file missing required columns: {sorted(list(missing_cols))}")
        return False

    if "cost_basis" not in df.columns:
        df["cost_basis"] = df["price"]
    df_positions = df[["symbol", "amount", "price", "cost_basis"]].copy()
    df_positions["amount"] = df_positions["amount"].fillna(0).astype(int)
    df_positions["price"] = df_positions["price"].astype(float)
    df_positions["cost_basis"] = df_positions["cost_basis"].fillna(df_positions["price"]).astype(float)

    latest_state = real_account.get_state(account_name=account_name)
    cash = latest_state["cash"] if latest_state else 0.0
    if "cash" in df.columns:
        cash_values = df["cash"].dropna()
        if not cash_values.empty:
            cash = float(cash_values.iloc[0])

    total_assets = None
    if "total_assets" in df.columns:
        total_assets_values = df["total_assets"].dropna()
        if not total_assets_values.empty:
            total_assets = float(total_assets_values.iloc[0])

    real_account.sync_broker_state(
        date=date,
        cash=cash,
        positions=df_positions,
        total_assets=total_assets,
        account_name=account_name
    )
    log.info(f"Synced Real Account from {sync_path}. Positions: {len(df_positions)}")
    return True

def main():
    parser = argparse.ArgumentParser(description="Run Daily Trading Workflow (Real + Shadow)")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="Trading Date (YYYY-MM-DD)")
    parser.add_argument("--model_path", type=str, help="Path to model directory")
    parser.add_argument("--real_sync", type=str, help="Path to CSV file with Real Account state (cash, positions)")
    parser.add_argument("--skip_update", action="store_true", help="Skip data update")
    parser.add_argument("--shadow_cash", type=float, default=1_000_000.0, help="Initial cash for Shadow Account")
    parser.add_argument("--retrain_days", type=int, default=7, help="Model max age in days before retraining")
    parser.add_argument("--top_k", type=int, default=30, help="Number of stocks to select in strategy")
    parser.add_argument("--min_trade", type=int, default=5000, help="Minimum trade amount in RMB")
    
    args = parser.parse_args()
    
    log.info(f"=== Starting Daily Trading Workflow for {args.date} ===")
    
    if not args.skip_update:
        update_data()
    
    QlibAdapter().init_qlib()
    
    model_path = args.model_path
    if not model_path:
        latest_model = ModelScheduler.find_latest_model()
        if latest_model:
            model_path = str(latest_model)
            log.info(f"Auto-detected latest model: {model_path}")
        else:
            log.error("No model path provided and none found in data/models or data/experiments.")
            return

    if not Path(model_path).exists():
        log.error(f"Model path does not exist: {model_path}")
        return

    # 1.1 Check Model Freshness & Retrain if needed
    model_path = ModelScheduler.check_and_retrain(model_path, args.date, retrain_freq_days=args.retrain_days)
    log.info(f"Using Model: {model_path}")

    shadow_account_name = "shadow"
    shadow_sim = ShadowSimulator(account_name=shadow_account_name, initial_cash=args.shadow_cash)
    
    if shadow_sim.initialize_if_needed(args.date):
        log.info("Shadow Account Initialized.")
    else:
        yesterday = (datetime.strptime(args.date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        plan_path = f"data/plan_{yesterday}_{shadow_account_name}.csv"
        
        if Path(plan_path).exists():
            log.info(f"Simulating execution for Shadow Account using {plan_path}...")
            shadow_sim.simulate_execution(plan_path, args.date)
        else:
            log.warning(f"No plan found for yesterday ({yesterday}) at {plan_path}. Skipping shadow simulation.")

    real_account_name = "real"
    real_account = RealAccount(account_name=real_account_name)
    
    if args.real_sync:
        sync_real_account_from_csv(real_account, real_account_name, args.real_sync, args.date)
    
    log.info("Generating Plan for Shadow Account...")
    manager_shadow = LiveManager(model_path=model_path, account_name=shadow_account_name, top_k=args.top_k, min_trade_amount=args.min_trade)
    plan_shadow = manager_shadow.run_daily_plan(args.date)
    print_plan_summary(plan_shadow, shadow_account_name)

    log.info("Generating Plan for Real Account...")
    if real_account.get_latest_date() or args.real_sync:
        manager_real = LiveManager(model_path=model_path, account_name=real_account_name, top_k=args.top_k, min_trade_amount=args.min_trade)
        plan_real = manager_real.run_daily_plan(args.date)
        print_plan_summary(plan_real, real_account_name)
    else:
        log.warning("Real Account has no state. Skipping plan generation. Please sync broker state first.")
    
    log.info("Daily Trading Workflow Completed.")

if __name__ == "__main__":
    main()
