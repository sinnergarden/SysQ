
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

from qsys.utils.logger import log

class RealAccount:
    """
    Persistent Account State backed by SQLite.
    Stores the 'Reality' (what we actually own at the broker).
    Supports multiple accounts via 'account_name'.
    """
    def __init__(self, db_path="data/real_account.db", account_name="default"):
        self.db_path = str(db_path)
        self.account_name = account_name
        self._init_db()

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. Balance History
        # Records daily snapshot of Net Asset Value
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS balance_history (
            date TEXT,
            account_name TEXT,
            cash REAL,
            total_assets REAL,
            frozen_cash REAL DEFAULT 0,
            PRIMARY KEY (date, account_name)
        )
        ''')
        
        # 2. Position Snapshots
        # Records what we held at the end of each day
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS position_history (
            date TEXT,
            account_name TEXT,
            symbol TEXT,
            amount INTEGER,
            price REAL,  -- Market price at snapshot
            cost_basis REAL, -- Avg cost
            PRIMARY KEY (date, account_name, symbol)
        )
        ''')
        
        # 3. Trade Log (Optional, for reconciliation)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            account_name TEXT,
            symbol TEXT,
            side TEXT,
            amount INTEGER,
            price REAL,
            fee REAL,
            tax REAL DEFAULT 0.0,
            total_cost REAL,
            order_id TEXT
        )
        ''')
        
        conn.commit()
        conn.close()

    def sync_broker_state(self, date: str, cash: float, positions: pd.DataFrame,
                         total_assets: Optional[float] = None, account_name: Optional[str] = None):
        """
        Sync state from Broker (The Source of Truth).
        """
        account_name = account_name or self.account_name
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. Calculate Assets if not provided
        pos_value = 0.0
        if not positions.empty:
            if 'price' not in positions.columns:
                 raise ValueError("Positions DataFrame must have 'price' column")
            pos_value = (positions['amount'] * positions['price']).sum()
            
        if total_assets is None:
            total_assets = cash + pos_value
            
        # 2. Update Balance
        cursor.execute('''
        INSERT OR REPLACE INTO balance_history (date, account_name, cash, total_assets)
        VALUES (?, ?, ?, ?)
        ''', (date, account_name, cash, total_assets))
        
        # 3. Update Positions (Delete old for this date+account, insert new)
        cursor.execute('DELETE FROM position_history WHERE date = ? AND account_name = ?', (date, account_name))
        
        if not positions.empty:
            for _, row in positions.iterrows():
                cost = row.get('cost_basis', 0.0)
                cursor.execute('''
                INSERT INTO position_history (date, account_name, symbol, amount, price, cost_basis)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (date, account_name, row['symbol'], row['amount'], row['price'], cost))
                
        conn.commit()
        conn.close()
        log.info(f"Synced broker state for {date} (Account: {account_name}). Total Assets: {total_assets:,.2f}")

    def get_state(self, date: Optional[str] = None, account_name: Optional[str] = None):
        """
        Get account state for a specific date (or latest if None).
        """
        account_name = account_name or self.account_name
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if date is None:
            # Get latest date for this account
            cursor.execute('SELECT date FROM balance_history WHERE account_name = ? ORDER BY date DESC LIMIT 1', (account_name,))
            res = cursor.fetchone()
            if not res:
                return None
            date = res[0]
            
        # Get Balance
        cursor.execute('SELECT cash, total_assets FROM balance_history WHERE date = ? AND account_name = ?', (date, account_name))
        bal_res = cursor.fetchone()
        if not bal_res:
            return None
            
        cash, total_assets = bal_res
        
        # Get Positions
        cursor.execute('SELECT symbol, amount, price, cost_basis FROM position_history WHERE date = ? AND account_name = ?', (date, account_name))
        pos_rows = cursor.fetchall()
        
        positions = {}
        for sym, amt, prc, cost in pos_rows:
            positions[sym] = {
                'total_amount': amt,
                'amount': amt, # Alias
                'price': prc,
                'cost_basis': cost
            }
            
        conn.close()
        
        return {
            'date': date,
            'account_name': account_name,
            'cash': cash,
            'total_assets': total_assets,
            'positions': positions
        }

    def get_latest_date(self, account_name: Optional[str] = None, before_date: Optional[str] = None):
        account_name = account_name or self.account_name
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        if before_date:
            cursor.execute('SELECT max(date) FROM balance_history WHERE account_name = ? AND date < ?', (account_name, before_date))
        else:
            cursor.execute('SELECT max(date) FROM balance_history WHERE account_name = ?', (account_name,))
        res = cursor.fetchone()
        conn.close()
        return res[0] if res else None

    def record_trade(
        self,
        *,
        date: str,
        symbol: str,
        side: str,
        amount: int,
        price: float,
        fee: float = 0.0,
        tax: float = 0.0,
        total_cost: Optional[float] = None,
        order_id: str = "",
        account_name: Optional[str] = None,
    ):
        account_name = account_name or self.account_name
        amount = int(amount)
        if amount <= 0:
            return
        price = float(price)
        fee = float(fee)
        tax = float(tax)
        if total_cost is None:
            gross = price * amount
            total_cost = gross + fee + tax if side == "buy" else gross - fee - tax

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO trade_log (date, account_name, symbol, side, amount, price, fee, tax, total_cost, order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (date, account_name, symbol, side, amount, price, fee, tax, float(total_cost), order_id),
        )
        conn.commit()
        conn.close()

    def clear_trade_log(self, *, date: str, account_name: Optional[str] = None):
        account_name = account_name or self.account_name
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM trade_log WHERE date = ? AND account_name = ?', (date, account_name))
        conn.commit()
        conn.close()

    def get_trade_log(self, *, date: Optional[str] = None, account_name: Optional[str] = None) -> pd.DataFrame:
        account_name = account_name or self.account_name
        conn = sqlite3.connect(self.db_path)
        query = 'SELECT date, account_name, symbol, side, amount, price, fee, tax, total_cost, order_id FROM trade_log WHERE account_name = ?'
        params = [account_name]
        if date is not None:
            query += ' AND date = ?'
            params.append(date)
        query += ' ORDER BY date, id'
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        return df
