
import unittest
import os
import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path
import shutil
import tempfile

from qsys.live.account import RealAccount
from qsys.live.simulation import ShadowSimulator
from qsys.live.scheduler import ModelScheduler
from qsys.live.reconciliation import (
    build_reconciliation_result,
    export_plan_bundle,
    sync_real_account_from_csv,
)

class TestLiveTrading(unittest.TestCase):
    
    def setUp(self):
        # Create a temp directory for DB and data
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_account.db")
        
    def tearDown(self):
        # Cleanup
        shutil.rmtree(self.test_dir)
        
    def test_real_account_init(self):
        """Test RealAccount database initialization"""
        account = RealAccount(db_path=self.db_path, account_name="test_acc")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("balance_history", tables)
        self.assertIn("position_history", tables)
        conn.close()
        
    def test_real_account_sync_and_get(self):
        """Test syncing broker state and retrieving it"""
        account = RealAccount(db_path=self.db_path, account_name="test_acc")
        
        # Sync state
        test_date = "2025-01-01"
        cash = 10000.0
        # Create DataFrame for positions
        positions = pd.DataFrame([
            {"symbol": "SH600519", "amount": 100, "price": 100.0, "cost_basis": 90.0}
        ])
        
        account.sync_broker_state(test_date, cash, positions)
        
        # Get latest date
        latest = account.get_latest_date()
        self.assertEqual(latest, test_date)
        
        # Get state
        state = account.get_state(test_date)
        if state is None:
            self.fail("State should not be None")
            return

        self.assertEqual(state["cash"], cash)
        # Check positions structure in state (it returns dict)
        self.assertEqual(state["positions"]["SH600519"]["amount"], 100)
        self.assertEqual(state["positions"]["SH600519"]["cost_basis"], 90.0)
        
        # Test get_latest_date with before_date (idempotency check logic)
        next_date = "2025-01-02"
        # Sync again for next day
        account.sync_broker_state(next_date, cash, positions)
        
        latest_before = account.get_latest_date(before_date=next_date)
        self.assertEqual(latest_before, test_date)

    def test_shadow_simulator_idempotency(self):
        """Test that running simulation multiple times doesn't duplicate trades"""
        # 1. Init Shadow Account
        sim = ShadowSimulator(
            account_name="shadow_test", 
            initial_cash=100000.0,
            db_path=self.db_path
        )
        
        init_date = "2025-01-01"
        sim.initialize_if_needed(init_date)
        
        # 2. Create a fake plan CSV
        plan_date = "2025-01-01" # Plan generated on 01-01 for 01-02 execution?
        # Usually: Plan generated on T-1 (e.g. 01-01) for T (01-02).
        # Simulation runs on T (01-02), reads plan from T-1 (01-01).
        
        plan_csv = os.path.join(self.test_dir, "plan_test.csv")
        df = pd.DataFrame([
            {"symbol": "SH600519", "side": "buy", "amount": 100, "price": 100.0, "weight": 0.1, "est_value": 10000}
        ])
        df.to_csv(plan_csv, index=False)
        
        # 3. Run Simulation for 2025-01-02
        sim_date = "2025-01-02"
        
        # First Run
        sim.simulate_execution(plan_csv, sim_date)
        
        state1 = sim.account.get_state(sim_date, "shadow_test")
        if state1 is None:
            self.fail("State1 should not be None")
            return
            
        cash1 = state1["cash"]
        pos1 = state1["positions"].get("SH600519", {}).get("total_amount", 0)
        
        self.assertEqual(pos1, 100)
        # Cash should decrease. Mock data price is around 20-30. 100 shares ~ 2000-3000 cost.
        # Plus fees. So cash should be around 97000-98000.
        self.assertLess(cash1, 99900.0) 
        
        # Second Run (Idempotency Check)
        sim.simulate_execution(plan_csv, sim_date)
        
        state2 = sim.account.get_state(sim_date, "shadow_test")
        if state2 is None:
            self.fail("State2 should not be None")
            return
            
        cash2 = state2["cash"]
        pos2 = state2["positions"].get("SH600519", {}).get("total_amount", 0)
        
        # Should match exactly
        self.assertEqual(pos2, pos1, "Positions changed after 2nd run (Idempotency Fail)")
        self.assertEqual(cash2, cash1, "Cash changed after 2nd run (Idempotency Fail)")

        trade_log = sim.account.get_trade_log(date=sim_date, account_name="shadow_test")
        self.assertEqual(len(trade_log), 1, "Trade log should be idempotent for repeated simulation")

    def test_plan_bundle_export(self):
        """Test standard pre-market plan export and post-close template generation."""
        output_dir = os.path.join(self.test_dir, "exports")
        plan_df = pd.DataFrame([
            {
                "symbol": "SH600519",
                "side": "buy",
                "amount": 100,
                "price": 123.45,
                "est_value": 12345.0,
                "weight": 0.1,
            }
        ])

        written = export_plan_bundle(
            plan_df,
            output_dir=output_dir,
            plan_date="2025-01-02",
            account_name="real",
            execution_date="2025-01-03",
        )

        self.assertTrue(os.path.exists(written["plan"]))
        self.assertTrue(os.path.exists(written["real_sync_template"]))

        exported_plan = pd.read_csv(written["plan"])
        exported_template = pd.read_csv(written["real_sync_template"])
        self.assertEqual(exported_plan.iloc[0]["account_name"], "real")
        self.assertEqual(exported_plan.iloc[0]["execution_date"], "2025-01-03")
        self.assertIn("cash", exported_template.columns)
        self.assertIn("total_assets", exported_template.columns)
        self.assertIn("filled_amount", exported_template.columns)
        self.assertIn("order_id", exported_template.columns)

    def test_reconciliation_sync_and_diff(self):
        """Test syncing a real CSV and reconciling it against shadow state."""
        shadow_account = RealAccount(db_path=self.db_path, account_name="shadow")
        real_account = RealAccount(db_path=self.db_path, account_name="real")

        shadow_positions = pd.DataFrame([
            {"symbol": "SH600519", "amount": 100, "price": 100.0, "cost_basis": 95.0}
        ])
        shadow_account.sync_broker_state(
            "2025-01-02",
            cash=90000.0,
            positions=shadow_positions,
            total_assets=100000.0,
            account_name="shadow",
        )

        csv_path = os.path.join(self.test_dir, "real_sync.csv")
        pd.DataFrame([
            {
                "symbol": "SH600519",
                "amount": 80,
                "price": 101.0,
                "cost_basis": 94.5,
                "cash": 92000.0,
                "total_assets": 100080.0,
                "side": "buy",
                "filled_amount": 80,
                "filled_price": 99.5,
                "fee": 12.0,
                "tax": 0.0,
                "total_cost": 7972.0,
                "order_id": "ord-1",
            }
        ]).to_csv(csv_path, index=False)

        normalized = sync_real_account_from_csv(
            real_account,
            account_name="real",
            sync_path=csv_path,
            date="2025-01-02",
            persist_trade_log=True,
        )
        self.assertEqual(len(normalized), 1)

        result = build_reconciliation_result(
            real_account,
            date="2025-01-02",
            real_account_name="real",
            shadow_account_name="shadow",
        )

        cash_diff = result.summary.loc[result.summary["metric"] == "cash", "diff"].iloc[0]
        self.assertEqual(cash_diff, 2000.0)
        self.assertEqual(len(result.real_trades), 1)
        self.assertEqual(result.real_trades.iloc[0]["order_id"], "ord-1")
        self.assertEqual(int(result.positions.iloc[0]["amount_diff"]), -20)

    def test_scheduler_find_latest(self):
        """Test finding latest model"""
        # Create fake model dirs
        models_dir = os.path.join(self.test_dir, "models")
        experiments_dir = os.path.join(self.test_dir, "experiments")
        os.makedirs(models_dir)
        os.makedirs(experiments_dir)
        
        # Create older model
        m1 = os.path.join(models_dir, "model_v1")
        os.makedirs(m1)
        # Use utime to set modification time (past)
        past_time = datetime.now().timestamp() - 1000
        os.utime(m1, (past_time, past_time))
        
        # Create newer model
        m2 = os.path.join(models_dir, "model_v2")
        os.makedirs(m2)
        # Use utime to set modification time (now)
        now_time = datetime.now().timestamp()
        os.utime(m2, (now_time, now_time))
        
        latest = ModelScheduler.find_latest_model(models_dir=models_dir, experiments_dir=experiments_dir)
        self.assertEqual(str(latest), m2)

if __name__ == '__main__':
    unittest.main()
