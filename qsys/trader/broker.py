import pandas as pd
from qsys.utils.logger import log

class BrokerAdapter:
    def __init__(self, broker_name="generic"):
        self.broker_name = broker_name

    def parse_positions(self, file_path):
        """
        Parse broker export position file.
        Expected standard columns after mapping: 'symbol', 'total_amount', 'sellable_amount', 'avg_cost', 'market_value'
        Returns: Dict[symbol, PositionDict]
        """
        try:
            df = self._read_file(file_path)
            df = self._standardize_columns(df, type="position")
            df = self._clean_data(df)
            
            positions = {}
            for _, row in df.iterrows():
                symbol = self._normalize_symbol(row['symbol'])
                positions[symbol] = {
                    'symbol': symbol,
                    'total_amount': int(row.get('total_amount', 0)),
                    'sellable_amount': int(row.get('sellable_amount', 0)),
                    'avg_cost': float(row.get('avg_cost', 0.0)),
                    'market_value': float(row.get('market_value', 0.0))
                }
            return positions
        except Exception as e:
            log.error(f"Failed to parse position file {file_path}: {e}")
            raise e

    def parse_orders(self, file_path):
        """
        Parse broker export order/deal file.
        Expected standard columns: 'date', 'time', 'symbol', 'side', 'price', 'amount', 'fee'
        """
        try:
            df = self._read_file(file_path)
            df = self._standardize_columns(df, type="order")
            df = self._clean_data(df)
            
            orders = []
            for _, row in df.iterrows():
                orders.append({
                    'date': str(row.get('date', '')),
                    'time': str(row.get('time', '')),
                    'symbol': self._normalize_symbol(row['symbol']),
                    'side': self._normalize_side(row['side']),
                    'price': float(row.get('price', 0.0)),
                    'amount': int(row.get('amount', 0)),
                    'fee': float(row.get('fee', 0.0))
                })
            return orders
        except Exception as e:
            log.error(f"Failed to parse order file {file_path}: {e}")
            raise e

    def _read_file(self, file_path):
        if str(file_path).endswith('.csv'):
            try:
                return pd.read_csv(file_path, encoding='utf-8')
            except:
                return pd.read_csv(file_path, encoding='gbk')
        elif str(file_path).endswith('.xls') or str(file_path).endswith('.xlsx'):
            return pd.read_excel(file_path)
        else:
            raise ValueError("Unsupported file format")

    def _standardize_columns(self, df, type="position"):
        # Mapping for generic/dfcf/htsc
        # Extend this map for different brokers
        col_map = {
            # Position
            '证券代码': 'symbol', '代码': 'symbol',
            '证券名称': 'name', '名称': 'name',
            '股票余额': 'total_amount', '持仓数量': 'total_amount',
            '可用余额': 'sellable_amount', '可用数量': 'sellable_amount',
            '成本价': 'avg_cost', '持仓成本': 'avg_cost',
            '市值': 'market_value', '最新市值': 'market_value',
            
            # Order
            '成交日期': 'date', '发生日期': 'date',
            '成交时间': 'time',
            '操作': 'side', '买卖标志': 'side',
            '成交均价': 'price', '成交价格': 'price',
            '成交数量': 'amount', '发生数量': 'amount',
            '发生金额': 'turnover',
            '手续费': 'fee', '佣金': 'fee', '印花税': 'tax'
        }
        
        df = df.rename(columns=col_map)
        return df

    def _clean_data(self, df):
        # Remove empty rows
        df = df.dropna(subset=['symbol'])
        # Remove non-stock rows (e.g. repo) if needed
        return df

    def _normalize_symbol(self, code):
        code = str(code).strip()
        if code.isdigit():
            # Simple heuristic for SH/SZ
            if code.startswith('6') or code.startswith('9') or code.startswith('5'): # 5/9 for ETF/funds in SH
                return f"{code}.SH"
            else:
                return f"{code}.SZ"
        return code

    def _normalize_side(self, side):
        side = str(side).strip()
        if side in ['证券买入', '买入', 'Buy']:
            return 'buy'
        elif side in ['证券卖出', '卖出', 'Sell']:
            return 'sell'
        return 'unknown'
